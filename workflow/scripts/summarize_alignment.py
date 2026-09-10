from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def unique_rate(log_text: str) -> float | None:
    match = re.search(r"Uniquely mapped reads %\s*\|\s*([\d.]+)%", log_text)
    return float(match.group(1)) if match else None


def hisat2_unique_rate(summary_text: str) -> float | None:
    # Paired: uniquely mapped = "aligned concordantly exactly 1 time" (of the read pairs).
    # Single-end: uniquely mapped = "aligned exactly 1 time" (of the unpaired reads). Both are
    # HISAT2's own denominator (total reads/pairs), matching STAR's "Uniquely mapped reads %".
    match = re.search(r"([\d.]+)%\)\s*aligned concordantly exactly 1 time", summary_text)
    if match is None:
        match = re.search(r"([\d.]+)%\)\s*aligned exactly 1 time", summary_text)
    return float(match.group(1)) if match else None


def _rate_message(sample: str, rate: float | None) -> dict[str, str]:
    if rate is None:
        return {"status": "REVIEW_REQUIRED", "message": f"{sample}: could not parse mapping rate."}
    if rate >= 70:
        return {"status": "PASS", "message": f"{sample}: {rate:.1f}% uniquely mapped."}
    if rate >= 50:
        return {"status": "WARNING", "message": f"{sample}: {rate:.1f}% uniquely mapped (low)."}
    return {"status": "REVIEW_REQUIRED", "message": f"{sample}: {rate:.1f}% uniquely mapped (very low; check genome/GTF and trimming)."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", nargs="+")
    parser.add_argument("--hisat2-summaries", nargs="+")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not args.logs and not args.hisat2_summaries:
        parser.error("one of --logs or --hisat2-summaries is required")

    messages: list[dict[str, str]] = []
    for log in args.logs or []:
        path = Path(log)
        sample = path.name.replace("_Log.final.out", "")
        rate = unique_rate(path.read_text(encoding="utf-8")) if path.exists() else None
        messages.append(_rate_message(sample, rate))
    for summary in args.hisat2_summaries or []:
        path = Path(summary)
        sample = path.name.replace("_hisat2_summary.txt", "")
        rate = hisat2_unique_rate(path.read_text(encoding="utf-8")) if path.exists() else None
        messages.append(_rate_message(sample, rate))

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "REVIEW_REQUIRED"
    payload = {"check": "06_alignment_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
