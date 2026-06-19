from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml


TOOLS = {
    "snakemake": ["snakemake", "--version"],
    "python": ["python", "--version"],
    "fastqc": ["fastqc", "--version"],
    "multiqc": ["multiqc", "--version"],
    "fastp": ["fastp", "--version"],
    "sortmerna": ["sortmerna", "--version"],
    "STAR": ["STAR", "--version"],
    "hisat2": ["hisat2", "--version"],
    "samtools": ["samtools", "--version"],
    "featureCounts": ["featureCounts", "-v"],
    "salmon": ["salmon", "--version"],
    "Rscript": ["Rscript", "--version"],
}


def capture_versions(project_root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name, command in TOOLS.items():
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
            versions[name] = (result.stdout or result.stderr).strip().splitlines()[0]
        except Exception as exc:
            versions[name] = f"unavailable ({exc.__class__.__name__})"
    out = project_root / "results" / "reports" / "software_versions.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(f"{k}: {v}" for k, v in versions.items()) + "\n", encoding="utf-8")
    return versions


def write_run_summary(project_root: Path, default_config_path: Path | None = None) -> dict[str, Any]:
    config_path = project_root / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    defaults = yaml.safe_load(default_config_path.read_text(encoding="utf-8")) if default_config_path and default_config_path.exists() else {}
    # The 'project' block carries per-project identity (name, working directory,
    # creation date) that always differs from the bundled defaults; excluding it
    # keeps the customized-parameters list focused on scientific/tool settings.
    customized = diff_configs(_drop_project(defaults), _drop_project(config))
    versions = capture_versions(project_root)
    sanity_path = project_root / "checks" / "sanity_checks.txt"
    payload = {
        "project": config.get("project", {}),
        "input": config.get("input", {}),
        "reference": config.get("reference", {}),
        "workflow": config.get("workflow", {}),
        "resources": config.get("resources", {}),
        "software_versions": versions,
        "customized_parameters": customized,
        "sanity_checks": sanity_path.read_text(encoding="utf-8") if sanity_path.exists() else "",
    }
    reports = project_root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (reports / "run_summary.txt").write_text(_summary_text(payload), encoding="utf-8")
    return payload


def _drop_project(config: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in config.items() if k != "project"}


def diff_configs(defaults: dict[str, Any], used: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for key, value in used.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in defaults:
            continue
        default_value = defaults[key]
        if isinstance(value, dict) and isinstance(default_value, dict):
            nested = diff_configs(default_value, value, path)
            changed.update(nested)
        elif value != default_value:
            changed[path] = {"default": default_value, "used": value}
    return changed


def _summary_text(payload: dict[str, Any]) -> str:
    lines = ["RNA-seq Analysis Run Summary", "============================", ""]
    project = payload.get("project", {})
    lines += ["Project", "-------", f"Project name: {project.get('name')}", f"Working directory: {project.get('working_directory')}", ""]
    lines += ["Workflow", "--------", json.dumps(payload.get("workflow", {}), indent=2), ""]
    lines += ["Customized / Non-standard Parameters", "------------------------------------"]
    changed = payload.get("customized_parameters", {})
    if changed:
        for key, value in changed.items():
            lines.append(f"{key}: default={value['default']} used={value['used']}")
    else:
        lines.append("None detected against bundled defaults.")
    lines += ["", "Software Versions", "-----------------"]
    lines += [f"{k}: {v}" for k, v in payload.get("software_versions", {}).items()]
    return "\n".join(lines) + "\n"
