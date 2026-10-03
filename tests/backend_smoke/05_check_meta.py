from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pandas as pd


project = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects" / "meta"
folder = project / "results/meta"
table = pd.read_csv(folder / "meta_analysis_results.csv")
ledger = json.loads((folder / "meta_eligibility.json").read_text(encoding="utf-8"))
check = json.loads((project / "checks/17_meta_analysis_qc.json").read_text(encoding="utf-8"))
if (len(table) != 80 or ledger.get("method") != "combined_concordance_and_pooled_full_family_bh_v1" or
        len(ledger["studies"]["included"]) != 2 or check["status"] != "PASS"):
    raise SystemExit("Meta-analysis result, family ledger or check is incomplete")


def bh(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    answer = [1.0] * len(values)
    running = 1.0
    for rank in range(len(order), 0, -1):
        index = order[rank - 1]
        running = min(running, values[index] * len(values) / rank)
        answer[index] = running
    return answer


tested = table["rem_pvalue"].map(math.isfinite)
expected = bh(table.loc[tested, "rem_pvalue"].tolist())
observed = table.loc[tested, "rem_padj"].tolist()


def check_family(values: list[float]) -> None:
    if len(values) != len(expected) or any(
            not math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-12)
            for a, b in zip(values, expected)):
        raise ValueError("Pooled-effect BH family disagrees with the independent calculation")


check_family(observed)
if sys.argv[1:] == ["--negative-fdr"]:
    altered = observed.copy()
    altered[0] = 0.5 if altered[0] < 0.25 else 0.0
    check_family(altered)
elif sys.argv[1:]:
    raise SystemExit("Unknown meta checker argument")
report = project / "results/reports/meta_analysis_report.html"
if not report.is_file() or "<html" not in report.read_text(encoding="utf-8").lower():
    raise SystemExit("Meta-analysis HTML report is unavailable")
figures = project / "results/figures"
pairs = sorted(figures.glob("meta_*.svg"))
if len(pairs) < 7:
    raise SystemExit("Meta figure set is incomplete")
for svg in pairs:
    if not svg.with_suffix(".png").is_file():
        raise SystemExit(f"Missing meta PNG for {svg.stem}")
    ET.parse(svg)
print(f"PASS: two-study meta result, {sum(tested)} pooled tests, independent BH, "
      f"HTML and {len(pairs)} figure pairs")
