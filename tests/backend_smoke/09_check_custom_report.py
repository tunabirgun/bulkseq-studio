from __future__ import annotations

import os
from pathlib import Path
import sys


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects"
old = root / "custom_old/results/enrichment"
new = root / "custom_new/results/enrichment"
for name in ("custom_ora.csv", "custom_gsea.csv", "custom_enrichment_objects.rds"):
    if (old / name).read_bytes() != (new / name).read_bytes():
        raise SystemExit(f"Final report generation changed scientific {name}")
report = (root / "custom_new/results/reports/results_report.html").read_text(encoding="utf-8")


def check_report(text: str) -> None:
    for expected in ("Supplied ORA universe: 80", "Supplied selected genes (ORA input): 18",
                     "Effective ORA annotated background: 45",
                     "Effective ORA annotated selected genes: 17", "GeneRatio", "BgRatio",
                     "17/17", "20/45"):
        if expected not in text:
            raise ValueError(f"Final custom ORA report omitted: {expected}")


check_report(report)
if sys.argv[1:] == ["--negative-population"]:
    check_report(report.replace("Effective ORA annotated background: 45",
                                "Effective ORA annotated background: 46"))
elif sys.argv[1:]:
    raise SystemExit("Unknown custom-report checker argument")
print("PASS: final custom HTML reports supplied/effective populations and leaves CSV/RDS bytes unchanged")
