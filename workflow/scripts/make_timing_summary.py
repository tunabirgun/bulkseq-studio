from __future__ import annotations

import argparse
import csv
import json
import os
import platform
from datetime import datetime, timedelta
from pathlib import Path

import yaml

try:
    import psutil
except Exception:  # psutil is optional at report time
    psutil = None


# Prefix -> phase, matched by startswith in list order (first match wins), so more
# specific prefixes must precede generic ones (salmon_index before a bare salmon rule).
# Covers every current route: STAR/HISAT2/Salmon, fastp/Trim Galore/Trimmomatic,
# SortMeRNA/RiboDetector, and the stats/network steps that used to fall into "Other".
PHASES = [
    ("download", "Download"),
    # reference preparation (indices, transcriptome, reference checks)
    ("read_length", "Reference"),
    ("star_index", "Reference"),
    ("salmon_index", "Reference"),
    ("hisat2_index", "Reference"),
    ("hisat2_build", "Reference"),
    ("make_transcriptome", "Reference"),
    ("05_reference", "Reference"),
    ("reference_check", "Reference"),
    # read QC
    ("fastqc", "QC"),
    ("multiqc", "QC"),
    ("fastq_screen", "QC"),
    ("rseqc", "QC"),
    # adapter/quality trimming
    ("fastp", "Trimming"),
    ("trim_galore", "Trimming"),
    ("trimgalore", "Trimming"),
    ("trimmomatic", "Trimming"),
    # rRNA depletion
    ("sortmerna", "rRNA filtering"),
    ("ribodetector", "rRNA filtering"),
    # alignment
    ("star_align", "Alignment"),
    ("hisat2", "Alignment"),
    ("samtools", "Alignment"),
    ("06_alignment", "Alignment"),
    # quantification
    ("salmon_quant", "Quantification"),
    ("salmon_tximport", "Quantification"),
    ("featurecounts", "Quantification"),
    ("07_quantification", "Quantification"),
    # differential expression (the active engine is route-specific)
    ("deseq2", "Differential expression"),
    ("limma", "Differential expression"),
    ("edger", "Differential expression"),
    # enrichment (includes enrichment_figures)
    ("enrichment", "Enrichment"),
    # DE figures
    ("figures", "Figures"),
    # statistics and networks
    ("network", "Stats & networks"),
    ("set_overlap", "Stats & networks"),
    ("wilcoxon", "Stats & networks"),
    ("sample_correlation", "Stats & networks"),
    ("tost", "Stats & networks"),
    ("gsva", "Stats & networks"),
    # provenance / reporting
    ("final_reports", "Reports"),
    ("html_report", "Reports"),
    ("aggregate", "Reports"),
    ("export_matrices", "Reports"),
]

# These benchmark files are written by report-assembly jobs. A stale copy may still
# exist while Snakemake reruns the job, so the scanner must exclude them by identity
# rather than relying on output deletion timing.
REPORT_ASSEMBLY_STEPS = frozenset({"final_reports", "html_report"})


def phase_for(step: str) -> str:
    for prefix, phase in PHASES:
        if step.startswith(prefix):
            return phase
    return "Other"


def hms(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds)))


def analysis_job_window(mtimes: list[float], steps: list[dict]) -> float:
    """Estimate analysis wall time from benchmark completion times and durations."""
    starts = [mtime - step["seconds"] for mtime, step in zip(mtimes, steps)]
    return (max(mtimes) - min(starts)) if mtimes and starts else sum(
        step["seconds"] for step in steps
    )


def analysis_benchmark_paths(bench_dir: Path) -> list[Path]:
    """Return benchmark files inside the declared scientific-analysis scope."""
    return [
        path for path in sorted(bench_dir.glob("*.tsv"))
        if path.stem not in REPORT_ASSEMBLY_STEPS
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    bench_dir = root / "benchmarks"

    steps = []
    mtimes = []
    for path in analysis_benchmark_paths(bench_dir):
        with path.open("r", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                try:
                    seconds = float(row.get("s", 0) or 0)
                except ValueError:
                    seconds = 0.0
                steps.append({"step": path.stem, "phase": phase_for(path.stem), "seconds": seconds})
                mtimes.append(path.stat().st_mtime)

    cumulative = sum(s["seconds"] for s in steps)
    # Benchmark files are written when each job finishes. Estimate the analysis start
    # by subtracting each job's measured duration from its completion time; using only
    # the first/last completion timestamps systematically drops the first job's runtime.
    wall = analysis_job_window(mtimes, steps)
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
            "physical_cores": psutil.cpu_count(logical=False),
            "total_ram_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        }
    # Machine specs the run executed on, for reproducibility. Best-effort; the pipeline
    # runs inside WSL2 on Windows or natively on Linux, so this describes that environment.
    detected.setdefault("logical_threads", os.cpu_count())
    detected["os"] = platform.platform()
    detected["hostname"] = platform.node()
    if not detected.get("cpu_model"):
        cpu_model = ""
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
        except Exception:
            cpu_model = platform.processor()
        detected["cpu_model"] = cpu_model or platform.processor()
    if not detected.get("total_ram_gb"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal"):
                    detected["total_ram_gb"] = round(int(line.split()[1]) / (1024**2), 1)
                    break
        except Exception:
            pass

    payload = {
        "project_name": config.get("project", {}).get("name", root.resolve().name),
        "run_finish_time": datetime.now().isoformat(timespec="seconds"),
        "wall_clock_approx_hms": hms(wall),
        "cumulative_job_hms": hms(cumulative),
        "timing_scope": (
            "Enabled scientific outputs through final report inputs; excludes the "
            "final_reports and html_report assembly jobs."
        ),
        "includes_report_assembly": False,
        "detected_resources": detected,
        "configured_resources": {
            "snakemake_cores": resources.get("total_threads"),
            "memory_gb": resources.get("total_memory_gb"),
        },
        "per_phase_hms": {phase: hms(secs) for phase, secs in sorted(by_phase.items(), key=lambda kv: kv[1], reverse=True)},
        "per_phase_seconds": {phase: round(secs, 1) for phase, secs in sorted(by_phase.items(), key=lambda kv: kv[1], reverse=True)},
        "per_step_seconds": {s["step"]: round(s["seconds"], 1) for s in sorted(steps, key=lambda s: s["seconds"], reverse=True)},
        "slowest_steps": [{"step": s["step"], "hms": hms(s["seconds"])} for s in slowest],
    }
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "timing_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    title = "BulkSeq Studio Analysis Timing Summary"
    lines = [title, "=" * len(title), "",
             f"Project name: {payload['project_name']}",
             f"Run finished: {payload['run_finish_time']}",
             f"Analysis job window (approx; excludes report assembly): {payload['wall_clock_approx_hms']}",
             f"Cumulative measured analysis jobs: {payload['cumulative_job_hms']}",
             f"Timing scope: {payload['timing_scope']}", "",
             "Configured resources", "--------------------",
             f"Snakemake CPU workers: {payload['configured_resources']['snakemake_cores']}",
             f"Memory (GB): {payload['configured_resources']['memory_gb']}", "",
             "Per-phase timings", "-----------------"]
    lines += [f"{phase:16s} {hms_val}" for phase, hms_val in payload["per_phase_hms"].items()]
    lines += ["", "Slowest steps", "-------------"]
    lines += [f"{i}. {s['step']}: {s['hms']}" for i, s in enumerate(payload["slowest_steps"], 1)]
    (reports / "timing_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
