#!/usr/bin/env python3
"""Ingest a user-supplied counts matrix into the canonical featureCounts-format
results/counts/counts.txt, so the downstream DESeq2 path is byte-identical to the
alignment route. Accepts either a plain gene x sample matrix or a featureCounts
table; sample columns are validated against samples.tsv.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FC_META = ["Chr", "Start", "End", "Strand", "Length"]


def clean_col(name: str) -> str:
    # Map a featureCounts BAM-path column back to its sample_id; leave plain ids as-is.
    base = os.path.basename(str(name))
    return re.sub(r"_Aligned\.sortedByCoord\.out\.bam$", "", base)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--samples", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--estimated-counts", action="store_true",
                    help="Permit fractional RSEM/tximport estimated counts (round to integers).")
    args = ap.parse_args()

    sep = "," if str(args.matrix).lower().endswith(".csv") else "\t"
    df = pd.read_csv(args.matrix, sep=sep, comment="#", dtype=str)
    if df.shape[1] < 2:
        sys.exit("Count matrix needs a gene-id column plus at least one sample column.")

    # First column is the gene id; drop any featureCounts metadata columns.
    df = df.rename(columns={df.columns[0]: "Geneid"})
    df["Geneid"] = df["Geneid"].astype(str)
    # Duplicate gene ids would be silently collapsed to the last occurrence when
    # used as DESeq2 row names, biasing results. Reject them with clear guidance.
    dups = df["Geneid"][df["Geneid"].duplicated()].unique().tolist()
    if dups:
        preview = ", ".join(dups[:10]) + (" ..." if len(dups) > 10 else "")
        sys.exit(
            f"Count matrix has {len(dups)} duplicate gene id(s): {preview}\n"
            "Collapse duplicates (sum counts per unique gene id) or remove them, then re-import."
        )
    sample_cols = [c for c in df.columns[1:] if c not in FC_META]
    # Guard against normalized / continuous input (TPM, FPKM/RPKM, log-CPM, RMA, DESeq2-normalized,
    # microarray intensities), which the DESeq2 count model and the meta-analysis cannot use. RSEM/
    # tximport *estimated* counts are legitimately fractional; shape cannot distinguish them from
    # FPKM, so mostly-fractional input is REFUSED by default and rounded only when the user declares
    # estimated counts (--estimated-counts / the GUI checkbox / config input.estimated_counts).
    _num = df[sample_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype="float64")
    _fin = _num[~pd.isna(_num)]
    # Fraction of NON-ZERO values that are fractional: zeros are integer and dominate sparse
    # matrices, so measuring over nonzeros stops a sparse normalized matrix from slipping past.
    _nz = _fin[_fin != 0]
    _frac = float((_nz % 1 != 0).mean()) if _nz.size else 0.0
    # TPM: each column sums to ~1e6 regardless of sparsity. Check it UNCONDITIONALLY on a majority
    # of columns (robust to one rescaled/off-spec column) before the fractional gate.
    _colsum = pd.DataFrame(_num).sum(axis=0, skipna=True).to_numpy(dtype="float64")
    if _colsum.size and float(np.mean(np.abs(_colsum - 1e6) / 1e6 < 0.01)) >= 0.5:
        sys.exit(
            "The matrix columns each sum to ~1,000,000, so this is TPM, not raw counts. DESeq2 and "
            "the meta-analysis require raw integer counts. Re-export un-normalized counts."
        )
    if _frac > 0.5:
        if args.estimated_counts:
            print("Note: estimated-counts mode is on; rounding fractional values to the nearest "
                  "integer for DESeq2 (per DESeq2/tximport guidance).")
        else:
            sys.exit(
                "The matrix values are mostly non-integer. If these are RSEM/tximport ESTIMATED "
                "counts, re-import with 'counts are estimated (RSEM/tximport)' enabled so they are "
                "rounded. Otherwise this looks like NORMALIZED/transformed data (TPM, FPKM/RPKM, "
                "log-CPM, RMA, DESeq2-normalized, or microarray intensities), which DESeq2 and the "
                "meta-analysis cannot use — re-export RAW integer counts and re-import."
            )
    cleaned = {c: clean_col(c) for c in sample_cols}

    samples = pd.read_csv(args.samples, sep="\t", dtype=str).fillna("")
    ids = [str(s) for s in samples["sample_id"].tolist()]

    # Match each sample_id to a (cleaned) matrix column.
    by_clean = {}
    for orig, cl in cleaned.items():
        by_clean.setdefault(cl, orig)
    missing = [s for s in ids if s not in by_clean]
    if missing:
        sys.exit(
            "The count matrix is missing columns for sample_id(s): "
            f"{', '.join(missing)}.\nMatrix sample columns: {', '.join(cleaned.values())}"
        )

    out = pd.DataFrame({"Geneid": df["Geneid"].astype(str)})
    for meta in FC_META:
        out[meta] = 0 if meta == "Length" else "NA"
    for sid in ids:  # samples.tsv order; canonical sample_id column names
        col = by_clean[sid]
        numeric = pd.to_numeric(df[col], errors="coerce")
        # Non-numeric cells coerce to NaN; surface that instead of silently zeroing.
        n_bad = int(numeric.isna().sum())
        if n_bad:
            print(f"Warning: {n_bad} non-numeric value(s) in column '{sid}' set to 0.")
        values = numeric.fillna(0).round().astype("int64")
        # DESeq2 requires non-negative integer counts; negatives are invalid input.
        if (values < 0).any():
            sys.exit(
                f"Column '{sid}' contains negative values. RNA-seq counts must be "
                "non-negative integers; check that you uploaded raw counts, not a "
                "normalized/log-transformed matrix."
            )
        out[sid] = values.values

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("# Ingested from a user-supplied count matrix (BulkSeq Studio)\n")
        out.to_csv(fh, sep="\t", index=False)

    # Minimal featureCounts-style summary for the quantification check.
    assigned = out[ids].sum().astype("int64").tolist()
    with open(args.summary, "w", encoding="utf-8") as fh:
        fh.write("Status\t" + "\t".join(ids) + "\n")
        fh.write("Assigned\t" + "\t".join(str(v) for v in assigned) + "\n")

    print(f"Ingested {len(out)} genes x {len(ids)} samples from {args.matrix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
