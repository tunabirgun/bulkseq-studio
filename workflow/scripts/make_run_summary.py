from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml


TOOLS = {
    "snakemake": ["snakemake", "--version"],
    "python": ["python", "--version"],
    "fastqc": ["fastqc", "--version"],
    "multiqc": ["multiqc", "--version"],
    "fastp": ["fastp", "--version"],
    "STAR": ["STAR", "--version"],
    "featureCounts": ["featureCounts", "-v"],
    "Rscript": ["Rscript", "--version"],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    config = yaml.safe_load((root / "config/config.yaml").read_text(encoding="utf-8")) or {}
    versions = {name: run_version(command) for name, command in TOOLS.items()}
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "software_versions.txt").write_text("\n".join(f"{k}: {v}" for k, v in versions.items()) + "\n", encoding="utf-8")
    payload = {
        "project": config.get("project", {}),
        "input": config.get("input", {}),
        "reference": config.get("reference", {}),
        "workflow": config.get("workflow", {}),
        "resources": config.get("resources", {}),
        "software_versions": versions,
        "sanity_checks": (root / "checks/sanity_checks.txt").read_text(encoding="utf-8") if (root / "checks/sanity_checks.txt").exists() else "",
    }
    (reports / "run_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["RNA-seq Analysis Run Summary", "============================", "", "Project", "-------"]
    lines.append(f"Project name: {payload['project'].get('name')}")
    lines.append(f"Working directory: {payload['project'].get('working_directory')}")
    lines.extend(["", "Workflow", "--------", json.dumps(payload["workflow"], indent=2), "", "Software Versions", "-----------------"])
    lines.extend(f"{k}: {v}" for k, v in versions.items())
    (reports / "run_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def run_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except Exception as exc:
        return f"unavailable ({exc.__class__.__name__})"


if __name__ == "__main__":
    raise SystemExit(main())
