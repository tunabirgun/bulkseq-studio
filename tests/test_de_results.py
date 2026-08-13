from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.de_results import (
    DETableValidationError,
    provenance_payload,
    validate_de_results_table,
    validate_recorded_project_copy,
)


def _write_table(path: Path, **overrides: list[object]) -> Path:
    data: dict[str, list[object]] = {
        "gene_id": ["g1", "g2"],
        "log2FoldChange": [1.5, -0.25],
        "padj": [0.01, 0.8],
    }
    data.update(overrides)
    pd.DataFrame(data).to_csv(path, index=False)
    return path


def test_full_validator_accepts_named_and_r_rowname_gene_schema(tmp_path: Path) -> None:
    named = validate_de_results_table(_write_table(tmp_path / "named.csv"))
    rowname_path = tmp_path / "rownames.tsv"
    pd.DataFrame({
        "Unnamed: 0": ["g1", "g2"],
        "logFC": ["1.0", "-2e-1"],
        "FDR": ["0", "1"],
    }).to_csv(rowname_path, sep="\t", index=False)
    rownamed = validate_de_results_table(rowname_path)

    assert named.gene_id_column == "gene_id"
    assert rownamed.gene_id_column == "Unnamed: 0"
    assert rownamed.log2fc_column == "logFC"
    assert rownamed.adjusted_p_column == "FDR"
    assert rownamed.row_count == 2


def test_full_validator_rejects_header_only_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("gene_id,log2FoldChange,padj\n", encoding="utf-8")
    with pytest.raises(DETableValidationError, match="no data rows"):
        validate_de_results_table(path)


def test_full_validator_rejects_invalid_value_after_preview_rows(tmp_path: Path) -> None:
    path = tmp_path / "late-invalid.csv"
    _write_table(
        path,
        gene_id=[f"g{i}" for i in range(7)],
        log2FoldChange=["1"] * 6 + ["1.25unexpected"],
        padj=["0.05"] * 7,
    )
    with pytest.raises(DETableValidationError, match="complete numeric token"):
        validate_de_results_table(path)


def test_full_validator_rejects_extra_field_instead_of_shifting_columns(tmp_path: Path) -> None:
    path = tmp_path / "extra-field.csv"
    path.write_text(
        "gene_id,log2FoldChange,padj\n"
        "g1,1,25,0.01\n",
        encoding="utf-8",
    )
    with pytest.raises(DETableValidationError, match="Could not read"):
        validate_de_results_table(path)


@pytest.mark.parametrize(
    ("field", "values", "message"),
    [
        ("log2FoldChange", ["Inf", "1"], "finite"),
        ("padj", ["NaN", "0.1"], "non-finite"),
        ("padj", ["-0.01", "0.1"], r"within \[0, 1\]"),
        ("padj", ["1.01", "0.1"], r"within \[0, 1\]"),
    ],
)
def test_full_validator_rejects_nonfinite_or_out_of_range_values(
    tmp_path: Path,
    field: str,
    values: list[str],
    message: str,
) -> None:
    path = _write_table(tmp_path / f"bad-{field}.csv", **{field: values})
    with pytest.raises(DETableValidationError, match=message):
        validate_de_results_table(path)


def test_full_validator_allows_canonical_missing_rows_when_finite_results_remain(
    tmp_path: Path,
) -> None:
    path = _write_table(
        tmp_path / "canonical-missing.csv",
        gene_id=["g1", "g2", "g3"],
        log2FoldChange=["1.0", "", "NA"],
        padj=["0.05", "NA", ""],
    )
    validated = validate_de_results_table(path)
    assert validated.row_count == 3


@pytest.mark.parametrize("field", ["log2FoldChange", "padj"])
def test_full_validator_requires_at_least_one_finite_required_value(
    tmp_path: Path,
    field: str,
) -> None:
    path = _write_table(tmp_path / f"all-missing-{field}.csv", **{field: ["", "NA"]})
    with pytest.raises(DETableValidationError, match="at least one finite numeric value"):
        validate_de_results_table(path)


def test_full_validator_rejects_noncanonical_missing_words(tmp_path: Path) -> None:
    path = _write_table(tmp_path / "not-available.csv", padj=["N/A", "0.1"])
    with pytest.raises(DETableValidationError, match="complete numeric token"):
        validate_de_results_table(path)


@pytest.mark.parametrize(
    ("genes", "message"),
    [
        (["g1", "  "], "must not be blank"),
        (["g1", "bad id"], "whitespace or control"),
        (["g1", "g1"], "must be unique"),
    ],
)
def test_full_validator_rejects_blank_or_duplicate_gene_ids(
    tmp_path: Path,
    genes: list[str],
    message: str,
) -> None:
    path = _write_table(tmp_path / "bad-genes.csv", gene_id=genes)
    with pytest.raises(DETableValidationError, match=message):
        validate_de_results_table(path)


def test_project_copy_check_detects_checksum_and_schema_drift(tmp_path: Path) -> None:
    path = _write_table(tmp_path / "project-copy.csv")
    imported = validate_de_results_table(path)
    record = provenance_payload(
        imported,
        original_basename=r"C:\private\study-results.csv",
        imported_at="2026-08-10T12:00:00+03:00",
        project_copy="config/deseq2_results.csv",
    )
    assert record["original_basename"] == "study-results.csv"
    _, initial_errors = validate_recorded_project_copy(
        path, record, configured_project_copy="config/deseq2_results.csv")
    assert initial_errors == []

    # A scientifically valid edit still invalidates the immutable import record.
    _write_table(path, log2FoldChange=[2.0, -0.25])
    _, content_errors = validate_recorded_project_copy(
        path, record, configured_project_copy="config/deseq2_results.csv")
    assert any("SHA-256 mismatch" in error for error in content_errors)

    pd.DataFrame({
        "gene_id": ["g1", "g2"],
        "logFC": [2.0, -0.25],
        "FDR": [0.01, 0.8],
    }).to_csv(path, index=False)
    _, schema_errors = validate_recorded_project_copy(
        path, record, configured_project_copy="config/deseq2_results.csv")
    assert any("column schema changed" in error for error in schema_errors)
    assert any("log2 fold-change column" in error for error in schema_errors)


def test_project_copy_check_rejects_legacy_record_without_integrity_fields(tmp_path: Path) -> None:
    path = _write_table(tmp_path / "legacy.csv")
    validated, errors = validate_recorded_project_copy(path, {})
    assert validated is not None
    assert any("no complete import-time provenance" in error for error in errors)
