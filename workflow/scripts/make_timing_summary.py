from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

try:
    import psutil
except Exception:  # psutil is optional at report time
    psutil = None


PHASES = [
    ("download", "Download"),
    ("read_length", "Reference"),
    ("star_index", "Reference"),
    ("05_reference", "Reference"),
    ("fastqc", "QC"),
    ("multiqc", "QC"),
    ("fastp", "Trimming"),
    ("star_align", "Alignment"),
    ("samtools", "Alignment"),
    ("06_alignment", "Alignment"),
    ("featurecounts", "Quantification"),
    ("07_quantification", "Quantification"),
    ("deseq2", "DESeq2"),
    ("figures", "Figures"),
    ("enrichment", "Enrichment"),
]


def phase_for(step: str) -> str:
    for prefix, phase in PHASES:
        if step.startswith(prefix):
            return phase
    return "Other"


def hms(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    bench_dir = root / "benchmarks"

    steps = []
    mtimes = []
    for path in sorted(bench_dir.glob("*.tsv")):
        with path.open("r", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                try:
                    seconds = float(row.get("s", 0) or 0)
                except ValueError:
                    seconds = 0.0
                steps.append({"step": path.stem, "phase": phase_for(path.stem), "seconds": seconds})
                mtimes.append(path.stat().st_mtime)

    cumulative = sum(s["seconds"] for s in steps)
    wall = (max(mtimes) - min(mtimes)) if len(mtimes) >= 2 else cumulative
    by_phase: dict[str, float] = {}
    for s in steps:
        by_phase[s["phase"]] = by_phase.get(s["phase"], 0.0) + s["seconds"]
    slowest = sorted(steps, key=lambda s: s["seconds"], reverse=True)[:5]

    config = yaml.safe_load((root / "config/config.yaml").read_text(encoding="utf-8")) or {}
    resources = config.get("resources", {})
    detected = {}
    if psutil is not None:
        detected = {
            "logical_threads": psutil.cpu_count(logical=True),
            "total_ram_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        }

    payload = {
        "project_name": config.get("project", {}).get("name", root.resolve().name),
        "run_finish_time": datetime.now().isoformat(timespec="seconds"),
        "wall_clock_approx_hms": hms(wall),
        "cumulative_job_hms": hms(cumulative),
        "detected_resources": detected,
        "configured_resources": {
            "snakemake_cores": resources.get("total_threads"),
            "memory_gb": resources.get("total_memory_gb"),
        },
        "per_phase_hms": {phase: hms(secs) for phase, secs in sorted(by_phase.items(), key=lambda kv: kv[1], reverse=True)},
        "per_step_seconds": {s["step"]: round(s["seconds"], 1) for s in sorted(steps, key=lambda s: s["seconds"], reverse=True)},
        "slowest_steps": [{"step": s["step"], "hms": hms(s["seconds"])} for s in slowest],
    }
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "timing_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = ["RNA-seq Analysis Timing Summary", "===============================", "",
             f"Project name: {payload['project_name']}",
             f"Run finished: {payload['run_finish_time']}",
             f"Overall wall-clock (approx): {payload['wall_clock_approx_hms']}",
             f"Total cumulative job runtime: {payload['cumulative_job_hms']}", "",
             "Configured resources", "--------------------",
             f"Snakemake cores: {payload['configured_resources']['snakemake_cores']}",
             f"Memory (GB): {payload['configured_resources']['memory_gb']}", "",
             "Per-phase timings", "-----------------"]
    lines += [f"{phase:16s} {hms_val}" for phase, hms_val in payload["per_phase_hms"].items()]
    lines += ["", "Slowest steps", "-------------"]
    lines += [f"{i}. {s['step']}: {s['hms']}" for i, s in enumerate(payload["slowest_steps"], 1)]
    (reports / "timing_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
