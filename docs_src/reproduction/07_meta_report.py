"""Create a synthetic, method-checked cross-study report example."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE_FIXTURE = ROOT / "tests" / "meta_pooled_methods.R"
DEFAULT_OUTPUT = ROOT / "docs" / "examples" / "meta"


def svg_document(title: str, body: str, width: int = 900, height: int = 560) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img">'
        '<title>%s</title><rect width="100%%" height="100%%" fill="white"/>'
        '<g fill="black" stroke="black" font-family="Times New Roman,serif">%s</g></svg>'
    ) % (width, height, width, height, html.escape(title), body)


def rasterize(svg: Path) -> None:
    subprocess.run([
        "rsvg-convert", "--format", "png", "--width", "2700", "--height", "1680",
        "--output", str(svg.with_suffix(".png")), str(svg),
    ], check=True)


def numeric(value: str) -> float:
    return float(value)


def finite(value: str) -> bool:
    try:
        return math.isfinite(numeric(value))
    except (TypeError, ValueError):
        return False


def application_version() -> str:
    source = (ROOT / "app" / "constants.py").read_text(encoding="utf-8")
    match = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", source, re.MULTILINE)
    if not match:
        raise RuntimeError("APP_VERSION is unavailable")
    return match.group(1)


def forest_svg(rows: list[dict[str, str]], study_rows: dict[str, list[dict[str, str]]]) -> str:
    row = next(item for item in rows if item["gene_id"] == "up")
    estimates = [(study, next(item for item in records if item["gene_id"] == "up"))
                 for study, records in study_rows.items()]
    bounds = []
    for _, item in estimates:
        estimate, se = numeric(item["log2FoldChange"]), numeric(item["lfcSE"])
        bounds.extend((estimate - 1.96 * se, estimate + 1.96 * se))
    bounds.extend((numeric(row["rem_ci_lo"]), numeric(row["rem_ci_hi"])))
    lower, upper = math.floor(min(0, *bounds)), math.ceil(max(0, *bounds))
    x = lambda value: 190 + 470 * (value - lower) / (upper - lower)
    body = ['<text x="36" y="44" font-size="24">Pooled effect for synthetic feature up</text>']
    zero = x(0)
    body.append(f'<path d="M{zero:.1f} 74V390" stroke="#777" stroke-dasharray="5 5"/>')
    inverse_variance = [1 / numeric(item["lfcSE"]) ** 2 for _, item in estimates]
    largest_weight = max(inverse_variance)
    for index, (study, item) in enumerate(estimates):
        y = 122 + index * 88
        estimate, se = numeric(item["log2FoldChange"]), numeric(item["lfcSE"])
        low, high = estimate - 1.96 * se, estimate + 1.96 * se
        side = 20 * math.sqrt(inverse_variance[index] / largest_weight)
        body += [
            f'<text x="36" y="{y + 6}" font-size="18">Study {html.escape(study)}</text>',
            f'<path d="M{x(low):.1f} {y}H{x(high):.1f}" stroke-width="2"/>',
            f'<rect x="{x(estimate) - side / 2:.1f}" y="{y - side / 2:.1f}" width="{side:.1f}" height="{side:.1f}"/>',
            f'<text x="860" y="{y + 6}" text-anchor="end" font-size="15">{estimate:.2f} [{low:.2f}, {high:.2f}]</text>',
        ]
    y = 330
    pooled, low, high = (numeric(row[key]) for key in ("rem_log2FC", "rem_ci_lo", "rem_ci_hi"))
    body += [
        '<path d="M36 292H864" stroke="#999"/>',
        f'<text x="36" y="{y + 6}" font-size="18">Common effect</text>',
        f'<polygon points="{x(low):.1f},{y} {x(pooled):.1f},{y - 15} {x(high):.1f},{y} {x(pooled):.1f},{y + 15}" fill="#3158c9" stroke="#3158c9"/>',
        f'<text x="860" y="{y + 6}" text-anchor="end" font-size="15">{pooled:.2f} [{low:.2f}, {high:.2f}]</text>',
        '<path d="M190 408H660" stroke="#555"/>',
    ]
    for value in range(lower, upper + 1):
        body.append(f'<text x="{x(value):.1f}" y="432" text-anchor="middle" font-size="14">{value}</text>')
    body += [
        '<text x="425" y="468" text-anchor="middle" font-size="16">log2 fold change (case vs control), 95% confidence intervals</text>',
        '<text x="36" y="508" font-size="14">Synthetic study-level fixture. Squares are study estimates; diamond is the fixed-effect estimate.</text>',
        '<text x="36" y="530" font-size="14">Square area is proportional to fixed-effect inverse-variance weight. This is a pooled-effect display, not a combined-p significance display.</text>',
    ]
    return svg_document("Pooled effect for synthetic feature up", "".join(body))


def gain_svg(rows: list[dict[str, str]]) -> str:
    alpha = 0.05
    combined = [item["meta_sig"].lower() == "true" for item in rows]
    per_study = [any(finite(item[key]) and numeric(item[key]) < alpha for key in item if key.endswith("_padj")) for item in rows]
    counts = [
        sum(a and not b for a, b in zip(combined, per_study)),
        sum(a and b for a, b in zip(combined, per_study)),
        sum(not a and b for a, b in zip(combined, per_study)),
        sum(not a and not b for a, b in zip(combined, per_study)),
    ]
    labels = ["Combined only", "Both", "Per-study only", "Neither"]
    body = ['<text x="36" y="44" font-size="24">Calls in the synthetic fixture</text>']
    for index, (label, count) in enumerate(zip(labels, counts)):
        x = 70 + index * 190
        height = count * 170
        body += [
            f'<rect x="{x}" y="{390 - height}" width="92" height="{height}" fill="#3158c9"/>',
            f'<text x="{x + 46}" y="{376 - height}" text-anchor="middle" font-size="24">{count}</text>',
            f'<text x="{x + 46}" y="424" text-anchor="middle" font-size="15">{label}</text>',
        ]
    body += [
        '<path d="M52 390H835" stroke="#555"/>',
        '<text x="36" y="478" font-size="14">Combined call: matching-sign combined-p FDR below 0.05. Per-study call: adjusted p below 0.05.</text>',
        '<text x="36" y="502" font-size="14">The fixture does not apply a fold-change screen to either bar category.</text>',
    ]
    return svg_document("Synthetic fixture call overlap", "".join(body))


def write_project(output: Path, fixture: Path) -> None:
    reports = output / "results" / "reports"
    meta = output / "results" / "meta"
    figures = output / "results" / "figures"
    checks = output / "checks"
    for path in (reports, meta, figures, checks):
        path.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((fixture / "meta_analysis_results.csv").open(encoding="utf-8", newline="")))
    study_rows = {
        study: list(csv.DictReader((fixture / f"per_study_{study}.csv").open(encoding="utf-8", newline="")))
        for study in ("A", "B")
    }
    ledger = json.loads((fixture / "meta_eligibility.json").read_text(encoding="utf-8"))
    ledger["execution"] = {
        "dataset_column": "dataset",
        "contrast_factor": "condition",
        "numerator": "case",
        "denominator": "control",
        "per_study_formula": "~ condition",
        "requested_meta_design_formula": "~ condition + dataset",
        "combined_alpha": 0.05,
    }
    ledger["example"] = {
        "kind": "synthetic_method_fixture",
        "scope": "Constructed study-level estimates and standard errors; not a count-based DESeq2 analysis or biological study.",
    }
    (meta / "meta_analysis_results.csv").write_text((fixture / "meta_analysis_results.csv").read_text(encoding="utf-8"), encoding="utf-8")
    for study, records in study_rows.items():
        with (meta / f"per_study_{study}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            for record in records:
                writer.writerow(record)
    (meta / "meta_eligibility.json").write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    convergent = [row for row in rows if row["meta_sig"].lower() == "true"]
    with (meta / "meta_convergent_genes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gene_id", "gene_symbol", "n_studies_sig", "combined_padj", "rem_log2FC", "rem_padj"])
        writer.writeheader()
        for row in convergent:
            writer.writerow({"gene_id": row["gene_id"], "gene_symbol": row["gene_id"], "n_studies_sig": row["n_studies_sig"], "combined_padj": row["combined_padj"], "rem_log2FC": row["rem_log2FC"], "rem_padj": row["rem_padj"]})
    with (meta / "meta_study_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["study", "input_samples", "case_samples", "control_samples", "post_filter_rows"])
        writer.writeheader()
        for study in ledger["studies"]["included"]:
            writer.writerow({"study": study["study"], "input_samples": study["replicates"], "case_samples": 2, "control_samples": 2, "post_filter_rows": study["post_filter_rows"]})
    summary = {
        "n_studies": 2,
        "n_shared_genes": len(rows),
        "n_meta_sig": len(convergent),
        "n_sig_up": sum(row["meta_sig"].lower() == "true" and row["common_direction"] == "up" for row in rows),
        "n_sig_down": sum(row["meta_sig"].lower() == "true" and row["common_direction"] == "down" for row in rows),
        "n_discordant": sum(row["common_direction"] == "discordant" for row in rows),
        "direction_concordance_pct": round(100 * sum(row["common_direction"] in {"up", "down"} for row in rows) / len(rows), 1),
        "pooling": "fixed-effect",
        "alpha": 0.05,
        "per_study_lfc_threshold": "not applied in this synthetic fixture",
        "n_forest_displayed": 1,
    }
    (reports / "meta_analysis_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (reports / "run_summary.json").write_text(json.dumps({"app_version": application_version(), "input": {"type": "synthetic_meta_fixture"}, "example": True}, indent=2) + "\n", encoding="utf-8")
    (meta / "example_provenance.json").write_text(json.dumps({
        "label": "Synthetic teaching example",
        "source": "tests/meta_pooled_methods.R",
        "calculation": "BulkSeq combine_meta with two constructed studies; common-effect pooling for k=2.",
        "not_claimed": ["A biological study", "A complete BulkSeq run", "Per-study DESeq2 fits", "Functional enrichment"],
    }, indent=2) + "\n", encoding="utf-8")
    (checks / "17_meta_analysis_qc.json").write_text(json.dumps({
        "check": "17_meta_analysis_qc",
        "status": "REVIEW_REQUIRED",
        "messages": [{"status": "REVIEW_REQUIRED", "message": "Synthetic teaching fixture: combine_meta completed on constructed study-level estimates. It does not include the input-level evidence required for a completed check 17 meta-analysis QC record."}],
    }, indent=2) + "\n", encoding="utf-8")
    (checks / "18_meta_enrichment_qc.json").write_text(json.dumps({
        "check": "18_meta_enrichment_qc",
        "status": "REVIEW_REQUIRED",
        "messages": [{"status": "REVIEW_REQUIRED", "message": "Functional enrichment was intentionally not run for this synthetic teaching fixture."}],
    }, indent=2) + "\n", encoding="utf-8")
    forest = figures / "meta_forest.svg"
    gain = figures / "meta_integration_gain.svg"
    forest.write_text(forest_svg(rows, study_rows), encoding="utf-8")
    gain.write_text(gain_svg(rows), encoding="utf-8")
    rasterize(forest)
    rasterize(gain)


def build(output: Path, rscript: str, overwrite: bool) -> None:
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {output}; use --overwrite after inspection")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meta-report-", dir=output.parent) as temporary:
        work = Path(temporary)
        subprocess.run([rscript, "--vanilla", str(SOURCE_FIXTURE), f"--fixture-dir={work}"], cwd=ROOT, check=True)
        write_project(output, work)
    sys.path.insert(0, str(ROOT / "workflow" / "scripts"))
    import make_meta_report
    report = output / "results" / "reports" / "meta_analysis_report.html"
    report.write_text(make_meta_report.build(output), encoding="utf-8")
    text = report.read_text(encoding="utf-8")
    marker = '<main id="main-content" tabindex="-1">'
    banner = ('<section class="outcome" aria-label="Synthetic example notice"><h2>Synthetic teaching example</h2>'
              '<p>This report uses constructed study-level estimates and standard errors to exercise BulkSeq\'s cross-study combination and report format. It is not a biological study. It is not a count-based DESeq2 analysis or a complete BulkSeq run.</p></section>')
    if marker not in text:
        raise RuntimeError("Could not insert the synthetic-example notice")
    text = text.replace(marker, marker + banner, 1)
    text = text.replace("Per-study differential expression", "Constructed study-level estimates")
    text = text.replace("full per-study table", "constructed study table")
    text = text.replace("This page describes separate per-study DESeq2 fits and their cross-study combination.",
                        "This page describes a synthetic method fixture that exercises cross-study combination of constructed study-level estimates.")
    text = text.replace("per-study DESeq2 → inverse-normal combined p-values and effect-size pooling.",
                        "constructed study-level estimates → inverse-normal combined p-values and effect-size pooling.")
    report.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rscript", default=os.environ.get("BULKSEQ_RSCRIPT", "Rscript"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    build(args.output.resolve(), args.rscript, args.overwrite)
    print(f"wrote {args.output / 'results/reports/meta_analysis_report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
