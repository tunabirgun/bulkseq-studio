from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core.provenance import (
    _drop_project,
    _load_reference_integrity,
    _summary_text,
    diff_configs,
)
from app.core.timing import _timing_text

# workflow/scripts/make_run_summary.py is the pipeline-side provenance writer (this module,
# app.core.provenance, is only the GUI's pre-run preview). Loaded by path like the other
# workflow/scripts/*.py tests (see tests/test_html_report_enrichment.py) since it is not
# imported as a package.
_MRS_PATH = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_run_summary.py"


def _load_make_run_summary():
    spec = importlib.util.spec_from_file_location("make_run_summary", _MRS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_diff_reports_changed_scalar() -> None:
    defaults = {"fastp": {"length_required": 36}}
    used = {"fastp": {"length_required": 50}}
    changed = diff_configs(defaults, used)
    assert changed == {"fastp.length_required": {"default": 36, "used": 50}}


def test_diff_ignores_keys_absent_from_defaults() -> None:
    changed = diff_configs({"a": 1}, {"a": 1, "b": 2})
    assert changed == {}


def test_diff_identical_configs_empty() -> None:
    cfg = {"workflow": {"aligner": "STAR"}, "deseq2": {"alpha": 0.05}}
    assert diff_configs(cfg, cfg) == {}


def test_drop_project_excludes_only_project() -> None:
    cfg = {"project": {"name": "x"}, "workflow": {"aligner": "STAR"}}
    assert _drop_project(cfg) == {"workflow": {"aligner": "STAR"}}


def test_project_identity_not_reported_as_customized() -> None:
    defaults = {"project": {"name": "example_project"}, "workflow": {"aligner": "STAR"}}
    used = {"project": {"name": "real_project"}, "workflow": {"aligner": "STAR"}}
    changed = diff_configs(_drop_project(defaults), _drop_project(used))
    assert changed == {}


def test_summary_text_reports_none_when_empty() -> None:
    text = _summary_text({"customized_parameters": {}, "software_versions": {}, "workflow": {}})
    assert "None detected" in text


def test_gui_fallback_summary_title_is_route_neutral() -> None:
    text = _summary_text({
        "project": {"name": "example", "working_directory": "/tmp/example"},
        "input": {"type": "microarray"},
        "workflow": {},
        "customized_parameters": {},
        "software_versions": {},
    })
    assert text.startswith("BulkSeq Studio Analysis Run Summary\n")
    assert "RNA-seq Analysis Run Summary" not in text


def test_gui_fallback_timing_title_is_route_neutral() -> None:
    text = _timing_text({
        "project_name": "example",
        "run_start_time": None,
        "run_finish_time": None,
        "pre_run_estimate": {},
        "slowest_steps": [],
    })
    assert text.startswith("BulkSeq Studio Analysis Timing Summary\n")
    assert "RNA-seq Analysis Timing Summary" not in text


def test_gui_preview_labels_imported_results_without_claiming_a_local_de_model() -> None:
    text = _summary_text({
        "project": {}, "workflow": {}, "customized_parameters": {}, "software_versions": {},
        "input": {"type": "deseq2_results", "deseq2_results": "config/import.csv",
                  "deseq2_results_direction": {"numerator": "case", "denominator": "control",
                                               "confirmed": True,
                                               "confirmed_at": "2026-08-10T12:00:00+03:00"},
                  "deseq2_results_provenance": {
                      "original_basename": "study.csv",
                      "project_copy": "config/import.csv",
                      "sha256": "a" * 64,
                      "imported_at": "2026-08-10T12:00:00+03:00",
                      "row_count": 100,
                      "column_names": ["gene_id", "log2FoldChange", "padj"],
                      "upstream_method": "edgeR",
                      "lfc_shrinkage": "not_applied",
                      "p_adjustment_method": "Benjamini-Hochberg",
                  }},
    })
    assert "positive log2FC = higher in case than control" in text
    assert "Project copy: config/import.csv" in text
    assert "Original basename (local provenance only): study.csv" in text
    assert "Upstream adjusted-p method: Benjamini-Hochberg" in text
    assert "Source table:" not in text
    assert "no differential-expression model or LFC shrinkage" in text


def test_gui_fallback_loads_and_renders_realized_reference_integrity(tmp_path: Path) -> None:
    lock = {
        "schema_version": 1,
        "status": "PASS",
        "genome": {
            "integrity": {
                "source_md5": "a" * 32,
                "configured_md5": "a" * 32,
                "md5_status": "VERIFIED",
                "source_bytes": 91,
                "canonical_bytes": 80,
                "canonical_sha256": "b" * 64,
            },
            "content": {"record_count": 2, "total_bases": 42},
        },
        "annotation": {
            "integrity": {
                "source_md5": "c" * 32,
                "configured_md5": "c" * 32,
                "md5_status": "VERIFIED",
                "source_bytes": 101,
                "canonical_bytes": 90,
                "canonical_sha256": "d" * 64,
            },
            "content": {"evidence_counts": {"gene": 2, "exon": 4, "CDS": 2}},
        },
        "counting_contract": {
            "feature_types": ["exon"],
            "attribute_type": "gene_id",
            "feature_rows": 4,
            "feature_rows_with_attribute": 4,
            "feature_rows_missing_attribute": 0,
        },
        "contig_compatibility": {
            "overlap_contigs": 2,
            "annotation_contigs": 2,
            "compatible_feature_rows": 4,
            "annotation_feature_rows": 4,
            "feature_row_overlap_fraction": 1.0,
            "minimum_feature_row_overlap_fraction": 0.95,
        },
    }
    lock_path = tmp_path / "references" / "reference.lock.json"
    lock_path.parent.mkdir(parents=True)
    import json

    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    realized = _load_reference_integrity(tmp_path, "sra")
    assert realized["lock_path"] == "references/reference.lock.json"
    text = _summary_text({
        "project": {},
        "input": {"type": "sra"},
        "workflow": {},
        "reference_integrity": realized,
        "customized_parameters": {},
        "software_versions": {},
    })
    assert "Realized reference lock: PASS" in text
    assert f"Genome canonical SHA-256: {'b' * 64}" in text
    assert "Annotation features (gene/exon/CDS): 2/4/2" in text
    assert "Configured counting contract: feature_type=exon; attribute_type=gene_id" in text
    assert "Compatible contigs (overlap/annotation): 2/2" in text
    assert "Compatible annotation feature rows: 4/4 (100.00%; required >= 95%)" in text


def test_gui_fallback_reference_lock_is_route_aware_and_malformed_fails_closed(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "references" / "reference.lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("not-json", encoding="utf-8")

    malformed = _load_reference_integrity(tmp_path, "fastq")
    assert malformed["status"] == "FAIL"
    assert malformed["lock_path"] == "references/reference.lock.json"
    assert "could not be read" in malformed["error"]
    assert _load_reference_integrity(tmp_path, "microarray") == {}


def test_run_summary_tools_capture_configured_trimmer_and_rrna_filter() -> None:
    # make_run_summary.py's TOOLS dict previously never probed the trimmer / rRNA filter /
    # contamination screen a run actually used (only the fastp/sortmerna defaults were ever
    # hardcoded), so a project that enabled trim_galore, ribodetector or fastq_screen was not
    # reproducible from its own run summary. select_tools() gates the probe list on the
    # config that was actually used.
    mrs = _load_make_run_summary()
    # The contamination screen needs a FastQ Screen config path as well as the switch —
    # that is the Snakefile's own gate, and without it the rule is skipped with a warning.
    config = {"workflow": {"trimmer": "trim-galore", "rrna_filtering": True,
                           "rrna_tool": "ribodetector", "contamination_screen": True},
              "contamination": {"conf": "/path/to/fastq_screen.conf"}}
    tools = mrs.select_tools(config)
    assert "trim_galore" in tools, "configured trimmer (trim-galore) is not probed"
    assert "ribodetector" in tools, "configured rRNA filter (ribodetector) is not probed"
    assert "fastq_screen" in tools, "enabled contamination screen (fastq_screen) is not probed"
    assert "fastp" not in tools, "fastp should not be probed when trim-galore is configured"
    assert "sortmerna" not in tools, "sortmerna should not be probed when ribodetector is configured"
