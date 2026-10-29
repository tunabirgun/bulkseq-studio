"""Contracts for the published synthetic meta-analysis example."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re
from statistics import NormalDist

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "docs" / "examples" / "meta"
REPORT = PROJECT / "results" / "reports" / "meta_analysis_report.html"


def _validate_up_row(rows, study_rows) -> None:
    row = next(item for item in rows if item["gene_id"] == "up")
    first = next(item for item in study_rows["A"] if item["gene_id"] == "up")
    second = next(item for item in study_rows["B"] if item["gene_id"] == "up")
    normal = NormalDist()
    p_values = [float(first["pvalue"]), float(second["pvalue"])]
    combined_z = sum(normal.inv_cdf(1 - value) for value in p_values) / math.sqrt(2)
    assert math.isclose(float(row["combined_z"]), combined_z, abs_tol=1e-12)
    assert math.isclose(float(row["combined_pvalue"]), 1 - normal.cdf(combined_z), abs_tol=1e-12)
    effects = [float(first["log2FoldChange"]), float(second["log2FoldChange"])]
    variances = [float(first["lfcSE"]) ** 2, float(second["lfcSE"]) ** 2]
    weights = [1 / value for value in variances]
    estimate = sum(effect * weight for effect, weight in zip(effects, weights)) / sum(weights)
    standard_error = math.sqrt(1 / sum(weights))
    assert math.isclose(float(row["rem_log2FC"]), estimate, abs_tol=1e-12)
    assert math.isclose(float(row["rem_ci_lo"]), estimate - 1.959963984540054 * standard_error, abs_tol=1e-12)
    assert math.isclose(float(row["rem_ci_hi"]), estimate + 1.959963984540054 * standard_error, abs_tol=1e-12)


def test_meta_example_is_a_report_with_truthful_synthetic_scope() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "BulkSeq Studio meta-analysis" in text
    assert "Synthetic teaching example" in text
    assert "not a biological study" in text
    assert "not a count-based DESeq2 analysis" in text
    assert "Synthetic teaching fixture" in text
    assert "per-study DESeq2" not in text


def test_meta_example_reconciles_summary_tables_and_ledger() -> None:
    rows = list(csv.DictReader((PROJECT / "results" / "meta" / "meta_analysis_results.csv").open(encoding="utf-8", newline="")))
    study_rows = {study: list(csv.DictReader((PROJECT / "results" / "meta" / f"per_study_{study}.csv").open(encoding="utf-8", newline=""))) for study in ("A", "B")}
    summary = json.loads((PROJECT / "results" / "reports" / "meta_analysis_summary.json").read_text(encoding="utf-8"))
    ledger = json.loads((PROJECT / "results" / "meta" / "meta_eligibility.json").read_text(encoding="utf-8"))
    assert len(rows) == summary["n_shared_genes"] == ledger["genes"]["complete_case_retained"]
    assert sum(row["meta_sig"].lower() == "true" for row in rows) == summary["n_meta_sig"]
    assert ledger["method"] == "combined_concordance_and_pooled_full_family_bh_v1"
    assert ledger["families"]["combined"]["size"] == 1
    assert ledger["families"]["pooled"]["size"] == len(rows)
    _validate_up_row(rows, study_rows)
    for study in ("A", "B"):
        with (PROJECT / "results" / "meta" / f"per_study_{study}.csv").open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        assert len(header) == len(set(header))


def test_meta_example_numeric_gate_rejects_a_changed_pooled_effect() -> None:
    rows = list(csv.DictReader((PROJECT / "results" / "meta" / "meta_analysis_results.csv").open(encoding="utf-8", newline="")))
    study_rows = {study: list(csv.DictReader((PROJECT / "results" / "meta" / f"per_study_{study}.csv").open(encoding="utf-8", newline=""))) for study in ("A", "B")}
    next(item for item in rows if item["gene_id"] == "up")["rem_log2FC"] = "0.64"
    with pytest.raises(AssertionError):
        _validate_up_row(rows, study_rows)


@pytest.mark.parametrize("name", ["meta_forest.svg", "meta_integration_gain.svg"])
def test_meta_example_figures_are_real_svg_with_descriptive_titles(name: str) -> None:
    text = (PROJECT / "results" / "figures" / name).read_text(encoding="utf-8")
    assert text.startswith("<svg")
    assert "<title>" in text and "Synthetic" in text
    if name == "meta_forest.svg":
        assert "Square area is proportional to fixed-effect inverse-variance weight" in text
        widths = re.findall(r'<rect x="[^"]+" y="[^"]+" width="([0-9.]+)" height="\1"/>', text)
        assert widths == ["20.0", "10.0"]
    png = PROJECT / "results" / "figures" / Path(name).with_suffix(".png")
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
