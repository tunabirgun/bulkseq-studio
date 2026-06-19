from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    rows = []
    for path in sorted((root / "benchmarks").glob("*.tsv")):
        with path.open("r", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                row["source"] = str(path)
                rows.append(row)
    payload = {
        "project_name": root.resolve().name,
        "run_finish_time": datetime.now().isoformat(timespec="seconds"),
        "per_step_timings": rows,
        "slowest_steps": sorted(rows, key=lambda r: float(r.get("s", 0) or 0), reverse=True)[:5],
    }
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "timing_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["RNA-seq Analysis Timing Summary", "===============================", "", f"Project name: {payload['project_name']}", "", "Slowest Steps", "-------------"]
    if payload["slowest_steps"]:
        lines.extend(f"{idx}. {row.get('source')}: {row.get('s')} seconds" for idx, row in enumerate(payload["slowest_steps"], 1))
    else:
        lines.append("No benchmark files found.")
    (reports / "timing_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
