"""Contracts for the generated synthetic standard report."""
from pathlib import Path
import csv
import json
import math
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "docs" / "examples" / "standard"


def test_standard_example_report_matches_renderer_inputs() -> None:
    report = PROJECT / "results" / "reports" / "results_report.html"
    rows = list(csv.DictReader((PROJECT / "results" / "deseq2" / "deseq2_results.csv").open(encoding="utf-8")))
    assert len(rows) == 450
    source = report.read_text(encoding="utf-8")
    assert "Synthetic standard analysis report" in source
    assert "not a completed biological study" in source
    assert "Results report" in source
    assert "data:image/svg+xml;base64," in source


def test_standard_example_uses_size_factor_normalized_base_mean() -> None:
    rows = list(csv.DictReader((PROJECT / "results" / "deseq2" / "deseq2_results.csv").open(encoding="utf-8")))
    model = json.loads((ROOT / "docs_src" / "model-examples.json").read_text(encoding="utf-8"))
    counts = dict(zip(model["genes"], model["counts"], strict=True))
    retained = {entry["id"] for entry in model["models"]["deseq2"]}
    geometric = {gene: math.prod(values) ** (1 / len(values)) for gene, values in counts.items() if gene in retained and all(values)}
    factors = []
    for index in range(len(model["samples"])):
        factors.append(math.exp(statistics.median(math.log(counts[gene][index] / geometric[gene]) for gene in geometric)))
    first = rows[0]
    expected = sum(count / factor for count, factor in zip(counts[first["gene_id"]], factors, strict=True)) / len(factors)
    assert abs(float(first["baseMean"]) - expected) < 1e-12


def test_standard_example_result_order_and_hero_are_padj_ordered() -> None:
    rows = list(csv.DictReader((PROJECT / "results" / "deseq2" / "deseq2_results.csv").open(encoding="utf-8")))
    assert [float(row["padj"]) for row in rows] == sorted(float(row["padj"]) for row in rows)
    source = (PROJECT / "results" / "reports" / "results_report.html").read_text(encoding="utf-8")
    top_up = next(row["gene_id"] for row in rows if float(row["log2FoldChange"]) > 0)
    top_down = next(row["gene_id"] for row in rows if float(row["log2FoldChange"]) < 0)
    assert f"<i>{top_up}</i>" in source and f"<i>{top_down}</i>" in source


def test_standard_example_rebuild_is_deterministic(tmp_path) -> None:
    script = ROOT / "docs_src" / "reproduction" / "06_standard_report.py"
    outputs = [tmp_path / "first" / "standard", tmp_path / "second" / "standard"]
    for output in outputs:
        subprocess.run([sys.executable, str(script), "--output", str(output)], check=True)
    first = {path.relative_to(outputs[0]): path.read_bytes() for path in outputs[0].rglob("*") if path.is_file()}
    second = {path.relative_to(outputs[1]): path.read_bytes() for path in outputs[1].rglob("*") if path.is_file()}
    assert first == second
    report = outputs[0] / "results" / "reports" / "results_report.html"
    assert report.exists() and report.stat().st_size > 10_000
    run = json.loads((outputs[0] / "results" / "reports" / "run_summary.json").read_text(encoding="utf-8"))
    assert run["input"]["type"] == "count_matrix"
