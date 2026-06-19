from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def unique_rate(log_text: str) -> float | None:
    match = re.search(r"Uniquely mapped reads %\s*\|\s*([\d.]+)%", log_text)
    return float(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    messages: list[dict[str, str]] = []
    for log in args.logs:
        path = Path(log)
        sample = path.name.replace("_Log.final.out", "")
        rate = unique_rate(path.read_text(encoding="utf-8")) if path.exists() else None
        if rate is None:
            messages.append({"status": "REVIEW_REQUIRED", "message": f"{sample}: could not parse mapping rate."})
        elif rate >= 70:
            messages.append({"status": "PASS", "message": f"{sample}: {rate:.1f}% uniquely mapped."})
        elif rate >= 50:
            messages.append({"status": "WARNING", "message": f"{sample}: {rate:.1f}% uniquely mapped (low)."})
        else:
            messages.append({"status": "REVIEW_REQUIRED", "message": f"{sample}: {rate:.1f}% uniquely mapped (very low; check genome/GTF and trimming)."})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0)) if messages else "REVIEW_REQUIRED"
    payload = {"check": "06_alignment_qc", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
