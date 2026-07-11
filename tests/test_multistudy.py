from __future__ import annotations

import pandas as pd

from app.core.sra_metadata import metadata_to_samples
from app.core.metadata import (
    dataset_condition_crosstab,
    detect_dataset_confounding,
    detect_multistudy_organism_mismatch,
    validate_metadata,
)


def _ena(run, ds, title):
    return {"run_accession": run, "library_layout": "SINGLE", "fastq_ftp": f"ftp/{run}.fastq.gz",
            "fastq_md5": "a", "fastq_bytes": "1000", "base_count": "1000",
            "scientific_name": "Homo sapiens", "sample_title": title, "dataset": ds}


def test_multi_study_fetch_keeps_dataset_column():
    meta = pd.DataFrame([_ena("SRR1", "GSE1", "c1"), _ena("SRR2", "GSE2", "t1")])
    s = metadata_to_samples(meta)
    assert "dataset" in s.columns
    assert set(s["dataset"]) == {"GSE1", "GSE2"}


def test_single_study_fetch_omits_dataset_column():
    # byte-identical single-study behaviour: no extra column
    meta = pd.DataFrame([_ena("SRR1", "GSE1", "c1"), _ena("SRR2", "GSE1", "t1")])
    assert "dataset" not in metadata_to_samples(meta).columns


def _samples(rows):
    return pd.DataFrame([{"sample_id": a, "dataset": b, "condition": c} for a, b, c in rows])


def test_ok_when_one_dataset_spans_both_arms():
    df = _samples([("s1", "D1", "A"), ("s2", "D1", "B"), ("s3", "D2", "A"), ("s4", "D2", "B")])
    assert detect_dataset_confounding(df) == []


def test_ok_with_single_arm_extra_dataset():
    # D1 has both arms -> estimable; D2 single-arm is admissible here (dropped in meta later)
    df = _samples([("s1", "D1", "A"), ("s2", "D1", "B"), ("s3", "D2", "A")])
    assert detect_dataset_confounding(df) == []


def test_blocks_when_groups_split_across_studies():
    out = detect_dataset_confounding(_samples([("s1", "D1", "A"), ("s2", "D2", "B")]))
    assert out and out[0]["status"] == "FAIL"


def test_blocks_rank_deficient_case_batch_test_misses():
    # D1={A}, D2={B}, D3={A}: rank-deficient; no dataset spans both arms -> must FAIL.
    out = detect_dataset_confounding(_samples([("s1", "D1", "A"), ("s2", "D2", "B"), ("s3", "D3", "A")]))
    assert out and out[0]["status"] == "FAIL"


def test_single_dataset_never_confounded():
    assert detect_dataset_confounding(_samples([("s1", "D1", "A"), ("s2", "D1", "B")])) == []


def test_explicit_contrast_respected():
    df = _samples([("s1", "D1", "A"), ("s2", "D1", "C"), ("s3", "D2", "B"), ("s4", "D2", "C")])
    assert detect_dataset_confounding(df, ("A", "B"))[0]["status"] == "FAIL"


def test_crosstab():
    ct = dataset_condition_crosstab(_samples([("s1", "D1", "A"), ("s2", "D1", "B"), ("s3", "D2", "A")]))
    assert ct is not None and int(ct.loc["D1", "A"]) == 1


def test_gate_fires_for_three_level_confounded_with_contrast():
    # D1={A,C}, D2={B,C}; contrast A vs B is split across studies -> FAIL. This is the >2-level case
    # the gate silently missed when validate_metadata was called without the contrast argument.
    df = pd.DataFrame([
        {"sample_id": "s1", "dataset": "D1", "condition": "A", "layout": "single", "fastq_1": ""},
        {"sample_id": "s2", "dataset": "D1", "condition": "C", "layout": "single", "fastq_1": ""},
        {"sample_id": "s3", "dataset": "D2", "condition": "B", "layout": "single", "fastq_1": ""},
        {"sample_id": "s4", "dataset": "D2", "condition": "C", "layout": "single", "fastq_1": ""},
    ])
    msgs = validate_metadata(df, allow_pending_sra=True, contrast=("A", "B"))
    assert any(m["status"] == "FAIL" and "split across studies" in m["message"] for m in msgs)


def test_organism_mismatch_blocks_and_same_ok():
    mixed = pd.DataFrame([
        {"sample_id": "s1", "dataset": "D1", "condition": "A", "organism": "Homo sapiens"},
        {"sample_id": "s2", "dataset": "D2", "condition": "B", "organism": "Mus musculus"}])
    out = detect_multistudy_organism_mismatch(mixed)
    assert out and out[0]["status"] == "FAIL"
    same = mixed.copy(); same.loc[1, "organism"] = "Homo sapiens"
    assert detect_multistudy_organism_mismatch(same) == []
    # case-variant spelling of the SAME organism must not raise a false mismatch
    casev = mixed.copy(); casev.loc[1, "organism"] = "homo sapiens"
    assert detect_multistudy_organism_mismatch(casev) == []
    # single study never blocks
    solo = same.copy(); solo.loc[1, "dataset"] = "D1"
    assert detect_multistudy_organism_mismatch(solo) == []


def test_validate_metadata_wires_dataset_gate():
    # A confounded multi-study sheet must FAIL validation (the gate is connected, not orphan).
    df = pd.DataFrame([
        {"sample_id": "s1", "condition": "A", "layout": "single", "fastq_1": "", "dataset": "D1"},
        {"sample_id": "s2", "condition": "B", "layout": "single", "fastq_1": "", "dataset": "D2"},
    ])
    msgs = validate_metadata(df, allow_pending_sra=True, contrast=("A", "B"))
    assert any(m["status"] == "FAIL" and "split across studies" in m["message"] for m in msgs)


def test_confounding_gate_ignores_absent_contrast_arms():
    # A freshly fetched multi-study sheet (condition still 'unknown') must NOT get a spurious
    # confounding FAIL just because the DE tab still holds the default treated/control contrast.
    df = _samples([("s1", "D1", "unknown"), ("s2", "D2", "unknown")])
    assert detect_dataset_confounding(df, ("treated", "control")) == []


def test_unsafe_dataset_name_fails():
    from app.core.metadata import detect_unsafe_dataset_names
    bad = _samples([("s1", "Control cohort", "A"), ("s2", "D2", "B")])
    out = detect_unsafe_dataset_names(bad)
    assert out and out[0]["status"] == "FAIL" and "Control cohort" in out[0]["message"]
    assert detect_unsafe_dataset_names(_samples([("s1", "D1", "A"), ("s2", "D2", "B")])) == []
    # A single-study sheet must NOT block on an unsafe name (the dataset column is ignored downstream);
    # this aligns the GUI gate with its WSL mirror, which checks names only after the >1-study early-out.
    solo_bad = _samples([("s1", "Bad Name", "A"), ("s2", "Bad Name", "B")])
    assert detect_unsafe_dataset_names(solo_bad) == []


def test_multistudy_admissibility_warns_when_under_two_studies():
    from app.core.metadata import detect_multistudy_admissibility
    # D1 has both arms x2, D2 is single-arm -> only 1 admissible study -> WARNING.
    one = _samples([("s1", "D1", "A"), ("s2", "D1", "A"), ("s3", "D1", "B"), ("s4", "D1", "B"),
                    ("s5", "D2", "A"), ("s6", "D2", "A")])
    out = detect_multistudy_admissibility(one, ("A", "B"))
    assert out and out[0]["status"] == "WARNING"
    # Both studies admissible -> no warning.
    two = _samples([("s1", "D1", "A"), ("s2", "D1", "A"), ("s3", "D1", "B"), ("s4", "D1", "B"),
                    ("s5", "D2", "A"), ("s6", "D2", "A"), ("s7", "D2", "B"), ("s8", "D2", "B")])
    assert detect_multistudy_admissibility(two, ("A", "B")) == []


def test_admissibility_defers_to_confounding_on_full_split():
    from app.core.metadata import detect_multistudy_admissibility
    # Fully split (no study has both arms) is study-confounding, owned by detect_dataset_confounding's
    # hard FAIL; admissibility must stay silent so the GUI does not show a redundant WARNING alongside.
    split = _samples([("s1", "D1", "A"), ("s2", "D1", "A"), ("s3", "D2", "B"), ("s4", "D2", "B")])
    assert detect_multistudy_admissibility(split, ("A", "B")) == []
    # And the confounding gate does FAIL that same sheet.
    assert detect_dataset_confounding(split, ("A", "B"))[0]["status"] == "FAIL"
