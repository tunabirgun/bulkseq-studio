from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import pandas as pd


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def _assigned_fractions(summary_path: Path) -> dict[str, float]:
    # featureCounts .summary: first column = status category, remaining columns = per-BAM counts.
    # Assigned fraction = Assigned / column total. Returns {bam_column: fraction}.
    rows = [line.rstrip("\n").split("\t") for line in summary_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:  # an existing-but-empty .summary (interrupted featureCounts) -> degrade, don't IndexError
        return {}
    header = rows[0]
    bams = header[1:]
    totals = [0.0] * len(bams)
    assigned = [0.0] * len(bams)
    for row in rows[1:]:
        status = row[0]
        values = [float(v) for v in row[1:]]
        for i, v in enumerate(values):
            totals[i] += v
            if status == "Assigned":
                assigned[i] += v
    return {bam: (assigned[i] / totals[i] if totals[i] else 0.0) for i, bam in enumerate(bams)}


def _load_strand_per_sample(path: Path) -> dict[str, tuple[int, float | None]]:
    result: dict[str, tuple[int, float | None]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("\t")
        sid, code = parts[0], int(parts[1])
        ratio = float(parts[2]) if len(parts) > 2 and parts[2] else None
        result[sid] = (code, ratio)
    return result


def _within_study_messages(strand: dict[str, tuple[int, float | None]], df: pd.DataFrame) -> list[dict[str, str]]:
    # Within-study consistency: a study whose samples disagree on the inferred code usually
    # means a mislabeled/misassigned library, independent of the cross-study median check
    # below (which needs >= 2 studies and only sees the featureCounts Assigned fraction).
    has_dataset = "dataset" in df.columns and df["dataset"].astype(str).str.strip().ne("").any()
    groups: dict[str, list[str]] = {}
    if has_dataset:
        for _, row in df.iterrows():
            sid = str(row["sample_id"]).strip()
            if sid:
                groups.setdefault(str(row["dataset"]).strip() or "all", []).append(sid)
    else:
        groups["all"] = list(strand)

    messages: list[dict[str, str]] = []
    for study, sids in sorted(groups.items()):
        known = [sid for sid in sids if sid in strand]
        codes = {strand[sid][0] for sid in known}
        if len(codes) <= 1:
            continue
        detail = ", ".join(
            f"{sid}={strand[sid][0]}" + (f" (ratio={strand[sid][1]:.2f})" if strand[sid][1] is not None else "")
            for sid in known
        )
        messages.append({"status": "REVIEW_REQUIRED", "message": (
            "Samples within one study disagree on inferred strandedness: check those "
            "libraries for a mislabeled or misassigned sample before interpreting their "
            "counts. featureCounts already applied each sample's own -s, so the counts "
            f"themselves are not wrong on that account. Study '{study}', inferred codes: "
            f"{detail}.")})
    return messages


def _bam_to_study(bams: list[str], df: pd.DataFrame) -> dict[str, str]:
    # Map each BAM column to a study by matching its sample_id substring. featureCounts names BAM
    # columns by their path, which embeds the sample_id (e.g. results/aligned/<sample_id>.bam).
    if "dataset" not in df.columns or "sample_id" not in df.columns:
        return {}
    # Longest sample_id first so a longer id is not shadowed by a shorter prefix.
    pairs = sorted(
        ((str(r["sample_id"]).strip(), str(r["dataset"]).strip()) for _, r in df.iterrows() if str(r["sample_id"]).strip()),
        key=lambda p: len(p[0]),
        reverse=True,
    )
    mapping = {}
    for bam in bams:
        name = Path(bam).name
        for sid, ds in pairs:
            if sid and sid in name:
                mapping[bam] = ds
                break
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--strand-per-sample", default=None, help="strandedness_per_sample.tsv")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    messages: list[dict[str, str]] = []
    summary_path = Path(args.summary)
    samples_path = Path(args.samples)

    if args.strand_per_sample and samples_path.exists():
        strand_path = Path(args.strand_per_sample)
        if strand_path.exists():
            df_all = pd.read_csv(samples_path, sep="\t", dtype=str).fillna("")
            within_messages = _within_study_messages(_load_strand_per_sample(strand_path), df_all)
            if within_messages:
                messages.extend(within_messages)
            else:
                messages.append({"status": "PASS", "message": (
                    "Every study's samples agree on inferred strandedness.")})

    if not summary_path.exists():
        messages.append({"status": "PASS", "message": f"featureCounts summary not found ({summary_path}); strandedness check skipped."})
    elif not samples_path.exists():
        messages.append({"status": "PASS", "message": f"samples sheet not found ({samples_path}); strandedness check skipped."})
    else:
        fractions = _assigned_fractions(summary_path)
        df = pd.read_csv(samples_path, sep="\t", dtype=str).fillna("")
        mapping = _bam_to_study(list(fractions), df)

        by_study: dict[str, list[float]] = {}
        for bam, frac in fractions.items():
            study = mapping.get(bam)
            if study:
                by_study.setdefault(study, []).append(frac)

        if len(by_study) < 2:
            messages.append({"status": "PASS", "message": (
                "Fewer than two studies could be resolved from the featureCounts summary; "
                "per-study strandedness comparison skipped.")})
        else:
            medians = {s: statistics.median(v) for s, v in by_study.items()}
            low = {s: m for s, m in medians.items() if m < 0.15}
            mmin = min(medians.values())
            mmax = max(medians.values())
            divergent = mmin > 0 and (mmax / mmin) > 2.0
            detail = ", ".join(f"{s}={m:.2f}" for s, m in sorted(medians.items()))
            if low or divergent:
                messages.append({"status": "REVIEW_REQUIRED", "message": (
                    "One or more studies assign far fewer reads to features than the others: "
                    "check their GTF/genome match, contamination and the per-sample "
                    "inference in results/aligned/strandedness_per_sample.tsv before "
                    "interpreting their counts. featureCounts already ran with each sample's "
                    "own inferred -s, so strandedness alone does not explain the gap. "
                    f"Per-study median Assigned fraction: {detail}.")})
            else:
                messages.append({"status": "PASS", "message": (
                    f"Per-study median Assigned fractions are consistent ({detail}); "
                    "per-sample featureCounts strandedness fits all studies.")})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "PASS"
    payload = {"check": "21_strandedness_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
