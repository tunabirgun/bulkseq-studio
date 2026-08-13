from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config_models import (
    Deseq2ResultsDirectionProvenance,
    Deseq2ResultsFileProvenance,
    InputConfig,
)


def test_deseq2_results_input_mode_roundtrips() -> None:
    cfg = InputConfig(type="deseq2_results", deseq2_results="config/deseq2_results.csv")
    assert cfg.type == "deseq2_results"
    assert cfg.deseq2_results == "config/deseq2_results.csv"
    dumped = cfg.model_dump()
    assert dumped["type"] == "deseq2_results"
    assert dumped["deseq2_results"] == "config/deseq2_results.csv"
    assert InputConfig.model_validate(dumped).type == "deseq2_results"


def test_deseq2_results_direction_provenance_is_backward_compatible() -> None:
    # Existing projects predate this field and must still open. A later workflow check,
    # not Pydantic loading, blocks an unconfirmed new run.
    legacy = InputConfig(type="deseq2_results", deseq2_results="config/results.csv")
    assert legacy.deseq2_results_direction.confirmed is False
    assert legacy.deseq2_results_direction.numerator is None
    assert legacy.deseq2_results_direction.denominator is None


def test_confirmed_deseq2_results_direction_roundtrips() -> None:
    cfg = InputConfig.model_validate({
        "type": "deseq2_results",
        "deseq2_results": "config/results.csv",
        "deseq2_results_direction": {
            "numerator": " treated ", "denominator": "control ",
            "confirmed": True, "confirmed_at": "2026-08-10T12:00:00+03:00",
        },
    })
    direction = cfg.deseq2_results_direction
    assert (direction.numerator, direction.denominator, direction.confirmed) == ("treated", "control", True)
    assert InputConfig.model_validate(cfg.model_dump(mode="json")).deseq2_results_direction == direction


@pytest.mark.parametrize(
    "record",
    [
        {"numerator": "", "denominator": "control", "confirmed": True,
         "confirmed_at": "2026-08-10T12:00:00+03:00"},
        {"numerator": "Control", "denominator": "control", "confirmed": True,
         "confirmed_at": "2026-08-10T12:00:00+03:00"},
        {"numerator": "case\ncohort", "denominator": "control", "confirmed": True,
         "confirmed_at": "2026-08-10T12:00:00+03:00"},
        {"numerator": "case", "denominator": "control", "confirmed": True,
         "confirmed_at": "2026-08-10T12:00:00"},
    ],
)
def test_confirmed_direction_rejects_incomplete_ambiguous_or_naive_records(record: dict) -> None:
    with pytest.raises(ValidationError):
        Deseq2ResultsDirectionProvenance.model_validate(record)


def test_unconfirmed_legacy_direction_remains_loadable() -> None:
    legacy = Deseq2ResultsDirectionProvenance(
        numerator="same", denominator="same", confirmed=False, confirmed_at="old local note")
    assert legacy.confirmed is False


def test_external_results_file_provenance_roundtrips_without_source_directory() -> None:
    record = Deseq2ResultsFileProvenance(
        original_basename=r"C:\private-study\external.csv",
        imported_at="2026-08-10T12:00:00+03:00",
        project_copy="config/deseq2_results.csv",
        sha256="a" * 64,
        byte_size=123,
        row_count=2,
        column_names=["gene_id", "log2FoldChange", "padj"],
        gene_id_column="gene_id",
        log2fc_column="log2FoldChange",
        adjusted_p_column="padj",
        upstream_method="edgeR",
        lfc_shrinkage="not_applied",
        p_adjustment_method="Benjamini-Hochberg",
    )
    assert record.original_basename == "external.csv"
    assert "private-study" not in str(record.model_dump(mode="json"))


def test_default_input_has_no_deseq2_results() -> None:
    cfg = InputConfig()
    assert cfg.type == "fastq"
    assert cfg.deseq2_results is None


def test_snakefile_keeps_first_sample_safe_for_header_only_uploaded_results() -> None:
    # This is a parse-time regression guard: sample-dependent rules must not index an
    # empty list on the results-only route, while all ordinary routes retain the guard.
    snakefile = (Path(__file__).resolve().parents[1] / "workflow" / "Snakefile").read_text(encoding="utf-8")
    assert "if not SAMPLES and not _DE_RESULTS_AT_PARSE:" in snakefile
    assert "FIRST_SAMPLE = SAMPLES[0] if SAMPLES else None" in snakefile
