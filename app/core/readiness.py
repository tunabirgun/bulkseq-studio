from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.paths import app_root


PYTHON_PACKAGES = {
    "PySide6": "PySide6",
    "pandas": "pandas",
    "pydantic": "pydantic",
    "yaml": "PyYAML",
    "psutil": "psutil",
    "openpyxl": "openpyxl",
}

EXTERNAL_TOOLS = {
    "wsl": "WSL2 for Linux-based execution on Windows",
    "conda": "Conda or Mamba environment manager",
    "mamba": "Mamba environment manager, optional but recommended",
    "snakemake": "Snakemake workflow engine",
}

BIOINFORMATICS_TOOLS = {
    "fastqc": "Raw/post-trim read QC",
    "multiqc": "QC report aggregation",
    "fastp": "Read trimming",
    "STAR": "Reference alignment",
    "featureCounts": "Gene-level counting",
    "samtools": "BAM indexing and summaries",
    "Rscript": "DESeq2/enrichment/figures",
}

WSL_ENV_NAME = "bulkseq"
WSL_TOOLS = {
    "snakemake": "Snakemake workflow engine",
    "fastqc": "Raw/post-trim read QC",
    "multiqc": "QC report aggregation",
    "fastp": "Read trimming",
    "STAR": "Reference alignment",
    "featureCounts": "Gene-level counting",
    "samtools": "BAM indexing and summaries",
    "Rscript": "DESeq2/enrichment/figures",
}


@dataclass(frozen=True)
class ReadinessItem:
    name: str
    status: str
    detail: str
    required_for: str


def check_readiness() -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    items.append(ReadinessItem("Python", "PASS", sys.executable, "GUI"))
    for import_name, package_name in PYTHON_PACKAGES.items():
        status = "PASS" if importlib.util.find_spec(import_name) is not None else "FAIL"
        detail = "installed" if status == "PASS" else f"missing; install package {package_name}"
        items.append(ReadinessItem(package_name, status, detail, "GUI/core features"))
    for command, purpose in EXTERNAL_TOOLS.items():
        status = "PASS" if shutil.which(command) else ("WARNING" if command == "mamba" else "REVIEW_REQUIRED")
        detail = shutil.which(command) or "not found on PATH"
        items.append(ReadinessItem(command, status, detail, purpose))
    for command, purpose in BIOINFORMATICS_TOOLS.items():
        status = "PASS" if shutil.which(command) else "REVIEW_REQUIRED"
        detail = shutil.which(command) or "not found on PATH or inside this Windows session"
        items.append(ReadinessItem(command, status, detail, purpose))
    items.extend(check_wsl_bulkseq_environment())
    return items


def check_wsl_bulkseq_environment(distro: str = "Ubuntu", env_name: str = WSL_ENV_NAME) -> list[ReadinessItem]:
    if shutil.which("wsl") is None:
        return [ReadinessItem(f"WSL env:{env_name}", "REVIEW_REQUIRED", "wsl.exe is not available", "Linux bioinformatics tools")]

    micromamba_probe = _run_wsl(distro, "test -x ~/.local/bin/micromamba && echo ~/.local/bin/micromamba")
    if micromamba_probe.returncode != 0:
        return [
            ReadinessItem("WSL micromamba", "REVIEW_REQUIRED", _short_output(micromamba_probe) or "micromamba not installed in WSL", "WSL package manager"),
            ReadinessItem(f"WSL env:{env_name}", "REVIEW_REQUIRED", "waiting for micromamba installation", "Linux bioinformatics tools"),
        ]

    items = [ReadinessItem("WSL micromamba", "PASS", _short_output(micromamba_probe), "WSL package manager")]
    env_prefix_command = _wsl_env_prefix_command(env_name)
    probe = _run_wsl(distro, env_prefix_command)
    if probe.returncode != 0:
        log_paths = _tool_paths_from_install_log()
        if _core_tools_present(log_paths):
            items.append(ReadinessItem(f"WSL env:{env_name}", "PASS", "micromamba environment found in setup log", "Linux bioinformatics tools"))
            items.extend(_items_from_tool_paths(log_paths))
            return items
        detail = _short_output(probe) or f"micromamba environment directory '{env_name}' not found"
        items.append(ReadinessItem(f"WSL env:{env_name}", "REVIEW_REQUIRED", detail, "Linux bioinformatics tools"))
        return items

    items.append(ReadinessItem(f"WSL env:{env_name}", "PASS", "micromamba environment found", "Linux bioinformatics tools"))
    log_paths = _tool_paths_from_install_log()
    for command, purpose in WSL_TOOLS.items():
        result = _run_wsl(
            distro,
            _wsl_tool_probe_command(env_name, command),
        )
        if result.returncode == 0:
            items.append(ReadinessItem(f"WSL {command}", "PASS", _short_output(result), purpose))
        elif command in log_paths:
            items.append(ReadinessItem(f"WSL {command}", "PASS", f"{log_paths[command]} (from setup log)", purpose))
        else:
            items.append(ReadinessItem(f"WSL {command}", "REVIEW_REQUIRED", _short_output(result) or "not found in WSL bulkseq environment", purpose))
    return items


def missing_python_packages() -> list[str]:
    missing: list[str] = []
    for import_name, package_name in PYTHON_PACKAGES.items():
        if importlib.util.find_spec(import_name) is None:
            missing.append(package_name)
    return missing


def install_python_packages(requirements_path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _run_wsl(distro: str, command: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["wsl"], 1, "", str(exc))


def _wsl_env_prefix_command(env_name: str) -> str:
    return (
        f"env_name={env_name!r}; "
        "candidates=(\"$HOME/micromamba/envs/$env_name\" \"/root/micromamba/envs/$env_name\" \"$HOME/.local/share/mamba/envs/$env_name\"); "
        "for candidate in \"${candidates[@]}\"; do "
        "  if [ -d \"$candidate\" ]; then echo \"$candidate\"; exit 0; fi; "
        "done; "
        "if [ -x ~/.local/bin/micromamba ]; then "
        "  prefix=$(~/.local/bin/micromamba env list 2>/dev/null | awk -v env=\"$env_name\" '$1 == env {print $NF; exit}'); "
        "  if [ -n \"$prefix\" ] && [ -d \"$prefix\" ]; then echo \"$prefix\"; exit 0; fi; "
        "fi; "
        "exit 1"
    )


def _wsl_tool_probe_command(env_name: str, tool: str) -> str:
    prefix_command = _wsl_env_prefix_command(env_name)
    return (
        f"tool={tool!r}; "
        f"env_prefix=$({prefix_command}); "
        "path=\"$env_prefix/bin/$tool\"; "
        "test -x \"$path\" && echo \"$path\""
    )


def _short_output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout or result.stderr or "").strip()
    if not text:
        return ""
    return text.splitlines()[-1][:240]


def _tool_paths_from_install_log() -> dict[str, str]:
    log_path = app_root() / "scripts" / "logs" / "wsl_bioenv_install.log"
    if not log_path.exists():
        return {}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    marker = "Verification:"
    if marker not in text:
        return {}
    block = text.rsplit(marker, 1)[-1]
    paths: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Open a new WSL shell"):
            break
        parts = stripped.split()
        if len(parts) >= 2 and parts[1].startswith("/"):
            paths[parts[0]] = parts[1]
    return paths


def _core_tools_present(paths: dict[str, str]) -> bool:
    return all(tool in paths for tool in ("snakemake", "fastqc", "multiqc", "fastp", "STAR", "featureCounts", "samtools"))


def _items_from_tool_paths(paths: dict[str, str]) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    for command, purpose in WSL_TOOLS.items():
        if command in paths:
            items.append(ReadinessItem(f"WSL {command}", "PASS", f"{paths[command]} (from setup log)", purpose))
        else:
            items.append(ReadinessItem(f"WSL {command}", "REVIEW_REQUIRED", "not found in WSL setup log", purpose))
    return items


def readiness_summary(items: list[ReadinessItem]) -> str:
    lines = []
    for item in items:
        lines.append(f"{item.status}: {item.name} - {item.detail} ({item.required_for})")
    return "\n".join(lines)


def next_readiness_actions(items: list[ReadinessItem]) -> list[str]:
    by_name = {item.name: item for item in items}
    actions: list[str] = []
    if by_name.get("wsl", ReadinessItem("wsl", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("Click Install/Enable WSL, then reboot Windows if prompted.")
        return actions
    if by_name.get("WSL micromamba", ReadinessItem("WSL micromamba", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("Click Install/Repair Core WSL Env to install micromamba inside WSL.")
        return actions
    if by_name.get("WSL env:bulkseq", ReadinessItem("WSL env:bulkseq", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("Click Install/Repair Core WSL Env to create the bulkseq environment.")
        return actions
    missing_core = [name for name in ("WSL snakemake", "WSL fastqc", "WSL multiqc", "WSL fastp", "WSL STAR", "WSL featureCounts", "WSL samtools") if by_name.get(name, ReadinessItem(name, "REVIEW_REQUIRED", "", "")).status != "PASS"]
    if missing_core:
        actions.append("Click Install/Repair Core WSL Env to repair missing core tools: " + ", ".join(missing_core) + ".")
    if by_name.get("WSL Rscript", ReadinessItem("WSL Rscript", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("After core tools pass, click Install Full R/DESeq2 Stack to enable DESeq2/enrichment/figure execution.")
    if not actions:
        actions.append("Setup is ready for WSL-based Snakemake runs.")
    return actions


def has_wsl_core_environment(items: list[ReadinessItem]) -> bool:
    by_name = {item.name: item for item in items}
    required = ("WSL env:bulkseq", "WSL snakemake", "WSL fastqc", "WSL multiqc", "WSL fastp", "WSL STAR", "WSL featureCounts", "WSL samtools")
    return all(by_name.get(name, ReadinessItem(name, "REVIEW_REQUIRED", "", "")).status == "PASS" for name in required)
