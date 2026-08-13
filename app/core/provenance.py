from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from app.constants import WSL_ENV_NAME, WSL_MAMBA_ROOT, WSL_MICROMAMBA


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


def _no_window_flags() -> int:
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _capture_versions_wsl(distro: str | None = None) -> dict[str, str]:
    # Probe every tool inside the WSL micromamba env in one shot — the tools live
    # there, not on the Windows PATH, so a local probe would report all missing.
    probes = []
    for name, command in TOOLS.items():
        binary = command[0]
        joined = " ".join(command)
        probes.append(
            f'printf "%s\\t" {shlex.quote(name)}; '
            f'if command -v {shlex.quote(binary)} >/dev/null 2>&1; then '
            # First non-empty line (featureCounts prints a leading blank line).
            f'{{ {joined} 2>&1 || true; }} | awk \'NF{{print; exit}}\'; '
            f'else echo "unavailable (not in env)"; fi'
        )
    script = "; ".join(probes)
    inner = (
        f'export MAMBA_ROOT_PREFIX="{WSL_MAMBA_ROOT}" && '
        f'"{WSL_MICROMAMBA}" run -n {WSL_ENV_NAME} bash -c {shlex.quote(script)}'
    )
    cmd = ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", inner]
    versions: dict[str, str] = {}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                check=False, creationflags=_no_window_flags())
        for line in (result.stdout or "").splitlines():
            if "\t" in line:
                key, _, value = line.partition("\t")
                versions[key.strip()] = value.strip()
    except Exception as exc:
        versions = {name: f"unavailable ({exc.__class__.__name__})" for name in TOOLS}
    # Any tool the probe did not report (e.g. WSL unreachable) is marked unknown.
    for name in TOOLS:
        versions.setdefault(name, "unavailable (WSL probe failed)")
    return versions


def _capture_versions_local() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name, command in TOOLS.items():
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
            versions[name] = (result.stdout or result.stderr).strip().splitlines()[0]
        except Exception as exc:
            versions[name] = f"unavailable ({exc.__class__.__name__})"
    return versions


def capture_versions(project_root: Path, use_wsl: bool = False, distro: str | None = None) -> dict[str, str]:
    versions = _capture_versions_wsl(distro) if use_wsl else _capture_versions_local()
    out = project_root / "results" / "reports" / "software_versions.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(f"{k}: {v}" for k, v in versions.items()) + "\n", encoding="utf-8")
    return versions


def write_run_summary(project_root: Path, default_config_path: Path | None = None,
                      use_wsl: bool = False, distro: str | None = None) -> dict[str, Any]:
    config_path = project_root / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    defaults = yaml.safe_load(default_config_path.read_text(encoding="utf-8")) if default_config_path and default_config_path.exists() else {}
    # The 'project' block carries per-project identity (name, working directory,
    # creation date) that always differs from the bundled defaults; excluding it
    # keeps the customized-parameters list focused on scientific/tool settings.
    customized = diff_configs(_drop_project(defaults), _drop_project(config))
    versions = capture_versions(project_root, use_wsl=use_wsl, distro=distro)
    sanity_path = project_root / "checks" / "sanity_checks.txt"
    payload = {
        "project": config.get("project", {}),
        "input": config.get("input", {}),
        "reference": config.get("reference", {}),
        "reference_integrity": _load_reference_integrity(
            project_root,
            str((config.get("input") or {}).get("type", "fastq")),
        ),
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


def _load_reference_integrity(project_root: Path, input_type: str) -> dict[str, Any]:
    """Load the realized reference lock for the GUI-side report fallback.

    The pipeline report writer has the same contract.  The selection-time YAML
    lock is intentionally not used here: it cannot describe the canonical bytes
    produced after decompression or GFF3 conversion.
    """
    if input_type in {"count_matrix", "microarray", "deseq2_results"}:
        return {}
    relative = Path("references/reference.lock.json")
    path = project_root / relative
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "status": "FAIL",
            "lock_path": relative.as_posix(),
            "error": f"Reference lock could not be read: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "status": "FAIL",
            "lock_path": relative.as_posix(),
            "error": "Reference lock root is not a JSON object.",
        }
    realized = dict(payload)
    realized["lock_path"] = relative.as_posix()
    return realized


def _reference_integrity_lines(payload: dict[str, Any]) -> list[str]:
    realized = payload.get("reference_integrity") or {}
    if not realized:
        return ["Realized reference lock: not recorded"]
    if realized.get("error"):
        return [
            f"Realized reference lock: {realized.get('status') or 'FAIL'}",
            f"Reference-lock error: {realized['error']}",
        ]

    lines = [
        f"Realized reference lock: {realized.get('status') or 'not recorded'} "
        f"({realized.get('lock_path') or 'references/reference.lock.json'})"
    ]
    for key, label in (("genome", "Genome"), ("annotation", "Annotation")):
        part = realized.get(key) or {}
        integrity = part.get("integrity") or {}
        content = part.get("content") or {}
        lines.extend([
            f"{label} source MD5: {integrity.get('source_md5') or 'not recorded'} "
            f"({integrity.get('md5_status') or 'not recorded'}; "
            f"configured={integrity.get('configured_md5') or 'not configured'})",
            f"{label} canonical SHA-256: "
            f"{integrity.get('canonical_sha256') or 'not recorded'}",
            f"{label} source/canonical bytes: "
            f"{integrity.get('source_bytes', 'not recorded')} / "
            f"{integrity.get('canonical_bytes', 'not recorded')}",
        ])
        if key == "genome":
            lines.append(
                f"{label} records/bases: {content.get('record_count', 'not recorded')} / "
                f"{content.get('total_bases', 'not recorded')}"
            )
        else:
            evidence = content.get("evidence_counts") or {}
            lines.append(
                f"{label} features (gene/exon/CDS): "
                f"{evidence.get('gene', 'not recorded')}/"
                f"{evidence.get('exon', 'not recorded')}/"
                f"{evidence.get('CDS', 'not recorded')}"
            )
    compatibility = realized.get("contig_compatibility") or {}
    counting = realized.get("counting_contract") or {}
    lines.append(
        "Configured counting contract: feature_type="
        f"{','.join(str(value) for value in (counting.get('feature_types') or [])) or 'not recorded'}; "
        f"attribute_type={counting.get('attribute_type') or 'not recorded'}; "
        f"eligible rows={counting.get('feature_rows', 'not recorded')}; "
        f"rows missing attribute={counting.get('feature_rows_missing_attribute', 'not recorded')}"
    )
    lines.append(
        "Compatible contigs (overlap/annotation): "
        f"{compatibility.get('overlap_contigs', 'not recorded')}/"
        f"{compatibility.get('annotation_contigs', 'not recorded')}"
    )
    fraction = compatibility.get("feature_row_overlap_fraction")
    threshold = compatibility.get("minimum_feature_row_overlap_fraction")
    lines.append(
        "Compatible annotation feature rows: "
        f"{compatibility.get('compatible_feature_rows', 'not recorded')}/"
        f"{compatibility.get('annotation_feature_rows', 'not recorded')} "
        f"({fraction:.2%}; required >= {threshold:.0%})"
        if isinstance(fraction, (int, float)) and isinstance(threshold, (int, float))
        else "Compatible annotation feature rows: not recorded"
    )
    return lines


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
    title = "BulkSeq Studio Analysis Run Summary"
    lines = [title, "=" * len(title), ""]
    project = payload.get("project", {})
    lines += ["Project", "-------", f"Project name: {project.get('name')}", f"Working directory: {project.get('working_directory')}", ""]
    input_cfg = payload.get("input", {}) or {}
    if input_cfg.get("type") == "deseq2_results":
        direction = input_cfg.get("deseq2_results_direction") or {}
        source = input_cfg.get("deseq2_results_provenance") or {}
        numerator = str(direction.get("numerator") or "").strip()
        denominator = str(direction.get("denominator") or "").strip()
        confirmed = direction.get("confirmed") is True
        confirmed_at = str(direction.get("confirmed_at") or "").strip()
        meaning = (f"positive log2FC = higher in {numerator} than {denominator}"
                   if numerator and denominator else "direction was not recorded")
        confirmation = "confirmed" if confirmed else "NOT confirmed"
        if confirmed_at:
            confirmation += f" at {confirmed_at}"
        lines += ["Imported-results provenance", "---------------------------",
                  f"Original basename (local provenance only): {source.get('original_basename') or 'not recorded'}",
                  f"Project copy: {source.get('project_copy') or input_cfg.get('deseq2_results') or 'not recorded'}",
                  f"Project-copy SHA-256: {source.get('sha256') or 'not recorded'}",
                  f"Imported at: {source.get('imported_at') or 'not recorded'}",
                  f"Imported rows: {source.get('row_count') or 'not recorded'}",
                  f"Imported columns: {', '.join(source.get('column_names') or []) or 'not recorded'}",
                  f"Log2FC direction: {meaning} ({confirmation}).",
                  f"Upstream differential-expression method: {source.get('upstream_method') or 'unknown'}",
                  f"Upstream LFC shrinkage: {source.get('lfc_shrinkage') or 'unknown'}",
                  f"Upstream adjusted-p method: {source.get('p_adjustment_method') or 'unknown'}",
                  "Local analysis: no differential-expression model or LFC shrinkage was run by "
                  "BulkSeq Studio for this results-only route.", ""]
    lines += ["Workflow", "--------", json.dumps(payload.get("workflow", {}), indent=2), ""]
    if input_cfg.get("type", "fastq") not in {
        "count_matrix", "microarray", "deseq2_results",
    }:
        lines += ["Reference integrity", "-------------------",
                  *_reference_integrity_lines(payload), ""]
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
