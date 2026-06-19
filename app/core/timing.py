from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path


def write_timing_summary(project_root: Path, estimate: dict[str, object] | None = None, run_started: str | None = None, run_finished: str | None = None) -> dict[str, object]:
    benchmarks = []
    for path in sorted((project_root / "benchmarks").glob("*.tsv")):
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                row["source"] = str(path)
                benchmarks.append(row)
    payload = {
        "project_name": project_root.name,
        "run_start_time": run_started,
        "run_finish_time": run_finished or datetime.now().isoformat(timespec="seconds"),
        "pre_run_estimate": estimate or {},
        "per_step_timings": benchmarks,
        "slowest_steps": sorted(benchmarks, key=lambda r: float(r.get("s", 0) or 0), reverse=True)[:5],
    }
    reports = project_root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "timing_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (reports / "timing_summary.txt").write_text(_timing_text(payload), encoding="utf-8")
    return payload


def _timing_text(payload: dict[str, object]) -> str:
    lines = ["RNA-seq Analysis Timing Summary", "===============================", "", "Project", "-------"]
    lines.append(f"Project name: {payload.get('project_name')}")
    lines.append(f"Run started: {payload.get('run_start_time')}")
    lines.append(f"Run finished: {payload.get('run_finish_time')}")
    estimate = payload.get("pre_run_estimate") or {}
    if isinstance(estimate, dict):
        lines += ["", "Estimated Runtime", "-----------------", f"Pre-run estimate: {estimate.get('range', 'not calculated')}"]
    lines += ["", "Slowest Steps", "-------------"]
    slowest = payload.get("slowest_steps") or []
    if isinstance(slowest, list) and slowest:
        for idx, row in enumerate(slowest, 1):
            lines.append(f"{idx}. {row.get('source')}: {row.get('s')} seconds")
    else:
        lines.append("No Snakemake benchmark files found yet.")
    return "\n".join(lines) + "\n"
