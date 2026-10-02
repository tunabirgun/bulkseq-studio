from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--meta-dir", default="results/meta")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    messages: list[dict[str, str]] = []
    studies: list[str] = []
    library_covered: list[str] = []
    unavailable: list[str] = []

    samples_path = Path(args.samples)
    if samples_path.exists():
        try:
            df = pd.read_csv(samples_path, sep="\t", dtype=str).fillna("")
            if "dataset" in df.columns:
                ds = df["dataset"].astype(str).str.strip()
                studies = sorted(v for v in ds.unique() if v)
                if {"read_count", "base_count"} <= set(df.columns):
                    for study in studies:
                        sub = df[ds == study]
                        if all(sub[col].astype(str).str.fullmatch(r"\d+").all()
                               for col in ("read_count", "base_count")):
                            library_covered.append(study)
                        else:
                            unavailable.append(f"Study '{study}' has missing or nonnumeric library-size evidence.")
                    for a, b in _identical_libraries(df[ds.isin(library_covered)]):
                        messages.append({"status": "REVIEW_REQUIRED", "message":
                                         f"Studies '{a}' and '{b}' have identical library-size signatures; review possible re-deposition."})
                else:
                    unavailable.append("Samples sheet lacks read_count or base_count columns.")
            else:
                unavailable.append("Samples sheet lacks study assignments (dataset column).")
        except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            unavailable.append(f"Samples evidence unreadable: {exc}")
    else:
        unavailable.append(f"Samples evidence unavailable: {samples_path}")

    meta_dir = Path(args.meta_dir)
    vectors = {}
    for study in studies:
        path = meta_dir / f"per_study_{study}.csv"
        if not path.exists():
            unavailable.append(f"Study '{study}' DE table is missing.")
            continue
        try:
            data = pd.read_csv(path)
            if {"gene_id", "log2FoldChange"} <= set(data.columns):
                values = pd.Series(pd.to_numeric(data["log2FoldChange"], errors="coerce").values,
                                   index=data["gene_id"].astype(str))
                if values.index.duplicated().any():
                    unavailable.append(f"Study '{study}' DE table has duplicate gene identifiers.")
                elif not np.isfinite(values.to_numpy(dtype=float)).all():
                    unavailable.append(f"Study '{study}' DE effect vector contains nonfinite or missing values.")
                elif len(values) >= 10:
                    vectors[study] = values
                else:
                    unavailable.append(f"Study '{study}' DE effect vector has fewer than 10 genes.")
            else:
                unavailable.append(f"Study '{study}' DE table lacks gene_id or log2FoldChange.")
        except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            unavailable.append(f"Study '{study}' DE table is unreadable: {exc}")
            continue
    de_pairs = []
    for a, b in combinations(studies, 2):
        if a not in vectors or b not in vectors:
            unavailable.append(f"Studies '{a}' and '{b}' lack two usable DE effect vectors.")
            continue
        shared = vectors[a].index.intersection(vectors[b].index)
        if len(shared) < 10:
            unavailable.append(f"Studies '{a}' and '{b}' share fewer than 10 DE gene identifiers.")
            continue
        va, vb = vectors[a].loc[shared], vectors[b].loc[shared]
        std_a, std_b = float(va.std()), float(vb.std())
        if not np.isfinite([std_a, std_b]).all() or std_a <= 0 or std_b <= 0:
            unavailable.append(f"Studies '{a}' and '{b}' have constant or undefined DE variation.")
            continue
        correlation = float(va.corr(vb))
        if not np.isfinite(correlation):
            unavailable.append(f"Studies '{a}' and '{b}' have undefined DE correlation.")
            continue
        de_pairs.append([a, b])
        if correlation > 0.999:
            messages.append({"status": "REVIEW_REQUIRED", "message":
                             f"Studies '{a}' and '{b}' have near-identical DE effect vectors; review possible re-deposition."})
    expected_pairs = len(studies) * (len(studies) - 1) // 2
    library_pairs = len(library_covered) * (len(library_covered) - 1) // 2
    if expected_pairs and len(de_pairs) == expected_pairs and library_pairs == expected_pairs:
        coverage = "assessed"
    elif de_pairs or library_pairs:
        coverage = "partial"
    else:
        coverage = "unassessed"
    messages.extend({"status": "REVIEW_REQUIRED", "message": reason} for reason in unavailable)
    messages.append({"status": "REVIEW_REQUIRED", "message":
                     f"Duplicate-study screening coverage: {coverage}; library-size pairs {library_pairs}/{expected_pairs}, "
                     f"DE-correlation pairs {len(de_pairs)}/{expected_pairs}. These heuristics cannot establish independent samples or studies; review source provenance."})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0))
    payload = {"check": "20_duplicate_study_qc", "status": status, "messages": messages,
               "coverage": {"status": coverage, "studies": studies,
                            "expected_pairs": expected_pairs, "library_pairs": library_pairs,
                            "de_pairs": de_pairs, "unavailable_reasons": unavailable}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
