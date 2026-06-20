from __future__ import annotations

import argparse
import json
from pathlib import Path


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    # featureCounts .summary: first column = status category, remaining columns =
    # per-BAM counts. Assignment rate = Assigned / total per sample.
    rows = [line.rstrip("\n").split("\t") for line in Path(args.summary).read_text(encoding="utf-8").splitlines() if line.strip()]
    header = rows[0]
    samples = header[1:]
    totals = [0.0] * len(samples)
    assigned = [0.0] * len(samples)
    has_unassigned = any(row[0].startswith("Unassigned") for row in rows[1:])
    for row in rows[1:]:
        status = row[0]
        values = [float(v) for v in row[1:]]
        for i, v in enumerate(values):
            totals[i] += v
            if status == "Assigned":
                assigned[i] += v

    messages: list[dict[str, str]] = []
    if not has_unassigned:
        # User-supplied count matrix (ingest_counts): there are no unassigned reads
        # to measure an assignment rate against, so a 100% "rate" would be a false
        # quality signal. Report library size instead and flag empty/outlier samples
        # relative to the median, which catches truncated or mislabelled columns.
        libsizes = assigned
        nonzero = sorted(v for v in libsizes if v > 0)
        median = nonzero[len(nonzero) // 2] if nonzero else 0.0
        for i, sample in enumerate(samples):
            name = Path(sample).name
            lib = libsizes[i]
            if lib <= 0:
                messages.append({"status": "FAIL", "message": f"{name}: 0 counts (empty column in the count matrix)."})
            elif median and lib < 0.1 * median:
                messages.append({"status": "REVIEW_REQUIRED", "message": f"{name}: {lib:,.0f} counts — far below the median library size ({median:,.0f}); check for a truncated column."})
            else:
                messages.append({"status": "PASS", "message": f"{name}: {lib:,.0f} counts (user-supplied matrix; assignment QC not applicable)."})
    else:
        for i, sample in enumerate(samples):
            name = Path(sample).name
            rate = (assigned[i] / totals[i] * 100) if totals[i] else 0.0
            if rate >= 60:
                messages.append({"status": "PASS", "message": f"{name}: {rate:.1f}% reads assigned to genes."})
            elif rate >= 40:
                messages.append({"status": "WARNING", "message": f"{name}: {rate:.1f}% assigned (check strandedness)."})
            else:
                messages.append({"status": "REVIEW_REQUIRED", "message": f"{name}: {rate:.1f}% assigned (low; likely wrong -s strandedness)."})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "REVIEW_REQUIRED"
    payload = {"check": "07_quantification_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
