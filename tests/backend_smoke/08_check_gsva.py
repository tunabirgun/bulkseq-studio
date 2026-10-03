from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pandas as pd


project = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects/custom_new"
scores = pd.read_csv(project / "results/gsva/gsva_scores.csv", index_col=0)


def check_scores(frame: pd.DataFrame) -> None:
    if (frame.shape != (2, 8) or set(frame.index) != {"signal_A", "signal_B"} or
            list(frame.columns) != [f"S{i:02d}" for i in range(1, 9)] or
            not frame.map(math.isfinite).all().all()):
        raise ValueError("GSVA scores are incomplete or non-finite")


check_scores(scores)
if sys.argv[1:] == ["--negative-nan"]:
    altered = scores.copy()
    altered.iloc[0, 0] = float("nan")
    check_scores(altered)
elif sys.argv[1:]:
    raise SystemExit("Unknown GSVA checker argument")
figures = project / "results/figures"
svg = figures / "gsva_heatmap.svg"
png = figures / "gsva_heatmap.png"
if not png.is_file() or not png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
    raise SystemExit("GSVA PNG is missing or invalid")
ET.parse(svg)
print("PASS: two finite descriptive GSVA sets across eight named samples and PNG/SVG pair")
