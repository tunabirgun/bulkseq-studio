from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd


REQUIRED = ["sample_id", "condition", "layout", "fastq_1"]


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
    duplicates = [sid for sid, count in Counter(df.get("sample_id", [])).items() if count > 1]
    if duplicates:
        messages.append({"status": "FAIL", "message": f"Duplicate sample IDs: {', '.join(duplicates)}"})
    unsafe = [sid for sid in df.get("sample_id", []) if not re.match(r"^[A-Za-z0-9_.-]+$", str(sid))]
    if unsafe:
        messages.append({"status": "FAIL", "message": f"Unsafe sample IDs: {', '.join(unsafe)}"})
    if not messages:
        messages.append({"status": "PASS", "message": "Metadata passed basic validation."})
    Path(args.out).write_text(json.dumps({"check": "metadata", "messages": messages}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
