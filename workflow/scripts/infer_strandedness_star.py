#!/usr/bin/env python3
# Infer strandedness per sample from STAR's --quantMode GeneCounts ReadsPerGene.out.tab
# (columns: gene_id, unstranded, forward-stranded, reverse-stranded). Same ratio/threshold
# logic as the prior single-sample inference, applied independently to every sample so a
# mixed-strandedness multi-study run is not miscounted from one sample's answer.
from __future__ import annotations

import argparse
import os

_SUFFIX = "_ReadsPerGene.out.tab"


def sample_id(path: str) -> str:
    base = os.path.basename(path)
    return base[: -len(_SUFFIX)] if base.endswith(_SUFFIX) else os.path.splitext(base)[0]


def infer_strand(tab_path: str) -> tuple[int, float]:
    fwd = rev = 0
    with open(tab_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("N_"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            try:
                fwd += int(parts[2])
                rev += int(parts[3])
            except ValueError:
                continue
    total = fwd + rev
    ratio = (rev / total) if total else 0.5
    return (2 if ratio > 0.7 else (1 if ratio < 0.3 else 0)), ratio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="legacy single-value strandedness.txt (first sample)")
    ap.add_argument("--per-sample-out", required=True, help="sample_id<TAB>strand, one per line")
    ap.add_argument("tabs", nargs="+", help="per-sample ReadsPerGene.out.tab, in sample order")
    args = ap.parse_args()

    per_sample = {sample_id(t): infer_strand(t) for t in args.tabs}

    os.makedirs(os.path.dirname(args.per_sample_out) or ".", exist_ok=True)
    with open(args.per_sample_out, "w", encoding="utf-8") as handle:
        for sid, (strand, ratio) in per_sample.items():
            handle.write(f"{sid}\t{strand}\t{ratio:.4f}\n")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    first = per_sample[sample_id(args.tabs[0])][0]
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(f"{first}\n")


if __name__ == "__main__":
    main()
