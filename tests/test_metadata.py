from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd

import importlib.util

from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import (
    detect_batch_condition_confounding,
    load_metadata,
    non_numeric_matrix_tokens,
    save_metadata,
    validate_metadata,
)


BASE = Path("manual_test_metadata")

_spec = importlib.util.spec_from_file_location(
    "validate_project_for_metadata",
    Path(__file__).resolve().parent.parent / "workflow" / "scripts" / "validate_project.py")
_validate_project = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validate_project)
check_samples = _validate_project.check_samples


def test_detect_paired_fastq() -> None:
    base = BASE / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    r1 = base / "sampleA_R1.fastq.gz"
    r2 = base / "sampleA_R2.fastq.gz"
    r1.write_text("", encoding="utf-8")
    r2.write_text("", encoding="utf-8")
    rows = detect_fastq_inputs([r1, r2])
    assert rows[0]["layout"] == "paired"
    assert rows[0]["fastq_2"] == str(r2)


def test_metadata_duplicate_fails() -> None:
    base = BASE / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    fastq = base / "a.fastq"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    df = pd.DataFrame(
        [
            {"sample_id": "s1", "condition": "control", "layout": "single", "fastq_1": str(fastq)},
            {"sample_id": "s1", "condition": "treated", "layout": "single", "fastq_1": str(fastq)},
        ]
    )
    messages = validate_metadata(df)
    assert any(m["status"] == "FAIL" and "Duplicate" in m["message"] for m in messages)


def test_missing_design_variable_fails() -> None:
    df = pd.DataFrame(
        [{"sample_id": "s1", "condition": "control", "layout": "single", "fastq_1": "x.fq"}]
    )
    messages = validate_metadata(df, allow_pending_sra=True, design_variables=["batch", "condition"])
    assert any(m["status"] == "FAIL" and "batch" in m["message"] for m in messages)


def test_batch_condition_confounding_flagged() -> None:
    df = pd.DataFrame(
        [
            {"sample_id": "s1", "condition": "control", "batch": "b1"},
            {"sample_id": "s2", "condition": "control", "batch": "b1"},
            {"sample_id": "s3", "condition": "treated", "batch": "b2"},
            {"sample_id": "s4", "condition": "treated", "batch": "b2"},
        ]
    )
    messages = detect_batch_condition_confounding(df)
    assert messages and messages[0]["status"] == "REVIEW_REQUIRED"


def test_single_replicate_warns() -> None:
    df = pd.DataFrame(
        [
            {"sample_id": "s1", "condition": "control", "layout": "single", "fastq_1": "a.fq"},
            {"sample_id": "s2", "condition": "treated", "layout": "single", "fastq_1": "b.fq"},
        ]
    )
    messages = validate_metadata(df, allow_pending_sra=True)
    assert any(m["status"] == "WARNING" and "two biological replicates" in m["message"] for m in messages)


def test_non_numeric_matrix_tokens_rejects_decimal_commas_and_placeholders() -> None:
    clean = pd.DataFrame({"S1": ["8.21", "12.9", "NA", ""], "S2": ["1e3", "-0.5", "7", "nan"]})
    assert non_numeric_matrix_tokens(clean) == []
    dirty = pd.DataFrame({"S1": ["8,21", "12.9"], "S2": ["NULL", "n/a"]})
    assert non_numeric_matrix_tokens(dirty) == ["8,21", "NULL", "n/a"]
    assert non_numeric_matrix_tokens(dirty, limit=1) == ["8,21"]


# --- optional library_name column (0.31.0) ----------------------------------------------

_LEGACY_COLUMNS = ["sample_id", "condition", "layout", "fastq_1", "fastq_2", "replicate", "batch"]
_LEGACY_ROWS = [
    ["s1", "control", "single", "a.fq", "", "1", "b1"],
    ["s2", "treated", "single", "b.fq", "", "1", "b2"],
]


def _legacy_sheet(path: Path) -> bytes:
    """A pre-0.31.0 sample sheet on disk, serialised exactly as save_metadata writes one."""
    df = pd.DataFrame(_LEGACY_ROWS, columns=_LEGACY_COLUMNS)
    save_metadata(df, path)
    return path.read_bytes()


def test_legacy_sheet_round_trips_byte_identically(tmp_path: Path) -> None:
    path = tmp_path / "samples.tsv"
    original = _legacy_sheet(path)
    before = path.stat().st_mtime_ns
    save_metadata(load_metadata(path), path)
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before


def test_blank_library_name_does_not_rewrite_a_legacy_sheet(tmp_path: Path) -> None:
    # The Resume trap: the interface offers library_name to every project, so a legacy sheet
    # reaches save_metadata carrying an all-blank column. Rewriting it bumps samples.tsv's
    # mtime and makes Snakemake rebuild from alignment instead of resuming.
    path = tmp_path / "samples.tsv"
    original = _legacy_sheet(path)
    before = path.stat().st_mtime_ns
    df = load_metadata(path)
    df.insert(1, "library_name", ["", "   "])
    save_metadata(df, path)
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before


def test_library_name_is_kept_when_any_row_has_one(tmp_path: Path) -> None:
    path = tmp_path / "samples.tsv"
    _legacy_sheet(path)
    df = load_metadata(path)
    df.insert(1, "library_name", ["Whole blood lib", ""])  # partially filled
    save_metadata(df, path)
    written = path.read_bytes()
    assert written.splitlines()[0].split(b"	")[1] == b"library_name"
    reloaded = load_metadata(path)
    assert list(reloaded["library_name"]) == ["Whole blood lib", ""]
    before = path.stat().st_mtime_ns
    save_metadata(reloaded, path)
    assert path.read_bytes() == written and path.stat().st_mtime_ns == before


def test_header_only_results_sheet_never_gains_library_name(tmp_path: Path) -> None:
    # A results-only project's header-only sheet must stay the exact minimal schema
    # workflow/scripts/validate_project.py accepts.
    path = tmp_path / "samples.tsv"
    save_metadata(pd.DataFrame(columns=["sample_id", "library_name", "condition",
                                        "layout", "fastq_1"]), path)
    assert path.read_text(encoding="utf-8").splitlines() == ["sample_id	condition	layout	fastq_1"]
    assert check_samples({"input": {"type": "deseq2_results"}}, path) == []


def test_duplicate_and_missing_library_names_pass_validation() -> None:
    rows = [
        {"sample_id": "s1", "library_name": "Pool A", "condition": "control",
         "layout": "single", "fastq_1": "a.fq"},
        {"sample_id": "s2", "library_name": "Pool A", "condition": "control",
         "layout": "single", "fastq_1": "b.fq"},
        {"sample_id": "s3", "library_name": "", "condition": "control",
         "layout": "single", "fastq_1": "c.fq"},
        {"sample_id": "s4", "library_name": "Pool A", "condition": "treated",
         "layout": "single", "fastq_1": "d.fq"},
        {"sample_id": "s5", "library_name": "", "condition": "treated",
         "layout": "single", "fastq_1": "e.fq"},
        {"sample_id": "s6", "library_name": "Pool A", "condition": "treated",
         "layout": "single", "fastq_1": "f.fq"},
    ]
    messages = validate_metadata(pd.DataFrame(rows), allow_pending_sra=True)
    assert messages == [{"status": "PASS", "message": "Metadata passed validation."}]
    # An existing project's sheet without the column validates to exactly the same verdict.
    legacy = pd.DataFrame([{k: v for k, v in row.items() if k != "library_name"} for row in rows])
    assert validate_metadata(legacy, allow_pending_sra=True) == messages
