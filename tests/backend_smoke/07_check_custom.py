from __future__ import annotations

import csv
from io import StringIO
import os
from pathlib import Path
import sys

import pandas as pd


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects"
old = root / "custom_old"
new = root / "custom_new"
folder = Path("results/enrichment")


def same_bytes(a: bytes, b: bytes) -> None:
    if a != b:
        raise ValueError("Custom-enrichment scientific output changed with reporting wording")


for name in ("custom_ora.csv", "custom_gsea.csv", "custom_enrichment_objects.rds"):
    same_bytes((old / folder / name).read_bytes(), (new / folder / name).read_bytes())
ora = (new / folder / "custom_ora.csv").read_bytes()
table = pd.read_csv(StringIO(ora.decode("utf-8")))
if table.empty or not {"GeneRatio", "BgRatio", "Count", "p.adjust"} <= set(table):
    raise SystemExit("Custom ORA model did not produce inspectable ratio evidence")
altered = table.copy()
altered.loc[0, "p.adjust"] = float(altered.loc[0, "p.adjust"]) + 0.1
if sys.argv[1:] == ["--negative-padj"]:
    same_bytes(ora, altered.to_csv(index=False).encode("utf-8"))
elif sys.argv[1:]:
    raise SystemExit("Unknown custom-enrichment checker argument")

results = pd.read_csv(new / "results/deseq2/deseq2_results.csv")
with (new / "inputs/custom.gmt").open(encoding="utf-8") as handle:
    annotated = {gene for line in handle for gene in line.rstrip("\n").split("\t")[2:]}
selected = set()
for name in ("upregulated_genes.csv", "downregulated_genes.csv"):
    selected.update(pd.read_csv(new / "results/deseq2" / name)["gene_id"])
supplied_universe = set(results.loc[results["padj"].notna(), "gene_id"])
effective_background = supplied_universe & annotated
effective_selected = selected & effective_background
summary = (new / folder / "custom_enrichment_summary.txt").read_text(encoding="utf-8")
for label, value in (
    ("Supplied ORA universe", len(supplied_universe)),
    ("Supplied selected genes (ORA input)", len(selected)),
    ("Effective ORA annotated background", len(effective_background)),
    ("Effective ORA annotated selected genes", len(effective_selected)),
):
    if f"{label}: {value}" not in summary:
        raise SystemExit(f"Summary disagrees with independently derived {label}")
if (len(supplied_universe), len(selected), len(effective_background),
        len(effective_selected)) != (80, 18, 45, 17):
    raise SystemExit("The planted 80/18 supplied and 45/17 effective fixture drifted")
for row in csv.DictReader(StringIO(ora.decode("utf-8"))):
    gene_n, query_n = map(int, row["GeneRatio"].split("/"))
    term_n, bg_n = map(int, row["BgRatio"].split("/"))
    if (query_n != len(effective_selected) or bg_n != len(effective_background) or
            gene_n != int(row["Count"]) or gene_n > term_n):
        raise SystemExit("Custom ORA row ratios disagree with the executed populations")
print(f"PASS: old/new ORA/GSEA CSV and RDS bytes equal; supplied {len(supplied_universe)}/"
      f"{len(selected)}, effective {len(effective_background)}/{len(effective_selected)}; "
      "model ratios consistent")
