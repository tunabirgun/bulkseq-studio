from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflow" / "scripts"))

_MRS = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_run_summary.py"
_MTS = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_timing_summary.py"
_LIMMA = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "run_limma.R"


@pytest.fixture(scope="module")
def mrs():
    spec = importlib.util.spec_from_file_location("make_run_summary", _MRS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mts():
    spec = importlib.util.spec_from_file_location("make_timing_summary", _MTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("step", ["deseq2", "limma_microarray", "edger"])
def test_timing_phase_is_route_neutral_for_all_de_engines(mts, step) -> None:
    assert mts.phase_for(step) == "Differential expression"


def test_timing_window_includes_the_first_jobs_runtime(mts) -> None:
    steps = [{"seconds": 10.0}, {"seconds": 5.0}]
    # Jobs finished at t=110 and t=120, so the first began at t=100.
    assert mts.analysis_job_window([110.0, 120.0], steps) == 20.0


def test_timing_scope_excludes_stale_report_assembly_benchmarks(mts, tmp_path) -> None:
    for name in ("limma_microarray", "enrichment_figures", "final_reports", "html_report"):
        (tmp_path / f"{name}.tsv").write_text("s\n1.0\n", encoding="utf-8")

    paths = mts.analysis_benchmark_paths(tmp_path)

    assert [path.stem for path in paths] == ["enrichment_figures", "limma_microarray"]
    assert mts.REPORT_ASSEMBLY_STEPS == {"final_reports", "html_report"}


def test_microarray_export_records_moderated_lfc_standard_error() -> None:
    script = _LIMMA.read_text(encoding="utf-8")
    assert 'adjust.method = "BH"' in script
    de_common = _LIMMA.with_name("de_common.R").read_text(encoding="utf-8")
    assert "fit$stdev.unscaled[, 1] * sqrt(fit$s2.post)" in de_common
    assert "lfc_se <- moderated_lfc_se(fit2, rownames(tt))" in script
    assert "lfcSE = lfc_se" in script
    assert "lfcSE = NA_real_" not in script


def test_microarray_customizations_exclude_inactive_shrinkage_and_use_route_labels(mrs) -> None:
    payload = {
        "input": {"type": "microarray"},
        "customized_parameters": {
            "deseq2.design_formula": {"default": "~ condition", "used": "~ batch + condition"},
            "deseq2.reference_level.condition": {"default": "control", "used": "wild_type"},
            "deseq2.lfc_shrinkage": {"default": True, "used": False},
            "deseq2.shrinkage_method": {"default": "apeglm", "used": "ashr"},
        },
    }
    active = mrs.microarray_active_customizations(payload)
    assert "deseq2.design_formula" in active
    assert "deseq2.reference_level.condition" in active
    assert "deseq2.lfc_shrinkage" not in active
    assert "deseq2.shrinkage_method" not in active
    assert mrs._display_customization_key("deseq2.design_formula", "microarray") == \
        "analysis.design_formula"
    assert mrs._display_customization_key(
        "deseq2.reference_level.condition", "microarray") == \
        "analysis.reference_level.condition"


def test_design_fields_render_as_readable_scientific_labels(mrs) -> None:
    assert mrs._format_reference_level({"condition": "wild_type"}) == "condition=wild_type"
    assert mrs._format_contrasts([{
        "name": "hub2_3_vs_wild_type",
        "factor": "condition",
        "numerator": "hub2_3",
        "denominator": "wild_type",
    }]) == "hub2_3 vs wild_type (factor: condition) [hub2_3_vs_wild_type]"


# ---- select_tools: trimmer / rRNA filter / contamination screen gating -----------------


def test_select_tools_defaults_to_fastp(mrs) -> None:
    tools = mrs.select_tools({"workflow": {}})
    assert tools["fastp"] == ["fastp", "--version"]
    assert "trim_galore" not in tools
    assert "trimmomatic" not in tools


def test_select_tools_probes_trim_galore_when_configured(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"trimmer": "trim-galore"}})
    assert tools["trim_galore"] == ["trim_galore", "--version"]
    assert "fastp" not in tools


def test_select_tools_probes_trimmomatic_when_configured(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"trimmer": "trimmomatic"}})
    assert tools["trimmomatic"][0] == "trimmomatic"
    assert "fastp" not in tools


def test_select_tools_omits_every_trimmer_when_trimming_is_off(mrs) -> None:
    # `trimming: false` runs no trimmer at all; probing one would record a version for a
    # tool that never executed.
    tools = mrs.select_tools({"workflow": {"trimming": False, "trimmer": "trim-galore"}})
    assert "fastp" not in tools
    assert "trim_galore" not in tools
    assert "trimmomatic" not in tools


def test_select_tools_omits_rrna_tool_when_filtering_off(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"rrna_filtering": False, "rrna_tool": "ribodetector"}})
    assert "ribodetector" not in tools
    assert "sortmerna" not in tools


def test_select_tools_probes_sortmerna_when_rrna_filtering_on(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"rrna_filtering": True}})
    assert tools["sortmerna"] == ["sortmerna", "--version"]


def test_select_tools_probes_ribodetector_when_configured(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"rrna_filtering": True, "rrna_tool": "ribodetector"}})
    assert tools["ribodetector"] == ["ribodetector_cpu", "--version"]
    assert "sortmerna" not in tools


def test_select_tools_omits_fastq_screen_by_default(mrs) -> None:
    assert "fastq_screen" not in mrs.select_tools({"workflow": {}})


def test_select_tools_probes_fastq_screen_when_contamination_screen_on(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"contamination_screen": True},
                              "contamination": {"conf": "/path/to/fastq_screen.conf"}})
    assert tools["fastq_screen"] == ["fastq_screen", "--version"]


def test_select_tools_omits_fastq_screen_without_a_config_path(mrs) -> None:
    # The Snakefile also requires a FastQ Screen config; without one the rule is skipped
    # with a warning, so recording a version would claim a tool ran that did not.
    tools = mrs.select_tools({"workflow": {"contamination_screen": True}})
    assert "fastq_screen" not in tools


@pytest.mark.parametrize("input_type", ["count_matrix", "microarray", "deseq2_results"])
def test_select_tools_omits_read_processing_tools_for_non_fastq_input(mrs, input_type) -> None:
    # These modes never touch reads, so the Snakefile forces TRIMMING/RRNA_FILTER off no
    # matter what the switches say — and they keep their FASTQ-run defaults in a converted
    # project.
    tools = mrs.select_tools({
        "input": {"type": input_type},
        "workflow": {"trimming": True, "rrna_filtering": True, "contamination_screen": True},
        "contamination": {"conf": "/x.conf"},
    })
    for absent in ("fastp", "trim_galore", "trimmomatic", "sortmerna", "ribodetector", "fastq_screen"):
        assert absent not in tools, f"{absent} probed for a {input_type} run"


def test_de_engine_label_names_the_engine_that_ran(mrs) -> None:
    # Only DESeq2 shrinks; naming DESeq2/apeglm for a voom or edgeR run reports an engine
    # and a method that never executed.
    assert mrs.de_engine_label({"workflow": {"de_engine": "deseq2"}}, False, None, "apeglm") == \
        "DESeq2, shrinkage: apeglm"
    assert mrs.de_engine_label({"workflow": {"de_engine": "deseq2"}}, False, "ashr", "apeglm") == \
        "DESeq2, shrinkage: ashr"
    assert mrs.de_engine_label({"workflow": {"de_engine": "limma-voom"}}, False, None, "apeglm") == \
        "limma-voom (no LFC shrinkage)"
    assert mrs.de_engine_label({"workflow": {"de_engine": "edger"}}, False, None, "apeglm") == \
        "edgeR (no LFC shrinkage)"
    assert mrs.de_engine_label({"workflow": {}}, True, None, "apeglm") == "limma (no LFC shrinkage)"
    assert mrs.de_engine_label({"input": {"type": "deseq2_results"}, "workflow": {}}, False, None, "apeglm") == \
        "externally supplied DE results (no local DE model or LFC shrinkage)"


def test_select_tools_includes_fastq_route_tools(mrs) -> None:
    tools = mrs.select_tools({"workflow": {}})
    for name in ("snakemake", "python", "fastqc", "multiqc", "STAR", "Rscript"):
        assert name in tools


@pytest.mark.parametrize("input_type", ["microarray", "count_matrix", "deseq2_results"])
def test_select_tools_non_read_routes_exclude_alignment_environment(mrs, input_type) -> None:
    tools = mrs.select_tools({
        "input": {"type": input_type},
        "workflow": {"aligner": "STAR", "quantifier": "featureCounts"},
    })
    assert set(tools) == {"snakemake", "python", "Rscript"}


# ---- sessionInfo.txt parsing: realised shrinkage method + BLAS/LAPACK platform line -----


def test_parse_session_info_extracts_shrinkage_line(mrs) -> None:
    text = (
        "Shrinkage method used: ashr (requested 'apeglm' failed and fell back: some reason)\n"
        "\n"
        "R version 4.5.2 (2025-10-31)\n"
        "Platform: x86_64-w64-mingw32/x64 (64-bit)\n"
    )
    info = mrs.parse_session_info(text)
    assert info["shrinkage_used"] == "ashr (requested 'apeglm' failed and fell back: some reason)"
    assert info["r_platform"] == "x86_64-w64-mingw32/x64 (64-bit)"


def test_parse_session_info_extracts_blas_lapack(mrs) -> None:
    text = (
        "Shrinkage method used: apeglm (as requested)\n\n"
        "Matrix products: default\n"
        "BLAS:   C:/conda/envs/bulkseq/Lib/R/lib/libRblas.dll\n"
        "LAPACK: C:/conda/envs/bulkseq/Lib/R/lib/libRlapack.dll\n"
    )
    info = mrs.parse_session_info(text)
    assert "libRblas.dll" in info["blas"]
    assert "libRlapack.dll" in info["lapack"]


def test_parse_session_info_empty_text_returns_empty_dict(mrs) -> None:
    assert mrs.parse_session_info("") == {}


def test_platform_provenance_reports_os_arch_and_r_line(mrs) -> None:
    session_info = {"r_platform": "x86_64-w64-mingw32/x64 (64-bit)",
                     "blas": "libRblas.dll", "lapack": "libRlapack.dll"}
    plat = mrs.platform_provenance(session_info)
    assert plat["os"]
    assert plat["arch"]
    assert plat["r_platform"] == "x86_64-w64-mingw32/x64 (64-bit)"
    assert plat["r_blas"] == "libRblas.dll"
    assert plat["r_lapack"] == "libRlapack.dll"


def test_platform_provenance_r_fields_none_when_no_session_info(mrs) -> None:
    plat = mrs.platform_provenance({})
    assert plat["r_platform"] is None
    assert plat["r_blas"] is None


# ---- render_text: realised shrinkage method overrides the configured one ----------------


def _base_payload(**overrides) -> dict:
    payload = {
        "project": {"name": "proj", "working_directory": "."},
        "run_date": "2026-01-01T00:00:00",
        "app_version": "1.0", "workflow_version": "1.0",
        "workflow_git_commit": None, "environment_lock_md5": None, "snakemake_version": "8.0",
        "input": {"type": "fastq"},
        "reference": {"organism_name": "human", "strain": None, "source": "ensembl", "release": "110",
                      "genome_md5": "x", "annotation_md5": "y"},
        "deseq2": {"shrinkage_method": "apeglm", "design_formula": "~condition",
                   "reference_level": "ctrl", "contrasts": [], "alpha": 0.05, "lfc_threshold": 1},
        "workflow": {},
        "customized_parameters": {},
        "warnings": [],
        "download_integrity": {},
        "output_paths": [],
        "platform": {"os": "Windows", "os_release": "11", "arch": "AMD64",
                     "r_platform": None, "r_blas": None, "r_lapack": None},
        "software_versions": {},
        "session_info": {},
    }
    payload.update(overrides)
    return payload


def test_render_text_reports_configured_method_when_no_fallback_recorded(mrs) -> None:
    text = mrs.render_text(_base_payload())
    assert "Method: DESeq2, shrinkage: apeglm" in text


def test_render_text_reports_realised_fallback_method(mrs) -> None:
    payload = _base_payload(session_info={
        "shrinkage_used": "ashr (requested 'apeglm' failed and fell back: coefficient not found)"
    })
    text = mrs.render_text(payload)
    assert "shrinkage: ashr (requested 'apeglm' failed and fell back" in text
    assert "shrinkage: apeglm" not in text


def test_render_text_includes_platform_section(mrs) -> None:
    text = mrs.render_text(_base_payload())
    assert "Platform" in text
    assert "OS: Windows 11" in text
    assert "Arch: AMD64" in text


def test_run_summary_loads_and_renders_realized_reference_integrity(mrs, tmp_path) -> None:
    lock = {
        "schema_version": 1,
        "status": "PASS",
        "genome": {
            "integrity": {
                "source_md5": "1" * 32,
                "configured_md5": "1" * 32,
                "md5_status": "VERIFIED",
                "source_bytes": 120,
                "canonical_sha256": "a" * 64,
                "canonical_bytes": 100,
            },
            "content": {"record_count": 2, "total_bases": 80},
        },
        "annotation": {
            "integrity": {
                "source_md5": "2" * 32,
                "configured_md5": None,
                "md5_status": "NOT_CONFIGURED",
                "source_bytes": 90,
                "canonical_sha256": "b" * 64,
                "canonical_bytes": 200,
            },
            "content": {"evidence_counts": {"gene": 3, "exon": 4, "CDS": 5}},
        },
        "contig_compatibility": {
            "overlap_contigs": 2,
            "annotation_contigs": 2,
            "annotation_feature_rows": 120,
            "compatible_feature_rows": 119,
            "feature_row_overlap_fraction": 119 / 120,
            "minimum_feature_row_overlap_fraction": 0.95,
        },
        "counting_contract": {
            "feature_types": ["exon"],
            "attribute_type": "gene_id",
            "feature_rows": 100,
            "feature_rows_missing_attribute": 0,
        },
    }
    lock_path = tmp_path / "references" / "reference.lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    realized = mrs.reference_integrity(tmp_path)
    payload = _base_payload(reference_integrity=realized)
    rendered = mrs.render_text(payload)
    tools = mrs.render_tools_references(payload)

    assert realized["lock_path"] == "references/reference.lock.json"
    for text in (rendered, tools):
        assert f"Genome canonical SHA-256: {'a' * 64}" in text
        assert f"Annotation canonical SHA-256: {'b' * 64}" in text
        assert "Genome source MD5: " + "1" * 32 + " (VERIFIED" in text
        assert "Annotation source MD5: " + "2" * 32 + " (NOT_CONFIGURED" in text
        assert "Annotation features (gene/exon/CDS): 3/4/5" in text
        assert "Compatible contigs (overlap/annotation): 2/2" in text
        assert "Configured counting contract: feature_type=exon; attribute_type=gene_id" in text
        assert "Compatible annotation feature rows: 119/120 (99.17%; required >= 95%)" in text


def _external_payload(**input_overrides) -> dict:
    external_input = {
        "type": "deseq2_results",
        "samples": "metadata/cohort.tsv",
        "deseq2_results": "config/deseq2_results.csv",
        "deseq2_results_direction": {
            "numerator": "stimulated", "denominator": "baseline", "confirmed": True,
            "confirmed_at": "2026-08-10T12:00:00+03:00",
        },
        "deseq2_results_provenance": {
            "original_basename": "upstream_results.tsv",
            "imported_at": "2026-08-10T11:59:00+03:00",
            "project_copy": "config/deseq2_results.csv",
            "sha256": "a" * 64,
            "byte_size": 321,
            "row_count": 2,
            "column_names": ["gene_id", "log2FoldChange", "padj"],
            "gene_id_column": "gene_id",
            "log2fc_column": "log2FoldChange",
            "adjusted_p_column": "padj",
            "upstream_method": "limma",
            "lfc_shrinkage": "not_applied",
            "p_adjustment_method": "Holm",
        },
    }
    external_input.update(input_overrides)
    return _base_payload(
        input=external_input,
        # These stale local-route settings must not be presented as what the table means.
        deseq2={"shrinkage_method": "apeglm", "design_formula": "~ stale",
                "reference_level": "stale",
                "contrasts": [{"numerator": "wrong", "denominator": "wrong"}],
                "alpha": 0.05, "lfc_threshold": 1},
        workflow={"aligner": "STAR", "quantifier": "featureCounts", "de_engine": "DESeq2",
                  "enrichment": True, "figures": True},
        customized_parameters={
            "workflow.de_engine": {"default": "DESeq2", "used": "DESeq2"},
            "deseq2.alpha": {"default": 0.05, "used": 0.01},
        },
        software_versions={"snakemake": "8", "python": "3.12", "STAR": "2.7"},
        r_packages={"DESeq2": "1.0", "apeglm": "1.0", "clusterProfiler": "4.0"},
    )


def test_uploaded_results_reports_full_provenance_without_local_model_claims(mrs) -> None:
    payload = _external_payload()
    reports = (
        mrs.render_text(payload),
        mrs.render_tools_references(payload),
        mrs.render_study_design(payload, "sample_id\tcondition\n", "metadata/cohort.tsv"),
    )
    for text in reports:
        assert "Original basename: upstream_results.tsv" in text
        assert "Project copy: config/deseq2_results.csv" in text
        assert f"Project-copy SHA-256: {'a' * 64}" in text
        assert "Project-copy byte size: 321" in text
        assert "Imported rows: 2" in text
        assert "Imported columns: gene_id, log2FoldChange, padj" in text
        assert "gene ID=gene_id; log2 fold change=log2FoldChange; adjusted p-value=padj" in text
        assert "Upstream differential-expression method: limma" in text
        assert "Upstream LFC shrinkage: not_applied" in text
        assert "Upstream p-adjustment method: Holm" in text
        assert ("positive log2FC = higher in stimulated (numerator) than baseline "
                "(denominator)") in text
        assert "Source table:" not in text
        assert "Design formula: ~ stale" not in text
        assert "Method: DESeq2" not in text
        assert "Alpha (FDR)" not in text
        assert "Benjamini" not in text
        assert "treated" not in text.casefold()
        assert "control" not in text.casefold()
    assert "no local DE model or LFC shrinkage" in mrs.de_engine_label(payload, False, None, "apeglm")


def test_uploaded_results_unknown_adjustment_stays_generic(mrs) -> None:
    payload = _external_payload()
    payload["input"]["deseq2_results_provenance"]["upstream_method"] = "unknown"
    payload["input"]["deseq2_results_provenance"]["lfc_shrinkage"] = "unknown"
    payload["input"]["deseq2_results_provenance"]["p_adjustment_method"] = "unknown"
    text = mrs.render_text(payload)
    assert "Supplied-result thresholds: adjusted p-value < 0.05" in text
    assert "Upstream p-adjustment method: unknown" in text
    assert "FDR" not in text
    assert "Benjamini" not in text


def test_external_json_payload_omits_inactive_local_route_settings(mrs) -> None:
    payload = _external_payload()
    payload.update({
        "fastp": {"trim_poly_g": True},
        "sortmerna": {"database": "stale"},
        "star": {"twopass_mode": True},
        "featurecounts": {"strandedness": 2},
        "session_info": {"shrinkage_used": "apeglm", "r_platform": "test"},
        "download_integrity": {"total": 2, "verified": 2},
        "output_paths": [
            "results/counts/counts.txt", "results/figures/pca.png",
            "results/deseq2/deseq2_results.csv", "results/figures/volcano.png",
        ],
    })
    normalized = mrs.normalize_external_report_payload(payload)
    assert normalized["deseq2"] == {"alpha": 0.05, "lfc_threshold": 1}
    assert normalized["workflow"] == {"enrichment": True, "figures": True}
    assert normalized["software_versions"] == {"snakemake": "8", "python": "3.12"}
    assert normalized["r_packages"] == {"clusterProfiler": "4.0"}
    assert normalized["session_info"] == {"r_platform": "test"}
    assert normalized["download_integrity"] == {}
    assert normalized["output_paths"] == [
        "results/deseq2/deseq2_results.csv", "results/figures/volcano.png"]
    for inactive in ("fastp", "sortmerna", "star", "featurecounts"):
        assert inactive not in normalized


def test_configured_samples_path_and_study_label_use_input_samples(mrs, tmp_path) -> None:
    payload = _base_payload(input={"type": "fastq", "samples": "metadata/cohort.tsv"})
    path, label = mrs.configured_samples_path(tmp_path, payload)
    assert path == tmp_path / "metadata" / "cohort.tsv"
    assert label == "metadata/cohort.tsv"
    study = mrs.render_study_design(
        payload, "sample_id\tcondition\ns1\tbaseline\n", label)
    assert "Samples (metadata/cohort.tsv)" in study
    assert "Samples (config/samples.tsv)" not in study


def _microarray_payload() -> dict:
    return _base_payload(
        input={"type": "microarray"},
        microarray={
            "gse_accession": "GSE30735", "platform": "GPL198",
            "source": "geo_series_matrix", "normalization": "auto",
            "log2_transform": "auto",
        },
        reference={"organism_name": "Arabidopsis thaliana"},
        enrichment={"orgdb": "org.At.tair.db", "keytype": "SYMBOL", "kegg_organism": "ath"},
        ppi={"enabled": True, "taxon": 3702},
        workflow={
            "fastqc_pre_trim": True, "trimming": True, "trimmer": "fastp",
            "aligner": "STAR", "quantifier": "featureCounts", "de_engine": "DESeq2",
            "enrichment": True, "figures": True, "gsva": False,
        },
        customized_parameters={
            "input.type": {"default": "fastq", "used": "microarray"},
            "workflow.aligner": {"default": "STAR", "used": "HISAT2"},
            "microarray.gse_accession": {"default": None, "used": "GSE30735"},
        },
        software_versions={
            "snakemake": "9", "python": "3.12", "Rscript": "4.5",
            "fastqc": "0.12", "multiqc": "1.30", "STAR": "2.7",
            "featureCounts": "2.1",
        },
        r_packages={
            "limma": "3.66", "clusterProfiler": "4.18", "ggplot2": "4.0",
            "DESeq2": "1.50", "apeglm": "1.32", "tximport": "1.38",
            "org.At.tair.db": "3.22",
        },
    )


def test_microarray_summary_is_route_correct_and_excludes_rna_read_claims(mrs) -> None:
    payload = mrs.normalize_microarray_report_payload(_microarray_payload())
    rendered = mrs.render_text(payload)
    tools = mrs.render_tools_references(payload)

    assert rendered.startswith("Microarray Analysis Run Summary\n")
    assert "Active microarray modules" in rendered
    assert '"enrichment": true' in rendered
    assert "GEO series matrix (microarray); differential expression: limma" in tools
    assert "limma: 3.66" in tools
    for wrong in (
        "RNA-seq Analysis Run Summary", "STAR", "featureCounts", "fastqc",
        "multiqc", "trimming", "read processing, mapping",
    ):
        assert wrong not in rendered
        assert wrong not in tools
    assert payload["workflow"] == {
        "enrichment": True, "figures": True, "gsva": False,
        "protein_interaction_network": True,
    }
    assert payload["deseq2"] == {
        "design_formula": "~condition",
        "reference_level": "ctrl",
        "contrasts": [],
        "alpha": 0.05,
        "lfc_threshold": 1,
    }
    assert payload["analysis_method"] == {
        "engine": "limma",
        "empirical_bayes": {"trend": True, "robust": True},
        "p_adjustment_method": "Benjamini-Hochberg",
        "lfc_shrinkage": False,
        "effect_size_uncertainty": "moderated log2-fold-change standard error",
    }
    assert "shrinkage_method" not in payload["deseq2"]
    assert "min_count" not in payload["deseq2"]
    assert payload["software_versions"] == {
        "snakemake": "9", "python": "3.12", "Rscript": "4.5"}
    assert "DESeq2" not in payload["r_packages"]
    assert payload["environment_r_packages"]["DESeq2"] == "1.50"


def test_enrichment_mapping_evidence_is_preserved_in_reports(mrs, tmp_path) -> None:
    summary = tmp_path / "results" / "enrichment" / "enrichment_summary.txt"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        "Functional enrichment summary\n"
        "Eligible ID mapping keytypes: SYMBOL, TAIR, ENTREZID, ALIAS\n"
        "Identifier routing policy: AGI locus IDs -> TAIR; all other IDs -> configured SYMBOL.\n"
        "Tested input IDs retained after mapping/exclusion: 20760/22180 (93.6%)\n"
        "Significant input IDs retained after mapping/exclusion: 147/154 (95.5%)\n"
        "Mapped tested-gene universe (unique Entrez IDs): 20629\n"
        "GO effective annotated ORA universes: BP 15910/20629 (77.1%; LIMITED_ANNOTATION); MF 20200/20629 (97.9%; PASS); CC 20500/20629 (99.4%; PASS)\n"
        "KEGG identity verification: PASS; configured code=ath; registry code=ath; organism=Arabidopsis thaliana; taxon=3702; expected organism=Arabidopsis thaliana; expected taxon=3702\n"
        "KEGG retrieval: SUCCESS; pathway collection=162; detail=none\n"
        "KEGG effective resource universe: 4748/20629 (23.0%); eligible 10-500 pathway universe=4687\n"
        "KEGG supported foreground: up 14/79 (17.7%); down 14/64 (21.9%); combined 28/143 (19.6%)\n"
        "KEGG eligible hypotheses/gene sets: 132 after 10-500 filter; foreground-overlapping ORA hypotheses adjusted=32\n"
        "KEGG adjusted results: ORA=0; GSEA=0; BH pvalueCutoff=0.05; qvalueCutoff=0.20\n"
        "KEGG resource status: LIMITED_ANNOTATION; no supported KEGG pathways met the adjusted criterion; this is not evidence that no pathway biology is present\n"
        "Ambiguous input IDs excluded: 14 (routed one-to-many: 8; unresolved cross-keytype: 6)\n"
        "Direction-conflict Entrez IDs excluded: 1; input IDs excluded: 2\n"
        "Foreground intersection (up/down Entrez) after exclusion: 0\n"
        "Mapping interpretation gate: PASS (WARNING below 80%; REVIEW_REQUIRED below 50%)\n"
        "Direction-conflict gate: REVIEW_REQUIRED (any conflict requires review)\n"
        "GO/DO annotation-resource status: LIMITED_ANNOTATION (coverage below 80% is LIMITED_ANNOTATION; zero or malformed resource universes are NOT_INTERPRETABLE; this is separate from global ID mapping)\n"
        "Universe policy: all and only unambiguously mapped, direction-conflict-free tested Entrez genes.\n"
        "ORA parameters: Benjamini-Hochberg (BH); pvalueCutoff=0.05; explicit tested-gene universe.\n"
        "ORA multiple-testing families: up, down, and combined queries are BH-corrected separately.\n"
        "Mapping limitation: enrichment tests only the mapped subset.\n",
        encoding="utf-8",
    )
    evidence = mrs.enrichment_mapping_evidence(tmp_path)
    payload = _microarray_payload()
    payload["enrichment_mapping"] = evidence
    for report in (mrs.render_text(payload), mrs.render_tools_references(payload)):
        assert "20760/22180 (93.6%)" in report
        assert "147/154 (95.5%)" in report
        assert "20629" in report
        assert "GO effective annotated ORA universes: BP 15910/20629" in report
        assert "Ambiguous input IDs excluded: 14" in report
        assert "Foreground intersection (up/down Entrez) after exclusion: 0" in report
        assert "Benjamini-Hochberg (BH)" in report
        assert "BH-corrected separately" in report
        assert "KEGG identity verification: PASS" in report
        assert "KEGG effective resource universe: 4748/20629 (23.0%)" in report
        assert "KEGG resource status: LIMITED_ANNOTATION" in report
        assert "no supported KEGG pathways met the adjusted criterion" in report
        assert "GO/DO annotation-resource status: LIMITED_ANNOTATION" in report
        assert "incomplete" not in report  # exact sidecar wording is rendered, never invented


def test_enrichment_script_has_mixed_id_fallback_na_filter_and_fail_closed_gate() -> None:
    script = (Path(__file__).resolve().parents[1] / "workflow" / "scripts" /
              "run_enrichment.R").read_text(encoding="utf-8")
    assert "map_ids_with_routing <- function" in script
    assert 'eligible <- unique(c(effective_keytype, configured_keytype, "TAIR", "ENSEMBL",' in script
    assert 'reason = "routed_one_to_many"' in script
    assert 'reason = "unresolved_cross_keytype"' in script
    assert "collapse_entrez_results <- function" in script
    assert "stats::median(values)" in script
    assert 'res$direction == "up"' in script and 'res$direction == "down"' in script
    assert "stopifnot(length(intersect(up_e, down_e)) == 0L)" in script
    assert "direction_gate <- function" in script
    assert "unique(mapped[!is.na(mapped) & nzchar(mapped)])" in script
    assert "MAPPING_WARNING_FRACTION <- 0.80" in script
    assert "MAPPING_REVIEW_FRACTION <- 0.50" in script
    assert "ANNOTATION_WARNING_FRACTION <- 0.80" in script
    assert "annotation_resource_gate <- function" in script
    assert "go_annotation_status <- function" in script
    assert "go_readable_for_orgdb <- function" in script
    assert "readable = go_readable" in script
    assert 'return("LIMITED_ANNOTATION")' in script
    assert "validate_kegg_identity <- function" in script
    assert "assess_kegg_resource <- function" in script
    assert 'return("REVIEW_REQUIRED")' not in script.split(
        "annotation_resource_gate <- function", 1)[1].split("}", 1)[0]
    assert "Mapping limitation: enrichment tests only the retained mapped subset" in script
    assert 'pAdjustMethod = "BH"' in script
    assert "universe = universe" in script
    assert "kegg_args$universe <- background" in script
    assert "Universe policy: all and only unambiguously mapped" in script
    assert "their term counts must not be summed" in script
    assert "GO effective annotated ORA universes:" in script
    assert "GO/DO annotation-resource status:" in script
    assert "KEGG resource status:" in script
    assert "no supported KEGG pathways met the adjusted criterion" in script
    rule = (Path(__file__).resolve().parents[1] / "workflow" / "rules" /
            "enrichment.smk").read_text(encoding="utf-8")
    assert 'organism_name=config.get("reference", {}).get("organism_name") or ""' in rule
    assert "smallest Entrez" not in script


def test_microarray_normalization_records_partial_missingness() -> None:
    script = (Path(__file__).resolve().parents[1] / "workflow" / "scripts" /
              "ingest_geo.R").read_text(encoding="utf-8")
    assert "n_missing_cells <- sum(is.na(gene_mat))" in script
    assert "n_genes_with_missing <- sum(rowSums(is.na(gene_mat)) > 0L)" in script
    assert '"missing_cells": %d' in script
    assert '"genes_with_missing": %d' in script
    assert '"genes_dropped_over_half_missing": %d' in script
    assert "Post-collapse missing intensities:" in script
    assert "first DE-table occurrence retained" not in script
    assert "unique(res$ENTREZID[match(base, res$base_id)])" not in script


def test_wilcoxon_axis_is_method_neutral() -> None:
    script = (Path(__file__).resolve().parents[1] / "workflow" / "scripts" /
              "run_wilcoxon.R").read_text(encoding="utf-8")
    assert 'labs(x = "Primary differential-expression statistic"' in script
    assert "DESeq2 / limma statistic" not in script


def _ppi_sidecar() -> dict:
    return {
        "schema_version": 1,
        "status": "PASS",
        "reason": None,
        "generated_at_utc": "2026-08-11T01:02:03Z",
        "database": {
            "name": "STRING", "configured_version": "12.0",
            "realized_version": "12.0", "realized_build": "stable-build-abc",
            "taxon": 3702, "query_date_utc": "2026-08-11",
        },
        "software": {
            "R": "4.5.2", "STRINGdb": "2.22.0", "igraph": "2.2.1",
            "ggplot2": "4.0.3",
        },
        "configuration": {
            "seed_source": "de", "max_seed_genes": 200,
            "score_threshold_combined": 0,
            "string_combined_score_scale": "0-1000",
            "stored_edge_weight": "combined_score / 1000",
        },
        "realized": {
            "seed_source": "differential_expression_symbols", "seed_input_count": 154,
            "seed_up_count": 90, "seed_down_count": 64,
            "seed_after_limit_count": 154, "mapped_seed_count": 149,
            "mapped_string_id_count": 148, "interactions_returned_count": 311,
            "interactions_passing_threshold_count": 205,
            "score_threshold_combined": 400, "minimum_combined_score": 401,
            "maximum_combined_score": 999, "node_count": 101, "edge_count": 205,
            "module_count": 7,
            "layout_method": "igraph::layout_with_fr", "layout_fallback_reason": None,
            "figure_width_in": 8.1, "figure_height_in": 6.5,
        },
        "methods": {
            "edge_source": {
                "method": "STRINGdb::get_interactions",
                "evidence": "combined physical and functional association evidence",
                "threshold": "combined_score >= configured threshold on the STRING 0-1000 scale",
                "stored_weight": "combined_score / 1000",
            },
            "community_detection": {
                "algorithm": "igraph::cluster_louvain",
                "weights": "combined_score / 1000", "seed": 42,
            },
            "betweenness": {
                "algorithm": "igraph::betweenness", "directed": False,
                "edge_distance": "1 / (combined_score / 1000)",
            },
            "layout": {
                "requested": "fr", "realized": "igraph::layout_with_fr", "seed": 42,
            },
            "figure_labels": {
                "algorithm": "ggrepel::geom_label_repel",
                "selection": "highest node degree", "seed": 42,
            },
        },
    }


def test_ppi_sidecar_is_loaded_and_rendered_without_inference(mrs, tmp_path) -> None:
    path = tmp_path / "results" / "networks" / "string_ppi_provenance.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_ppi_sidecar()), encoding="utf-8")
    provenance = mrs.ppi_provenance(tmp_path)
    assert provenance["schema_version"] == 1
    assert provenance["sidecar_path"] == "results/networks/string_ppi_provenance.json"
    # A configured score below the usable STRING floor is clamped by the R script; the
    # sidecar must keep the requested and the realized value apart.
    assert provenance["configuration"]["score_threshold_combined"] == 0
    assert provenance["realized"]["score_threshold_combined"] == 400

    payload = _microarray_payload()
    payload["ppi_provenance"] = provenance
    for rendered in (mrs.render_text(payload), mrs.render_tools_references(payload)):
        assert "STRING realized version/build: 12.0 / stable-build-abc" in rendered
        assert "STRING taxon/query date (UTC): 3702 / 2026-08-11" in rendered
        assert "Mapped seeds: 149 of 154 after limit (154 input); unique STRING IDs: 148" in rendered
        assert "combined-score threshold/min/max: 400 / 401 / 999" in rendered
        assert "STRING combined_score integrates" not in rendered  # exact sidecar evidence only
        assert "combined physical and functional association evidence" in rendered
        assert "igraph::cluster_louvain; weights=combined_score / 1000; seed=42" in rendered
        assert "edge distance=1 / (combined_score / 1000)" in rendered
        assert "requested=fr; realized=igraph::layout_with_fr; seed=42" in rendered
        assert "R=4.5.2; STRINGdb=2.22.0; igraph=2.2.1" in rendered


def test_label_free_ppi_sidecar_does_not_require_unused_ggrepel(mrs, tmp_path) -> None:
    sidecar = _ppi_sidecar()
    sidecar["software"]["ggrepel"] = "0.9.6"  # stale entry from a labelled-figure run
    sidecar["methods"]["figure_labels"] = {
        "algorithm": "no node labels in the static topology panel",
        "selection": "none; identities and hub metrics are reported outside the static panel",
        "identity_and_hub_metrics": [
            "interactive PPI view", "results/networks/ppi_hub_genes.csv",
        ],
        "seed": None,
    }
    path = tmp_path / "results" / "networks" / "string_ppi_provenance.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(sidecar), encoding="utf-8")

    provenance = mrs.ppi_provenance(tmp_path)
    assert provenance["status"] == "PASS"
    payload = _microarray_payload()
    payload["ppi_provenance"] = provenance
    rendered = "\n".join(mrs.ppi_provenance_lines(payload))
    assert "Figure labels: no node labels in the static topology panel" in rendered
    assert "selection=none; identities and hub metrics are reported outside the static panel" in rendered
    assert "ggrepel=" not in rendered

    sidecar["methods"]["figure_labels"].pop("selection")
    path.write_text(json.dumps(sidecar), encoding="utf-8")
    invalid = mrs.ppi_provenance(tmp_path)
    assert invalid["status"] == "INVALID"
    assert "methods.figure_labels.selection" in invalid["reason"]


def test_unreadable_check_file_is_reported_as_fail_not_a_crash(tmp_path, monkeypatch) -> None:
    # The PPI check used to be hand-escaped JSON, so a STRING error message carrying a
    # newline or backslash produced a file json.loads() could not parse; the sanity rule
    # then died instead of reporting it.
    agg = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "aggregate_sanity_checks.py"
    spec = importlib.util.spec_from_file_location("aggregate_sanity_checks", agg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    checks = tmp_path / "checks"
    checks.mkdir()
    broken = checks / "16_ppi_network.json"
    broken.write_text('{"check": "16_ppi_network", "messages": [{"message": "a\nb"}]}',
                      encoding="utf-8")
    good = checks / "01_input_validation.json"
    good.write_text(json.dumps({"check": "01_input_validation", "status": "PASS",
                                "messages": [{"status": "PASS", "message": "ok"}]}),
                    encoding="utf-8")
    out = tmp_path / "sanity_checks.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["aggregate", "--checks", str(broken), str(good), "--out", str(out)])
    assert mod.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "Overall: FAIL" in text
    assert "16_ppi_network: FAIL" in text
    assert "unreadable check file:" in text
    assert "01_input_validation: PASS" in text


def test_missing_ppi_sidecar_is_explicitly_not_recorded(mrs, tmp_path) -> None:
    provenance = mrs.ppi_provenance(tmp_path)
    assert provenance == {
        "status": "NOT_RECORDED", "reason": "not recorded",
        "sidecar_path": "results/networks/string_ppi_provenance.json",
    }
    payload = _microarray_payload()
    payload["ppi"]["taxon"] = 999999
    payload["ppi_provenance"] = provenance
    lines = "\n".join(mrs.ppi_provenance_lines(payload))
    assert "Status: NOT_RECORDED; reason: not recorded" in lines
    assert "STRING taxon/query date (UTC): not recorded / not recorded" in lines
    assert "999999" not in lines


@pytest.mark.parametrize(
    "body, reason_fragment",
    [
        ("{broken", "malformed sidecar"),
        (json.dumps({"schema_version": 99}), "unsupported or missing schema_version"),
        (json.dumps({"schema_version": 1, "status": "PASS"}), "missing required fields"),
    ],
)
def test_malformed_ppi_sidecar_fails_closed(mrs, tmp_path, body, reason_fragment) -> None:
    path = tmp_path / "results" / "networks" / "string_ppi_provenance.json"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    provenance = mrs.ppi_provenance(tmp_path)
    assert provenance["status"] == "INVALID"
    assert reason_fragment in provenance["reason"]
    payload = _microarray_payload()
    payload["ppi_provenance"] = provenance
    rendered = "\n".join(mrs.ppi_provenance_lines(payload))
    assert "Status: INVALID" in rendered
    assert "STRING realized version/build: not recorded / not recorded" in rendered


def test_ppi_pass_sidecar_cannot_omit_realized_facts(mrs, tmp_path) -> None:
    sidecar = _ppi_sidecar()
    sidecar["database"]["realized_version"] = None
    sidecar["realized"]["mapped_seed_count"] = None
    path = tmp_path / "results" / "networks" / "string_ppi_provenance.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(sidecar), encoding="utf-8")
    provenance = mrs.ppi_provenance(tmp_path)
    assert provenance["status"] == "INVALID"
    assert "PASS sidecar lacks realized facts" in provenance["reason"]
    assert "database.realized_version" in provenance["reason"]
    assert "realized.mapped_seed_count" in provenance["reason"]


# ---- Figure-style plumbing in the stats / GOI R scripts ---------------------

_SCRIPTS = Path(__file__).resolve().parents[1] / "workflow" / "scripts"

# Figure group each script belongs to, as offered by the GUI override table
# (main_window.py PALETTE_GROUPS). A script that never calls getp_for ignores its
# group's palette, font, base font size and canvas entirely.
_STYLE_GROUPS = {
    "make_figures.R": "core",
    "make_enrichment_figures.R": "enrichment",
    "make_goi.R": "core",
    "run_wilcoxon.R": "core",
    "run_gsva.R": "core",
    "run_set_overlap.R": "enrichment",
}


def _assert_style_routing(name: str, source: str) -> None:
    assert f'getp_for(style, "{_STYLE_GROUPS[name]}")' in source, name
    # A requested Windows font must be mapped onto one installed in the pipeline
    # environment; taking the name verbatim renders a serif request as the sans default.
    assert "resolve_font(" in source, name
    assert 'if (nzchar(font_family)) font_family else NULL' not in source, name


@pytest.mark.parametrize("name", sorted(_STYLE_GROUPS))
def test_every_figure_script_routes_style_through_its_group(name: str) -> None:
    _assert_style_routing(name, (_SCRIPTS / name).read_text(encoding="utf-8"))


def test_style_routing_gate_rejects_a_global_only_getter() -> None:
    source = (_SCRIPTS / "run_wilcoxon.R").read_text(encoding="utf-8")
    broken = source.replace('gp <- getp_for(style, "core")', "gp <- make_getp(style)", 1)
    assert broken != source
    with pytest.raises(AssertionError):
        _assert_style_routing("run_wilcoxon.R", broken)


def test_style_routing_gate_rejects_an_unresolved_font_family() -> None:
    source = (_SCRIPTS / "run_set_overlap.R").read_text(encoding="utf-8")
    broken = source.replace(
        "base_family <- resolve_font(font_family)",
        "base_family <- if (nzchar(font_family)) font_family else NULL", 1)
    assert broken != source
    with pytest.raises(AssertionError):
        _assert_style_routing("run_set_overlap.R", broken)


def test_goi_figures_use_the_configured_canvas_and_resolution() -> None:
    source = (_SCRIPTS / "make_goi.R").read_text(encoding="utf-8")
    assert 'fig_dpi <- as.integer(getp("dpi", 300))' in source
    assert 'fig_w <- as.numeric(gp("width_in", 7)); fig_h <- as.numeric(gp("height_in", 5))' in source
    for hardcoded in ("dpi = 300", "dpi = 150", "res = 300", "width = 7, height = 5", "w = 7,"):
        assert hardcoded not in source, hardcoded


def test_set_overlap_dotplot_paints_both_colour_and_fill() -> None:
    # enrichplot maps p.adjust to `fill` in current versions; a colour-only scale
    # silently leaves the default red-blue in place of the configured palette.
    source = (_SCRIPTS / "run_set_overlap.R").read_text(encoding="utf-8")
    assert "scale_colour_gradientn(colours = pal_spec$seq(255)" in source
    assert "scale_fill_gradientn(colours = pal_spec$seq(255)" in source


def _run_summarize_quantification(tmp_path: Path, monkeypatch, summary_rows, route: str) -> dict:
    import importlib.util
    import sys

    summary = tmp_path / "counts.txt.summary"
    summary.write_text("\n".join("\t".join(row) for row in summary_rows) + "\n", encoding="utf-8")
    out = tmp_path / "check.json"
    script = _SCRIPTS / "summarize_quantification.py"
    spec = importlib.util.spec_from_file_location("summarize_quantification", script)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", [
        "summarize_quantification.py", "--summary", str(summary), "--out", str(out),
        "--route", route,
    ])
    spec.loader.exec_module(mod)
    mod.main()
    return json.loads(out.read_text(encoding="utf-8"))


def test_low_assignment_message_is_route_aware(tmp_path: Path, monkeypatch) -> None:
    # featureCounts (-s) and STAR gene-counts (--strand) both take an explicit strandedness
    # code; Salmon runs with -l A (auto-detected), so strandedness is not a plausible cause
    # for a low rate there.
    rows = [
        ["Status", "s1"],
        ["Assigned", "30"],
        ["Unassigned_NoFeatures", "70"],
    ]
    aligned = _run_summarize_quantification(tmp_path, monkeypatch, rows, "aligned")
    assert any("strandedness" in m["message"] for m in aligned["messages"])
    assert not any("auto-detected" in m["message"] for m in aligned["messages"])

    salmon = _run_summarize_quantification(tmp_path, monkeypatch, rows, "salmon")
    assert not any("strandedness setting" in m["message"] for m in salmon["messages"])
    assert any("auto-detected" in m["message"] for m in salmon["messages"])


def test_count_matrix_route_never_mentions_strandedness(tmp_path: Path, monkeypatch) -> None:
    # ingest_counts.py's summary carries only an Assigned row (workflow/scripts/ingest_counts.py),
    # so has_unassigned is False and the route-specific cause is never reached regardless of
    # --route; a count matrix has no alignment step, so strandedness cannot be the cause.
    rows = [["Status", "s1", "s2"], ["Assigned", "5", "0"]]
    for route in ("aligned", "salmon"):
        result = _run_summarize_quantification(tmp_path, monkeypatch, rows, route)
        assert not any("strandedness" in m["message"] for m in result["messages"])


def test_environment_spec_reads_marker_via_conda_prefix(tmp_path: Path, monkeypatch, mrs) -> None:
    prefix = tmp_path / "envs" / "bulkseq"
    prefix.mkdir(parents=True)
    (prefix / ".bulkseq_spec").write_text("bulkseq.lock.yaml\nabc123\nlock\n", encoding="utf-8")
    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    spec = mrs.environment_spec()
    assert spec == {"file": "bulkseq.lock.yaml", "sha256": "abc123", "source": "lock"}


def test_environment_spec_absent_marker_reports_unknown(tmp_path: Path, monkeypatch, mrs) -> None:
    prefix = tmp_path / "envs" / "bulkseq"
    prefix.mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    spec = mrs.environment_spec()
    assert spec["source"] == "unknown"
    assert spec["file"] is None


def test_environment_spec_negative_fallback_source_is_not_reported_as_lock(tmp_path: Path, monkeypatch, mrs) -> None:
    # The defect this marker exists to catch: a float-spec install must never be reported
    # as the pinned lock.
    prefix = tmp_path / "envs" / "bulkseq"
    prefix.mkdir(parents=True)
    (prefix / ".bulkseq_spec").write_text("bulkseq_full.yaml\ndef456\nfallback\n", encoding="utf-8")
    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    spec = mrs.environment_spec()
    assert spec["source"] == "fallback"
    assert spec["source"] != "lock"
    assert spec["file"] == "bulkseq_full.yaml"


def test_environment_spec_reports_core_source(tmp_path: Path, monkeypatch, mrs) -> None:
    prefix = tmp_path / "envs" / "bulkseq"
    prefix.mkdir(parents=True)
    (prefix / ".bulkseq_spec").write_text("bulkseq_core.yaml\nc0re1\ncore\n", encoding="utf-8")
    monkeypatch.setenv("CONDA_PREFIX", str(prefix))
    spec = mrs.environment_spec()
    assert spec["source"] == "core"


def test_active_env_prefix_falls_back_to_sys_executable(tmp_path: Path, monkeypatch, mrs) -> None:
    # Native-Linux/CLI runs invoke this script's own interpreter with neither CONDA_PREFIX
    # nor MAMBA_ROOT_PREFIX set. sys.executable is <prefix>/bin/python3.
    prefix = tmp_path / "envs" / "bulkseq"
    (prefix / "bin").mkdir(parents=True)
    (prefix / ".bulkseq_profile").write_text("core\n", encoding="utf-8")
    fake_python = prefix / "bin" / "python3"
    fake_python.write_text("", encoding="utf-8")
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    monkeypatch.setattr(mrs.sys, "executable", str(fake_python))
    assert mrs._active_env_prefix() == prefix


def test_active_env_prefix_negative_no_marker_returns_none(tmp_path: Path, monkeypatch, mrs) -> None:
    # Without a .bulkseq_profile marker at the derived prefix, the fallback must not guess.
    prefix = tmp_path / "envs" / "bulkseq"
    (prefix / "bin").mkdir(parents=True)
    fake_python = prefix / "bin" / "python3"
    fake_python.write_text("", encoding="utf-8")
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    monkeypatch.setattr(mrs.sys, "executable", str(fake_python))
    assert mrs._active_env_prefix() is None


# ---- workflow_provenance: workflow_metadata.yaml, not the creation stamp, is the source ----


def _write_workflow_metadata(root: Path, **fields) -> None:
    import yaml
    (root / "workflow").mkdir(parents=True, exist_ok=True)
    (root / "workflow" / "workflow_metadata.yaml").write_text(
        yaml.safe_dump(fields, sort_keys=False), encoding="utf-8")


def test_workflow_provenance_prefers_metadata_over_creation_stamp(tmp_path: Path, mrs) -> None:
    _write_workflow_metadata(tmp_path, app_version="0.30.0", workflow_version="0.30.1",
                              workflow_digest="a" * 64, copied_at="2026-09-10T12:00:00")
    project = {"app_version": "0.29.0", "workflow_version": "0.29.0"}

    prov = mrs.workflow_provenance(tmp_path, project)

    assert prov == {"executed_version": "0.30.1", "executed_app_version": "0.30.0",
                    "digest": "a" * 64, "copied_at": "2026-09-10T12:00:00"}


def test_workflow_provenance_falls_back_when_metadata_absent(tmp_path: Path, mrs) -> None:
    project = {"app_version": "0.29.0", "workflow_version": "0.29.0"}

    prov = mrs.workflow_provenance(tmp_path, project)

    assert prov == {"executed_version": "0.29.0", "executed_app_version": None, "digest": None, "copied_at": None}


@pytest.mark.parametrize("body", ["not: [valid, yaml:", "- just\n- a\n- list\n", "workflow_digest: only\n"])
def test_workflow_provenance_falls_back_on_malformed_metadata(tmp_path: Path, mrs, body) -> None:
    (tmp_path / "workflow").mkdir(parents=True)
    (tmp_path / "workflow" / "workflow_metadata.yaml").write_text(body, encoding="utf-8")
    project = {"app_version": "0.29.0", "workflow_version": "0.29.0"}

    prov = mrs.workflow_provenance(tmp_path, project)

    assert prov == {"executed_version": "0.29.0", "executed_app_version": None, "digest": None, "copied_at": None}


def test_workflow_provenance_negative_never_reports_stamp_when_metadata_disagrees(tmp_path: Path, mrs) -> None:
    # Regression: project stamp says 0.29.0, but workflow/ was re-synced
    # to 0.30.1 after creation. The executed version must be 0.30.1, never the stale stamp.
    _write_workflow_metadata(tmp_path, workflow_version="0.30.1", workflow_digest="b" * 64,
                              copied_at="2026-09-10T09:00:00")
    project = {"app_version": "0.29.0", "workflow_version": "0.29.0"}

    prov = mrs.workflow_provenance(tmp_path, project)

    assert prov["executed_version"] != "0.29.0"
    assert prov["executed_version"] == "0.30.1"


def test_workflow_provenance_with_metadata_lacking_app_version(tmp_path: Path, mrs) -> None:
    # Pre-0.30.1 metadata.yaml has no app_version field; executed_app_version must be None
    # so the fallback to project.get("app_version") activates in the payload.
    _write_workflow_metadata(tmp_path, workflow_version="0.30.0", workflow_digest="c" * 64,
                              copied_at="2026-09-10T08:00:00")
    project = {"app_version": "0.29.0", "workflow_version": "0.29.0"}

    prov = mrs.workflow_provenance(tmp_path, project)

    assert prov == {"executed_version": "0.30.0", "executed_app_version": None,
                    "digest": "c" * 64, "copied_at": "2026-09-10T08:00:00"}


def test_workflow_version_summary_reports_executed_version_and_creation_stamps(mrs) -> None:
    payload = {
        "workflow_version": "0.30.1", "workflow_digest": "c" * 64, "workflow_copied_at": "2026-09-10T09:00:00",
        "project_created_app_version": "0.29.0", "project_created_workflow_version": "0.29.0",
    }
    line = mrs.workflow_version_summary(payload)
    assert line == ("Workflow version: 0.30.1 (digest cccccccccccc, copied 2026-09-10T09:00:00); "
                     "project created with app 0.29.0 / workflow 0.29.0")


def test_workflow_version_summary_labels_the_stamp_fallback(mrs) -> None:
    payload = {"workflow_version": "0.29.0", "workflow_digest": None, "workflow_copied_at": None,
               "project_created_app_version": "0.29.0", "project_created_workflow_version": "0.29.0"}
    line = mrs.workflow_version_summary(payload)
    assert line == "Workflow version: 0.29.0 (project creation stamp; no workflow_metadata.yaml)"


def test_render_text_and_tools_references_use_executed_workflow_version(mrs) -> None:
    payload = _base_payload(
        workflow_version="0.30.1", workflow_digest="d" * 64, workflow_copied_at="2026-09-10T09:00:00",
        project_created_app_version="0.29.0", project_created_workflow_version="0.29.0",
        enrichment={}, ppi={}, microarray={}, gene_sets={}, r_packages={}, sanity_checks="",
    )
    text = mrs.render_text(payload)
    refs = mrs.render_tools_references(payload)
    assert "Workflow version: 0.30.1 (digest dddddddddddd" in text
    assert "0.29.0" in text  # only inside the "project created with" clause
    assert "Workflow version: 0.29.0" not in text
    assert "Workflow version: 0.30.1 (digest dddddddddddd" in refs
    assert "Workflow version: 0.29.0" not in refs


# --- Reports: shared renderers, provenance parity, enrichment-evidence drift, single contrast ---

_MHR = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_html_report.py"
_VALIDATE = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "validate_project.py"
_ENRICHMENT_R = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "run_enrichment.R"
_DESEQ2_SMK = Path(__file__).resolve().parents[1] / "workflow" / "rules" / "deseq2.smk"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mhr():
    return _load("make_html_report", _MHR)


@pytest.fixture(scope="module")
def validate_project():
    return _load("validate_project", _VALIDATE)


def _strandedness_payload(**realized) -> dict:
    return {"strandedness": {"configured": {"code": 0}, "realized": realized}}


_STRANDEDNESS_FIXTURES = {
    "uniform": _strandedness_payload(
        code=2, label="reverse", path="results/aligned/strandedness.txt",
        per_sample={"a": 2, "b": 2}, uniform=True),
    "mixed": _strandedness_payload(
        code=2, label="reverse", path="results/aligned/strandedness.txt",
        per_sample={"a": 1, "b": 2}, uniform=False),
    "no_per_sample": _strandedness_payload(
        code=0, label="unstranded", path="results/aligned/strandedness.txt"),
    "label_disagrees_with_code": _strandedness_payload(
        code=1, label="reverse", path="results/aligned/strandedness.txt"),
    "code_is_boolean": _strandedness_payload(
        code=True, label="forward", path="results/aligned/strandedness.txt"),
    "path_missing": _strandedness_payload(code=2, label="reverse", path="   "),
    "realized_absent": {"strandedness": {"configured": {"code": 2}}},
    "no_record": {},
}


@pytest.mark.parametrize("name", sorted(_STRANDEDNESS_FIXTURES))
def test_both_reports_render_the_same_realized_strandedness(mrs, mhr, name) -> None:
    payload = _STRANDEDNESS_FIXTURES[name]
    assert mrs.realized_strandedness_text(payload) == mhr._realized_strandedness_text(payload)


def test_realized_strandedness_renders_mixed_runs_per_sample(mrs) -> None:
    text = mrs.realized_strandedness_text(_STRANDEDNESS_FIXTURES["mixed"])
    assert text == ("mixed (realized per-sample from results/aligned/strandedness.txt: "
                    "a=forward, b=reverse)")


@pytest.mark.parametrize("name", ["label_disagrees_with_code", "code_is_boolean", "path_missing",
                                  "realized_absent", "no_record"])
def test_realized_strandedness_renders_nothing_for_a_malformed_record(mrs, name) -> None:
    assert mrs.realized_strandedness_text(_STRANDEDNESS_FIXTURES[name]) is None


def test_pre_fix_html_renderer_misreports_a_mixed_run(mrs, tmp_path) -> None:
    # Negative control for the shared renderer: the 0.30.1 report renderer described a
    # per-sample (mixed) run with the single first-sample code, which is what RPT-1 fixes.
    # The control reads the PREVIOUS release's renderer out of git history. A continuous
    # integration checkout is shallow, so that commit is usually absent there; skip visibly
    # rather than fail, and say why, so the suite's skip summary shows the control did not run.
    repo = str(Path(__file__).resolve().parents[1])
    commit = "42caf51"
    present = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                             cwd=repo, capture_output=True)
    if present.returncode != 0:
        pytest.skip(f"commit {commit} is not in this clone (shallow checkout): "
                    "fetch history to run the pre-fix renderer control")
    source = subprocess.run(["git", "show", f"{commit}:workflow/scripts/make_html_report.py"],
                            cwd=repo, capture_output=True, text=True, check=True).stdout
    previous_path = tmp_path / "previous_make_html_report.py"
    previous_path.write_text(source, encoding="utf-8")
    previous = _load("previous_make_html_report", previous_path)

    mixed = _STRANDEDNESS_FIXTURES["mixed"]
    assert previous._realized_strandedness_text(mixed) == (
        "reverse (2; realized from results/aligned/strandedness.txt)")
    assert "mixed" not in previous._realized_strandedness_text(mixed)
    # The parity assertion above holds only because of the shared renderer.
    assert previous._realized_strandedness_text(mixed) != mrs.realized_strandedness_text(mixed)


def _provenance_payload(**overrides) -> dict:
    payload = _base_payload(
        app_version="0.31.0", workflow_version="0.31.0", workflow_digest="e" * 64,
        workflow_copied_at="2026-09-12T08:00:00",
        project_created_app_version="0.29.0", project_created_workflow_version="0.29.0",
        workflow_git_commit="f" * 40, environment_lock_md5="9" * 32,
        environment_spec={"file": "bulkseq.lock.yaml", "source": "lock", "sha256": "a" * 64},
        r_packages={"DESeq2": "1.48.0"}, software_versions={"snakemake": "9.23.1"},
    )
    payload.update(overrides)
    return payload


def test_report_carries_every_provenance_field_the_run_summary_records(mrs, mhr) -> None:
    payload = _provenance_payload()
    summary = mrs.render_text(payload)
    report = mhr._versions_table(payload)
    spec = payload["environment_spec"]
    expected = [
        payload["app_version"], payload["workflow_version"], payload["workflow_digest"][:12],
        payload["workflow_copied_at"], payload["project_created_app_version"],
        payload["project_created_workflow_version"], spec["file"], spec["source"],
        spec["sha256"], payload["environment_lock_md5"], payload["workflow_git_commit"][:12],
    ]
    for value in expected:
        assert value in summary, value
        assert value in report, value


def test_report_never_substitutes_the_lock_md5_for_unrecorded_provenance(mrs, mhr) -> None:
    payload = _provenance_payload(
        app_version=None, environment_spec={"file": None, "source": "unknown", "sha256": None})
    rows = dict(mhr._provenance_rows(payload))
    lock = payload["environment_lock_md5"]

    assert rows["App version"] == "not recorded (workflow copied before 0.30.1)"
    assert rows["Installed environment spec"] == (
        "unknown (no marker; environment predates this record)")
    assert lock not in rows["App version"]
    assert lock not in rows["Installed environment spec"]
    # Both fallbacks are the run summary's own wording, not a second spelling of it.
    assert rows["App version"] in mrs.render_text(payload)
    assert rows["Installed environment spec"] in mrs.environment_spec_line(payload)


# Literals in run_enrichment.R that look like a summary-line prefix but are written somewhere
# else: a check message, an stderr message(), or a detail fragment embedded inside another line.
_NON_SUMMARY_PREFIXES = frozenset({
    "Enrichment:", "Enrichment skipped:", "Enrichment could not run:",
    "Entrez collapse requires columns:", "GSEA deterministic tie handling:",
    "KEGG ORA retrieval failed:", "KEGG GSEA retrieval failed:", "KEGG registry unavailable:",
    "KEGG enrichment could not run:",
})
# Summary lines that report results or route selection rather than identifier-mapping,
# universe, gate or resource evidence. The reports render these from the result tables.
_NON_EVIDENCE_SUMMARY_PREFIXES = frozenset({
    "Skipped:", "Enrichment failed:", "KEGG enrichment failed:", "GO route:",
    "GO/disease enrichment:", "GO BP terms (gost ORA):", "KEGG-only enrichment:",
    "KEGG locus-tag-to-GeneID bridge:", "Significant genes (ORA input):",
    "Ranked genes (GSEA input):", "Up-regulated:", "Down-regulated:", "Combined significant:",
    "GSEA GO BP gene sets meeting the adjusted criterion (directional, full ranked list):",
    "KEGG adjusted results meeting the criterion:",
})

_R_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
_R_PREFIX = re.compile(r"^([A-Z][^%:]*?:)(\s|$)")


def _emitted_prefixes(r_source: str) -> set[str]:
    """Prefix-shaped literals in run_enrichment.R, the shape its summary lines are written in."""
    found = set()
    for match in _R_LITERAL.finditer(r_source):
        prefix = _R_PREFIX.match(match.group(1))
        if prefix:
            found.add(prefix.group(1))
    return found


def _assert_prefixes_classified(r_source: str) -> None:
    from _enrichment_evidence import ENRICHMENT_EVIDENCE_PREFIXES
    classified = (set(ENRICHMENT_EVIDENCE_PREFIXES) | _NON_SUMMARY_PREFIXES
                  | _NON_EVIDENCE_SUMMARY_PREFIXES)
    unclassified = sorted(p for p in _emitted_prefixes(r_source)
                          if not any(p.startswith(c) for c in classified))
    assert not unclassified, (
        "run_enrichment.R writes prefixes neither quoted as evidence nor classified as "
        f"non-evidence: {unclassified}")


def test_every_enrichment_summary_prefix_is_classified() -> None:
    _assert_prefixes_classified(_ENRICHMENT_R.read_text(encoding="utf-8"))


def test_a_new_enrichment_summary_prefix_fails_the_drift_check() -> None:
    source = _ENRICHMENT_R.read_text(encoding="utf-8")
    injected = source.replace(
        'sprintf("Unmapped input IDs excluded: %d", mapping$unmapped_inputs),',
        'sprintf("Unmapped input IDs excluded: %d", mapping$unmapped_inputs),\n'
        '      sprintf("Orthology bridge coverage: %d", 0),', 1)
    assert injected != source
    with pytest.raises(AssertionError, match="Orthology bridge coverage:"):
        _assert_prefixes_classified(injected)


def test_both_reports_quote_the_same_enrichment_evidence(mrs, mhr, tmp_path) -> None:
    from _enrichment_evidence import ENRICHMENT_EVIDENCE_PREFIXES
    summary = "\n".join([f"{prefix} value" for prefix in ENRICHMENT_EVIDENCE_PREFIXES]
                        + ["Up-regulated: 10 genes, 3 GO BP terms (ORA)"])
    path = tmp_path / "results" / "enrichment" / "enrichment_summary.txt"
    path.parent.mkdir(parents=True)
    path.write_text(summary, encoding="utf-8")

    evidence = mrs.enrichment_mapping_evidence(tmp_path)["evidence"]
    assert evidence == mhr.evidence_lines(summary)
    assert len(evidence) == len(ENRICHMENT_EVIDENCE_PREFIXES)
    assert "KEGG retrieval date (UTC): value" in evidence
    assert "KEGG key form observed: value" in evidence


def _contrast(numerator: str, denominator: str) -> dict:
    return {"name": f"{numerator}_vs_{denominator}", "factor": "condition",
            "numerator": numerator, "denominator": denominator}


_ONE_CONTRAST = [_contrast("treated", "control")]
_TWO_CONTRASTS = [_contrast("treated", "control"), _contrast("recovered", "control")]
_IGNORED_TEXT = "recovered vs control (factor: condition) [recovered_vs_control]"


def _smk_contrast_notice(contrasts) -> list[str]:
    """Execute deseq2.smk's own parse-time disclosure block against a configured contrast list."""
    source = _DESEQ2_SMK.read_text(encoding="utf-8")
    block = re.search(r"(?m)^_CONTRAST_NOTICE = .*\n^if _CONTRAST_NOTICE:\n(?:    .*\n)+", source)
    assert block is not None, "deseq2.smk must disclose the contrasts it does not analyse"
    written: list[str] = []
    from _contrast_disclosure import single_contrast_notice
    namespace = {"single_contrast_notice": single_contrast_notice,
                 "_DE": {"contrasts": contrasts},
                 "sys": SimpleNamespace(stderr=SimpleNamespace(write=written.append))}
    exec(block.group(0), namespace)
    return written


def _de_payload(contrasts) -> dict:
    return _base_payload(deseq2={"design_formula": "~ condition", "reference_level": "control",
                                 "contrasts": contrasts, "alpha": 0.05, "lfc_threshold": 1,
                                 "shrinkage_method": "apeglm"})


def test_a_second_contrast_is_disclosed_on_every_non_gui_surface(mrs, mhr, validate_project,
                                                                 tmp_path) -> None:
    from _contrast_disclosure import IGNORED_LABEL, single_contrast_notice
    payload = _de_payload(_TWO_CONTRASTS)

    written = _smk_contrast_notice(_TWO_CONTRASTS)
    assert written == [f"WARNING: {single_contrast_notice(_TWO_CONTRASTS)}\n"]
    assert _IGNORED_TEXT in written[0]

    for text in (mrs.render_text(payload), mrs.render_study_design(payload, "", None)):
        assert "Contrast analysed: treated vs control (factor: condition)" in text
        assert f"{IGNORED_LABEL}: {_IGNORED_TEXT}" in text
        assert "Contrasts: " not in text

    cards = mhr._meta_cards(payload, tmp_path)
    assert f"<div class='card-k'>{IGNORED_LABEL}</div>" in cards
    assert _IGNORED_TEXT in cards
    assert f"{IGNORED_LABEL}: {_IGNORED_TEXT}" in mhr._study_design_section(payload)

    messages = validate_project.check_single_contrast({"deseq2": {"contrasts": _TWO_CONTRASTS}})
    assert [m["status"] for m in messages] == ["WARNING"]
    assert _IGNORED_TEXT in messages[0]["message"]


def test_a_single_contrast_project_is_not_qualified_anywhere(mrs, mhr, validate_project,
                                                             tmp_path) -> None:
    from _contrast_disclosure import IGNORED_LABEL, SINGLE_CONTRAST_SENTENCE
    payload = _de_payload(_ONE_CONTRAST)

    assert _smk_contrast_notice(_ONE_CONTRAST) == []
    assert validate_project.check_single_contrast({"deseq2": {"contrasts": _ONE_CONTRAST}}) == []
    surfaces = [mrs.render_text(payload), mrs.render_study_design(payload, "", None),
                mhr._meta_cards(payload, tmp_path), mhr._study_design_section(payload)]
    for text in surfaces:
        assert IGNORED_LABEL not in text
        assert SINGLE_CONTRAST_SENTENCE not in text
    assert ("Contrasts: treated vs control (factor: condition) [treated_vs_control]"
            in surfaces[0])


def test_the_single_contrast_warning_does_not_block_the_launch_gate(validate_project,
                                                                    tmp_path) -> None:
    out = tmp_path / "00_project_setup.json"
    validate_project.write_payload(
        out, "00_project_setup",
        validate_project.check_single_contrast({"deseq2": {"contrasts": _TWO_CONTRASTS}}))
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "WARNING"


def test_provenance_survives_a_report_with_no_recorded_tool_versions(mhr) -> None:
    payload = _provenance_payload(software_versions={}, r_packages={})
    section = mhr._versions_table(payload)
    assert payload["workflow_digest"][:12] in section
    assert payload["environment_spec"]["sha256"] in section
