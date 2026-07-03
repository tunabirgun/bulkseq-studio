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
