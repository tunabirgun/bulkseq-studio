#!/usr/bin/env python3
# Build the canonical featureCounts-layout counts.txt + .summary from STAR's per-sample
# {sample}_ReadsPerGene.out.tab (produced by --quantMode GeneCounts). Each tab has 4 header
# rows (N_unmapped, N_multimapping, N_noFeature, N_ambiguous) then one row per gene, columns:
# gene_id, unstranded, forward-stranded, reverse-stranded. Each sample's own strandedness int
# (0/1/2, from strandedness_per_sample.tsv) selects its column 1/2/3, so samples that disagree
# on library strandedness are each read from their matching column. Stdlib only -- no second
# BAM pass; STAR already counted during alignment. The output schema matches featureCounts so
# DESeq2 and the whole downstream are unchanged.
from __future__ import annotations

import argparse
import os

N_ROWS = ("N_unmapped", "N_multimapping", "N_noFeature", "N_ambiguous")
_SUFFIX = "_ReadsPerGene.out.tab"


def sample_id(path: str) -> str:
    base = os.path.basename(path)
    return base[: -len(_SUFFIX)] if base.endswith(_SUFFIX) else os.path.splitext(base)[0]


def load_strand_map(path: str) -> dict[str, int]:
    # Column 3 (reverse-strand ratio, check 21) is tolerated but ignored here.
    strands: dict[str, int] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            sid, _, rest = line.rstrip("\n").partition("\t")
            if sid:
                strands[sid] = int(rest.split("\t", 1)[0])
    return strands


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strand-file", required=True, help="strandedness_per_sample.tsv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("tabs", nargs="+", help="per-sample ReadsPerGene.out.tab, in sample order")
    args = ap.parse_args()
    n = len(args.tabs)
    samples = [sample_id(t) for t in args.tabs]
    strands = load_strand_map(args.strand_file)
    cols = [strands[sid] + 1 for sid in samples]  # 0-based column into [gene, unstranded, fwd, rev]

    gene_order: list[str] = []
    counts: dict[str, list[int]] = {}
    nstats = {k: [0] * n for k in N_ROWS}
    for si, tab in enumerate(args.tabs):
        col = cols[si]
        with open(tab, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) <= col:
                    continue
                gid = parts[0]
                try:
                    val = int(parts[col])
                except ValueError:
                    continue
                if gid in N_ROWS:
                    nstats[gid][si] = val
                    continue
                if gid not in counts:
                    gene_order.append(gid)
                    counts[gid] = [0] * n
                counts[gid][si] = val

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as out:
        out.write("# Program:STAR_GeneCounts (STAR --quantMode GeneCounts ReadsPerGene.out.tab)\n")
        out.write("Geneid\tChr\tStart\tEnd\tStrand\tLength\t" + "\t".join(samples) + "\n")
        for gid in gene_order:
            out.write(gid + "\t.\t.\t.\t.\t0\t" + "\t".join(str(v) for v in counts[gid]) + "\n")

    assigned = [0] * n
    for gid in gene_order:
        row = counts[gid]
        for i in range(n):
            assigned[i] += row[i]
    os.makedirs(os.path.dirname(args.summary) or ".", exist_ok=True)
    with open(args.summary, "w", encoding="utf-8") as s:
        s.write("Status\t" + "\t".join(samples) + "\n")
        s.write("Assigned\t" + "\t".join(str(v) for v in assigned) + "\n")
        s.write("Unassigned_Unmapped\t" + "\t".join(str(v) for v in nstats["N_unmapped"]) + "\n")
        s.write("Unassigned_MultiMapping\t" + "\t".join(str(v) for v in nstats["N_multimapping"]) + "\n")
        s.write("Unassigned_NoFeatures\t" + "\t".join(str(v) for v in nstats["N_noFeature"]) + "\n")
        s.write("Unassigned_Ambiguity\t" + "\t".join(str(v) for v in nstats["N_ambiguous"]) + "\n")


if __name__ == "__main__":
    main()
