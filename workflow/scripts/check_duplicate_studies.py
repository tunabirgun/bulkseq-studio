from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import pandas as pd


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def _sample_signature(sub: pd.DataFrame) -> frozenset:
    # Multiset of per-sample (read_count, base_count) as a frozenset of (value, multiplicity) so
    # order does not matter. Only rows with both fields populated contribute.
    vals: list[tuple[str, str]] = []
    for _, row in sub.iterrows():
        rc = str(row.get("read_count", "") or "").strip()
        bc = str(row.get("base_count", "") or "").strip()
        if rc and bc:
            vals.append((rc, bc))
    counts: dict[tuple[str, str], int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    return frozenset(counts.items())


def _identical_libraries(df: pd.DataFrame) -> list[tuple[str, str]]:
    if "dataset" not in df.columns or not {"read_count", "base_count"} <= set(df.columns):
        return []
    ds = df["dataset"].astype(str).str.strip()
    studies = [d for d in ds.replace("", pd.NA).dropna().unique()]
    sigs = {}
    for d in studies:
        sig = _sample_signature(df[ds == d])
        if sig:  # skip studies with no populated (read_count, base_count)
            sigs[d] = sig
    pairs = []
    for a, b in combinations(sorted(sigs), 2):
        if sigs[a] == sigs[b]:
            pairs.append((a, b))
    return pairs


def _correlated_de(meta_dir: Path, r_threshold: float = 0.999) -> list[tuple[str, str]]:
    # Per-study DESeq2 log2FoldChange vectors correlate on shared genes -> likely the same study.
    files = sorted(meta_dir.glob("per_study_*.csv"))
    # Exclude the VSD rds companions that share the prefix pattern (they are .rds, glob is .csv only).
    lfc = {}
    for f in files:
        study = f.stem[len("per_study_"):]
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        if "gene_id" not in d.columns or "log2FoldChange" not in d.columns:
            continue
        s = pd.Series(d["log2FoldChange"].values, index=d["gene_id"].astype(str))
        s = s[~s.index.duplicated(keep="first")].dropna()
        if len(s) >= 10:
            lfc[study] = s
    pairs = []
    for a, b in combinations(sorted(lfc), 2):
        shared = lfc[a].index.intersection(lfc[b].index)
        if len(shared) < 10:
            continue
        va, vb = lfc[a].loc[shared], lfc[b].loc[shared]
        if va.std() == 0 or vb.std() == 0:
            continue
        r = va.corr(vb)
        if pd.notna(r) and r > r_threshold:
            pairs.append((a, b))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--meta-dir", default="results/meta")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    messages: list[dict[str, str]] = []
    notes: list[str] = []

    samples_path = Path(args.samples)
    if samples_path.exists():
        df = pd.read_csv(samples_path, sep="\t", dtype=str).fillna("")
        if "dataset" in df.columns and df["dataset"].astype(str).str.strip().replace("", pd.NA).nunique(dropna=True) > 1:
            for a, b in _identical_libraries(df):
                messages.append({"status": "REVIEW_REQUIRED", "message": (
                    f"Studies '{a}' and '{b}' have an identical per-sample (read_count, base_count) "
                    "multiset — likely the same data re-deposited. Pseudo-replication inflates "
                    "meta-analysis significance and understates between-study heterogeneity; verify "
                    "they are independent studies before pooling.")})
        else:
            notes.append("samples sheet has no multi-study 'dataset' column; library-size duplicate check skipped.")
    else:
        notes.append(f"samples sheet not found ({samples_path}); library-size duplicate check skipped.")

    meta_dir = Path(args.meta_dir)
    per_study = sorted(meta_dir.glob("per_study_*.csv"))
    if per_study:
        for a, b in _correlated_de(meta_dir):
            messages.append({"status": "REVIEW_REQUIRED", "message": (
                f"Studies '{a}' and '{b}' have per-study DE log2FoldChange vectors correlating "
                ">0.999 on shared genes — likely the same study re-deposited. Pseudo-replication "
                "inflates meta-analysis significance and understates between-study heterogeneity.")})
    else:
        notes.append("no per_study_*.csv found; DE-correlation duplicate check skipped.")

    if not messages:
        note = " ".join(notes) if notes else "no duplicated studies detected."
        messages.append({"status": "PASS", "message": f"No likely re-deposited (pseudo-replicated) studies detected. {note}".strip()})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0))
    payload = {"check": "20_duplicate_study_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
