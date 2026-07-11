from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "validate_project", Path(__file__).resolve().parent.parent / "workflow" / "scripts" / "validate_project.py")
validate_project = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_project)
check_design = validate_project.check_design


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
