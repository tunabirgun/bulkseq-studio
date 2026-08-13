from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "validate_project", Path(__file__).resolve().parent.parent / "workflow" / "scripts" / "validate_project.py")
validate_project = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_project)
check_design = validate_project.check_design
check_deseq2_results_direction = validate_project.check_deseq2_results_direction
check_deseq2_results_provenance = validate_project.check_deseq2_results_provenance
check_samples = validate_project.check_samples


def _samples(tmp_path, conditions):
    p = tmp_path / "samples.tsv"
    lines = ["sample_id\tcondition"] + [f"s{i}\t{c}" for i, c in enumerate(conditions)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _cfg(ref, num, den):
    return {"input": {"type": "sra"}, "deseq2": {
        "reference_level": {"condition": ref},
        "contrasts": [{"name": "c", "factor": "condition", "numerator": num, "denominator": den}]}}


def test_mismatch_fails(tmp_path):
    s = _samples(tmp_path, ["WT", "WT", "MUT", "MUT"])
    msgs = check_design(_cfg("control", "treated", "control"), s)
    fails = [m for m in msgs if m["status"] == "FAIL"]
    assert fails and any("control" in m["message"] and "WT" in m["message"] for m in fails)


def test_match_passes(tmp_path):
    s = _samples(tmp_path, ["WT", "WT", "MUT", "MUT"])
    assert check_design(_cfg("WT", "MUT", "WT"), s) == []


def test_missing_factor_column_fails(tmp_path):
    s = _samples(tmp_path, ["WT", "MUT"])
    cfg = {"input": {"type": "sra"}, "deseq2": {
        "contrasts": [{"factor": "genotype", "numerator": "a", "denominator": "b"}]}}
    msgs = check_design(cfg, s)
    assert any(m["status"] == "FAIL" and "genotype" in m["message"] for m in msgs)


def test_deseq2_results_upload_skips(tmp_path):
    # Uploaded results: no DE model is fit, so the design is not validated.
    s = _samples(tmp_path, ["WT", "MUT"])
    assert check_design({"input": {"type": "deseq2_results"}, "deseq2": _cfg("x", "y", "z")["deseq2"]}, s) == []


def _uploaded_cfg(direction=None):
    inp = {"type": "deseq2_results", "deseq2_results": "config/results.csv"}
    if direction is not None:
        inp["deseq2_results_direction"] = direction
    return {"input": inp}


def test_legacy_uploaded_results_without_direction_fails_before_new_run():
    fails = check_deseq2_results_direction(_uploaded_cfg())
    assert len(fails) == 1
    assert fails[0]["status"] == "FAIL"
    assert "direction provenance" in fails[0]["message"]


def test_uploaded_results_requires_confirmed_distinct_nonblank_direction():
    timestamp = "2026-08-10T12:00:00+03:00"
    blank = check_deseq2_results_direction(_uploaded_cfg({
        "numerator": "", "denominator": "control", "confirmed": True, "confirmed_at": timestamp,
    }))
    same = check_deseq2_results_direction(_uploaded_cfg({
        "numerator": "Control", "denominator": "control", "confirmed": True,
        "confirmed_at": timestamp,
    }))
    unconfirmed = check_deseq2_results_direction(_uploaded_cfg({
        "numerator": "treated", "denominator": "control", "confirmed": False,
        "confirmed_at": timestamp,
    }))
    assert all(any(m["status"] == "FAIL" for m in case) for case in (blank, same, unconfirmed))
    assert any("incomplete" in m["message"] for m in blank)
    assert any("both numerator and denominator" in m["message"] for m in same)
    assert any("not been confirmed" in m["message"] for m in unconfirmed)


@pytest.mark.parametrize("confirmed_at", [None, "", "2026-08-10", "2026-08-10T12:00:00", "not-a-time"])
def test_uploaded_results_requires_timezone_aware_confirmation_time(confirmed_at):
    messages = check_deseq2_results_direction(_uploaded_cfg({
        "numerator": "treated", "denominator": "control", "confirmed": True,
        "confirmed_at": confirmed_at,
    }))
    assert any(m["status"] == "FAIL" and "timezone-aware ISO 8601" in m["message"]
               for m in messages)


@pytest.mark.parametrize("confirmed_at", ["2026-08-10T09:00:00Z", "2026-08-10T12:00:00+03:00"])
def test_uploaded_results_accepts_timezone_aware_confirmation_time(confirmed_at):
    assert check_deseq2_results_direction(_uploaded_cfg({
        "numerator": "treated", "denominator": "control", "confirmed": True,
        "confirmed_at": confirmed_at,
    })) == []


def test_uploaded_results_accepts_confirmed_direction_and_ordinary_modes_ignore_it():
    confirmed = _uploaded_cfg({"numerator": "treated", "denominator": "control", "confirmed": True,
                               "confirmed_at": "2026-08-10T12:00:00+03:00"})
    assert check_deseq2_results_direction(confirmed) == []
    assert check_deseq2_results_direction({"input": {"type": "fastq"}}) == []


def test_header_only_samples_are_valid_only_for_uploaded_results(tmp_path):
    samples = tmp_path / "samples.tsv"
    samples.write_text("sample_id\tcondition\tlayout\tfastq_1\n", encoding="utf-8")
    assert check_samples(_uploaded_cfg(), samples) == []
    ordinary = check_samples({"input": {"type": "fastq"}}, samples)
    assert len(ordinary) == 1
    assert ordinary[0]["status"] == "FAIL"
    assert "no sample rows" in ordinary[0]["message"]


@pytest.mark.parametrize("header", [
    "",
    "sample_id\tcondition\n",
    "condition\tsample_id\tlayout\tfastq_1\n",
    "sample_id\tcondition\tlayout\tfastq_1\textra\n",
])
def test_header_only_uploaded_results_requires_exact_snakefile_safe_schema(tmp_path, header):
    samples = tmp_path / "samples.tsv"
    samples.write_text(header, encoding="utf-8")
    messages = check_samples(_uploaded_cfg(), samples)
    assert any(m["status"] == "FAIL" and "exact minimal schema" in m["message"]
               for m in messages)


def _external_provenance(path: Path) -> dict:
    return {
        "original_basename": "original.csv",
        "imported_at": "2026-08-10T12:00:00+03:00",
        "project_copy": "config/results.csv",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "byte_size": path.stat().st_size,
        "row_count": 1,
        "column_names": ["gene_id", "log2FoldChange", "padj"],
        "gene_id_column": "gene_id",
        "log2fc_column": "log2FoldChange",
        "adjusted_p_column": "padj",
        "upstream_method": "unknown",
        "lfc_shrinkage": "unknown",
        "p_adjustment_method": "unknown",
    }


def test_external_project_copy_provenance_must_be_complete_and_match_bytes(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    project_copy = config_dir / "results.csv"
    project_copy.write_text("gene_id,log2FoldChange,padj\ng1,1.0,0.01\n", encoding="utf-8")
    cfg = _uploaded_cfg()
    cfg["input"]["deseq2_results"] = "config/results.csv"
    cfg["input"]["deseq2_results_provenance"] = _external_provenance(project_copy)
    assert check_deseq2_results_provenance(cfg, base=tmp_path) == []

    project_copy.write_text("gene_id,log2FoldChange,padj\ng1,-1.0,0.01\n", encoding="utf-8")
    messages = check_deseq2_results_provenance(cfg, base=tmp_path)
    assert any("SHA-256 mismatch" in message["message"] for message in messages)


def test_external_project_copy_provenance_rejects_legacy_or_redirected_record(tmp_path):
    project_copy = tmp_path / "results.csv"
    project_copy.write_text("gene_id,log2FoldChange,padj\ng1,1.0,0.01\n", encoding="utf-8")
    cfg = _uploaded_cfg()
    cfg["input"]["deseq2_results"] = str(project_copy)
    missing = check_deseq2_results_provenance(cfg, base=tmp_path)
    assert any("no complete import-time provenance" in message["message"] for message in missing)

    record = _external_provenance(project_copy)
    record["project_copy"] = "config/other.csv"
    cfg["input"]["deseq2_results_provenance"] = record
    redirected = check_deseq2_results_provenance(cfg, base=tmp_path)
    assert any("path differs" in message["message"] for message in redirected)


def test_workflow_provenance_reader_rejects_extra_field_row_shift(tmp_path):
    malformed = tmp_path / "malformed.csv"
    malformed.write_text(
        "gene_id,log2FoldChange,padj\ng1,1,25,0.01\n", encoding="utf-8"
    )
    with pytest.raises(Exception, match="header length|fields|index"):
        validate_project._read_external_table(malformed)


def test_workflow_provenance_reader_matches_gui_cp1252_support(tmp_path):
    table = tmp_path / "cp1252.csv"
    table.write_bytes(
        "gene_id,log2FoldChange,padj,symbol\ng1,1.0,0.01,case–gene\n".encode("cp1252")
    )
    dataframe = validate_project._read_external_table(table)
    assert dataframe.loc[0, "symbol"] == "case–gene"


required_r_packages = validate_project.required_r_packages
_CORE = set(validate_project._CORE_R_PACKAGES)


def test_core_r_packages_cover_hardloaded_figure_stack():
    # The mandatory figures / sample-correlation / set-overlap rules hard-load these on every run;
    # scales in particular is only transitive in the fallback env spec, so it must be probed here.
    assert {"scales", "svglite", "RColorBrewer", "msigdbr"} <= _CORE


def test_required_r_packages_add_conditional_deps_by_config():
    # meta-analysis + limma-voom
    meta = set(required_r_packages({"workflow": {"meta_analysis": True, "de_engine": "limma-voom"}}))
    assert {"metaRNASeq", "metafor", "HTSFilter", "edgeR"} <= meta
    # Salmon route -> tximport (aligner/quantifier live under the workflow section, not 'alignment')
    assert "tximport" in required_r_packages({"workflow": {"aligner": "Salmon"}})
    assert "tximport" in required_r_packages({"workflow": {"quantifier": "Salmon_tximport"}})
    # a nonexistent 'alignment' section must NOT trigger tximport (guards the fixed regression)
    assert "tximport" not in required_r_packages({"alignment": {"aligner": "Salmon"}})
    # g:Profiler route -> gprofiler2
    assert "gprofiler2" in required_r_packages({"enrichment": {"backend": "gprofiler"}})
    assert "gprofiler2" in required_r_packages({"enrichment": {"gprofiler_organism": "scerevisiae"}})
    # GSVA / edgeR engine
    assert "GSVA" in required_r_packages({"workflow": {"gsva": True}})
    assert "edgeR" in required_r_packages({"workflow": {"de_engine": "edgeR"}})
    # microarray CEL -> GEOquery + affy
    micro = set(required_r_packages({"input": {"type": "microarray"}, "microarray": {"source": "affy_cel"}}))
    assert {"GEOquery", "affy"} <= micro


def test_plain_deseq2_run_adds_no_conditional_packages():
    # A plain fastq/DESeq2 run must not require any of the conditional packages (no false FAIL on a
    # lighter env that legitimately lacks, say, gprofiler2 or tximport for that run).
    plain = set(required_r_packages({"workflow": {"de_engine": "DESeq2"}, "input": {"type": "fastq"}}))
    assert not ({"metaRNASeq", "metafor", "HTSFilter", "edgeR", "GSVA", "tximport", "gprofiler2",
                 "GEOquery", "affy"} & plain)
    # de-dup keeps the list unique
    lst = required_r_packages({"workflow": {"meta_analysis": True}})
    assert len(lst) == len(set(lst))


def test_required_r_packages_adds_deseq2_shrinkage_estimator():
    # A count-based DESeq2 run calls lfcShrink; the active estimator (apeglm default / ashr) is a
    # separate package and must be load-tested. 'normal', no shrinkage, deseq2_results, and non-DESeq2
    # engines add nothing.
    assert "apeglm" in required_r_packages({"input": {"type": "fastq"}, "workflow": {"de_engine": "DESeq2"}})
    assert "ashr" in required_r_packages({"input": {"type": "fastq"}, "deseq2": {"shrinkage_method": "ashr"}})
    assert "apeglm" not in required_r_packages({"input": {"type": "deseq2_results"}})
    assert "apeglm" not in required_r_packages({"input": {"type": "fastq"}, "deseq2": {"lfc_shrinkage": False}})
    assert "apeglm" not in required_r_packages({"input": {"type": "fastq"}, "workflow": {"de_engine": "edgeR"}})
    assert not ({"apeglm", "ashr"} & set(required_r_packages({"input": {"type": "fastq"}, "deseq2": {"shrinkage_method": "normal"}})))
