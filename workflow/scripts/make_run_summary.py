from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import yaml


def env_lock_md5() -> str | None:
    # md5 of the pinned conda lock that defines the analysis environment.
    lock = Path(__file__).resolve().parent.parent / "envs" / "bulkseq.lock.yaml"
    if not lock.exists():
        return None
    return hashlib.md5(lock.read_bytes()).hexdigest()


def workflow_git_commit() -> str | None:
    # Source commit when run from a checkout; None in a packaged build (no .git).
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent),
                              "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, check=False)
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


TOOLS = {
    "snakemake": ["snakemake", "--version"],
    "python": ["python", "--version"],
    "fastqc": ["fastqc", "--version"],
    "multiqc": ["multiqc", "--version"],
    "fastp": ["fastp", "--version"],
    "STAR": ["STAR", "--version"],
    "samtools": ["samtools", "--version"],
    "featureCounts": ["featureCounts", "-v"],
    "Rscript": ["Rscript", "--version"],
}


def run_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except Exception as exc:
        return f"unavailable ({exc.__class__.__name__})"


def diff_configs(defaults: dict, used: dict, prefix: str = "") -> dict:
    changed: dict = {}
    for key, value in used.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in defaults:
            continue
        default_value = defaults[key]
        if isinstance(value, dict) and isinstance(default_value, dict):
            changed.update(diff_configs(default_value, value, path))
        elif value != default_value:
            changed[path] = {"default": default_value, "used": value}
    return changed


def drop_project(config: dict) -> dict:
    return {k: v for k, v in config.items() if k != "project"}


def collect_warnings(sanity_text: str) -> list[str]:
    return [line.strip() for line in sanity_text.splitlines() if "WARNING" in line or "REVIEW_REQUIRED" in line]


def existing_outputs(root: Path) -> list[str]:
    candidates = [
        "results/counts/counts.txt",
        "results/deseq2/deseq2_results.csv",
        "results/qc/multiqc/multiqc_report.html",
        "results/figures/pca.png",
        "results/figures/volcano.png",
        "results/enrichment/enrichment_summary.txt",
    ]
    return [c for c in candidates if (root / c).exists()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    config = yaml.safe_load((root / "config/config.yaml").read_text(encoding="utf-8")) or {}
    default_path = root / "config/default_config.yaml"
    defaults = yaml.safe_load(default_path.read_text(encoding="utf-8")) if default_path.exists() else {}
    customized = diff_configs(drop_project(defaults), drop_project(config))

    versions = {name: run_version(command) for name, command in TOOLS.items()}
    sanity_path = root / "checks/sanity_checks.txt"
    sanity_text = sanity_path.read_text(encoding="utf-8") if sanity_path.exists() else ""
    project = config.get("project", {})

    payload = {
        "run_date": datetime.now().isoformat(timespec="seconds"),
        "app_version": project.get("app_version"),
        "workflow_version": project.get("workflow_version"),
        "workflow_git_commit": workflow_git_commit(),
        "environment_lock_md5": env_lock_md5(),
        "snakemake_version": versions.get("snakemake"),
        "project": project,
        "input": config.get("input", {}),
        "reference": config.get("reference", {}),
        "workflow": config.get("workflow", {}),
        "deseq2": config.get("deseq2", {}),
        "fastp": config.get("fastp", {}),
        "star": config.get("star", {}),
        "featurecounts": config.get("featurecounts", {}),
        "gene_sets": config.get("gene_sets", {}),
        "resources": config.get("resources", {}),
        "rule_threads": config.get("rule_threads", {}),
        "software_versions": versions,
        "customized_parameters": customized,
        "warnings": collect_warnings(sanity_text),
        "output_paths": existing_outputs(root),
        "sanity_checks": sanity_text,
    }
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (reports / "software_versions.txt").write_text("\n".join(f"{k}: {v}" for k, v in versions.items()) + "\n", encoding="utf-8")
    (reports / "run_summary.txt").write_text(render_text(payload), encoding="utf-8")
    return 0


def render_text(p: dict) -> str:
    lines = ["RNA-seq Analysis Run Summary", "============================", ""]
    lines += ["Project", "-------",
              f"Project name: {p['project'].get('name')}",
              f"Working directory: {p['project'].get('working_directory')}",
              f"Run date: {p['run_date']}",
              f"App version: {p['app_version']}    Workflow version: {p['workflow_version']}",
              f"Workflow commit: {p.get('workflow_git_commit') or 'n/a (packaged build)'}",
              f"Environment lock md5: {p.get('environment_lock_md5') or 'n/a'}",
              f"Snakemake: {p['snakemake_version']}", ""]
    ref = p["reference"]
    lines += ["Reference", "---------",
              f"Organism: {ref.get('organism_name')}  Strain: {ref.get('strain')}",
              f"Source/release: {ref.get('source')} {ref.get('release', '')}",
              f"Genome MD5: {ref.get('genome_md5')}  Annotation MD5: {ref.get('annotation_md5')}", ""]
    de = p["deseq2"]
    lines += ["Design", "------",
              f"Design formula: {de.get('design_formula')}",
              f"Reference level: {de.get('reference_level')}",
              f"Contrasts: {json.dumps(de.get('contrasts', []))}",
              f"Alpha: {de.get('alpha')}  Shrinkage: {de.get('shrinkage_method')}", ""]
    lines += ["Selected modules", "----------------", json.dumps(p["workflow"], indent=2), ""]
    lines += ["Customized / Non-standard Parameters", "------------------------------------"]
    if p["customized_parameters"]:
        for key, value in p["customized_parameters"].items():
            lines.append(f"{key}: default={value['default']} used={value['used']}")
    else:
        lines.append("None detected against bundled defaults.")
    lines += ["", "Warnings", "--------"]
    lines += p["warnings"] or ["None."]
    lines += ["", "Output paths", "------------"]
    lines += p["output_paths"] or ["None yet."]
    lines += ["", "Software Versions", "-----------------"]
    lines += [f"{k}: {v}" for k, v in p["software_versions"].items()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
