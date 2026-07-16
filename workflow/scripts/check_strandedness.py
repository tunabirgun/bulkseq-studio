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
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    messages: list[dict[str, str]] = []
    summary_path = Path(args.summary)
    samples_path = Path(args.samples)

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
                    f"Per-study median Assigned fraction diverges ({detail}). A single global "
                    "featureCounts -s cannot fit studies with different library strandedness; the "
                    "low-assignment study/studies likely need a different -s (rerun featureCounts "
                    "per study with the strandedness that matches each library).")})
            else:
                messages.append({"status": "PASS", "message": (
                    f"Per-study median Assigned fractions are consistent ({detail}); a single global "
                    "featureCounts -s fits all studies.")})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "PASS"
    payload = {"check": "21_strandedness_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
