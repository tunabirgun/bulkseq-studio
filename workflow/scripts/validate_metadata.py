from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd


REQUIRED = ["sample_id", "condition", "layout", "fastq_1"]
PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    df = pd.read_csv(args.samples, sep="\t", dtype=str).fillna("")
    messages: list[dict[str, str]] = []

    missing = [col for col in REQUIRED if col not in df.columns]
    if missing:
        messages.append({"status": "FAIL", "message": f"Missing required columns: {', '.join(missing)}"})

    ids = list(df.get("sample_id", []))
    duplicates = [sid for sid, count in Counter(ids).items() if count > 1]
    if duplicates:
        messages.append({"status": "FAIL", "message": f"Duplicate sample IDs: {', '.join(duplicates)}"})
    unsafe = [sid for sid in ids if not re.match(r"^[A-Za-z0-9_.-]+$", str(sid))]
    if unsafe:
        messages.append({"status": "FAIL", "message": f"Unsafe sample IDs: {', '.join(unsafe)}"})

    if "condition" in df.columns:
        counts = df.groupby("condition")["sample_id"].count().to_dict()
        for condition, count in counts.items():
            if condition in ("", "unknown"):
                continue
            if count < 2:
                messages.append({"status": "WARNING", "message": f"Condition '{condition}' has fewer than two replicates."})

    if not messages:
        messages.append({"status": "PASS", "message": "Metadata passed input validation."})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0))
    payload = {"check": "01_input_validation", "status": status, "messages": messages}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
