from __future__ import annotations

import base64
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pandas as pd


project = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects" / "main"
table = pd.read_csv(project / "results/deseq2/deseq2_results.csv").set_index("gene_id")
if len(table) != 80 or table.index.has_duplicates or not {"log2FoldChange", "pvalue", "padj"} <= set(table):
    raise SystemExit("Main differential-expression table is incomplete")
pvalues = pd.to_numeric(table["pvalue"], errors="coerce")
adjusted = pd.to_numeric(table["padj"], errors="coerce")
if not pvalues.map(math.isfinite).all() or not adjusted.map(math.isfinite).all():
    raise SystemExit("Main model lacks the complete finite 80-test family")


def check_effects(values: pd.Series) -> None:
    up = [f"gene{i:04d}" for i in range(1, 9)]
    down = [f"gene{i:04d}" for i in range(9, 17)]
    if not ((values.loc[up] > 0).all() and (values.loc[down] < 0).all()):
        raise ValueError("Main model lost a planted effect direction")


def check_bh(values: pd.Series) -> None:
    order = pvalues.sort_values().index
    expected = pd.Series(index=order, dtype=float)
    running = 1.0
    for rank in range(len(order), 0, -1):
        gene = order[rank - 1]
        running = min(running, pvalues.loc[gene] * len(order) / rank)
        expected.loc[gene] = running
    if not all(math.isclose(values.loc[gene], expected.loc[gene], rel_tol=1e-8,
                            abs_tol=1e-12) for gene in order):
        raise ValueError("Main DESeq2 BH family disagrees with independent calculation")


effects = pd.to_numeric(table["log2FoldChange"], errors="coerce")
check_effects(effects)
check_bh(adjusted)
if sys.argv[1:] == ["--negative-bh"]:
    changed = adjusted.copy()
    changed.iloc[0] = 0.5 if changed.iloc[0] < 0.25 else 0.0
    check_bh(changed)
elif sys.argv[1:] == ["--negative-effect"]:
    changed = effects.copy()
    changed.loc["gene0001"] = -abs(changed.loc["gene0001"])
    check_effects(changed)
elif sys.argv[1:]:
    raise SystemExit("Unknown main-model checker argument")
report = project / "results/reports/results_report.html"
content = report.read_text(encoding="utf-8")
volcano = project / "results/figures/volcano"
if ("<html" not in content.lower() or 'data-figure="volcano"' not in content or
        not any(base64.b64encode(volcano.with_suffix(ext).read_bytes()).decode("ascii") in content
                for ext in (".png", ".svg"))):
    raise SystemExit("Main HTML report is unavailable")
figures = project / "results/figures"
pairs = sorted(figures.glob("*.svg"))
if len(pairs) < 10:
    raise SystemExit("Main figure set is incomplete")
for svg in pairs:
    png = svg.with_suffix(".png")
    if not png.is_file() or not png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit(f"PNG pair is missing or invalid: {svg.stem}")
    ET.parse(svg)
print(f"PASS: 80 finite main tests, all 16 planted effect directions, full-family BH, "
      f"HTML report and {len(pairs)} PNG/SVG figure pairs")
