from __future__ import annotations

import base64
import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.paths import app_root, wsl_has_working_distro


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

# Tool probe lists. hisat2/salmon/gffread are the alternative-aligner-route tools; they
# are PROBED and DISPLAYED here but deliberately NOT part of the has_*_core_environment
# "default-route ready" gate below, so an existing STAR-route user is never forced to
# repair a working env. Keep this set in sync with workflow/envs/bulkseq_core.yaml,
# bulkseq_full.yaml, and the setup_wsl_bioenv.sh verification loop.
# Optional-route CLI tools added in 0.16.0 (trimmers, rRNA tools, contamination screen, RSeQC).
# Like hisat2/salmon/gffread these are PROBED and DISPLAYED so the user sees whether every route is
# installed, but they are intentionally NOT part of the default-route gate (has_*_core_environment),
# so a working STAR-route env is never flagged incomplete; each route is guarded at run time too.
OPTIONAL_ROUTE_TOOLS = {
    "aria2c": "Faster multi-connection FASTQ download (aria2; falls back to a single stream)",
    "trim_galore": "Read trimming (Trim Galore route)",
    "trimmomatic": "Read trimming (Trimmomatic route)",
    "sortmerna": "rRNA filtering (SortMeRNA)",
    "ribodetector_cpu": "rRNA filtering (RiboDetector)",
    "fastq_screen": "Contamination screen (FastQ Screen)",
    "read_distribution.py": "Extended alignment QC (RSeQC)",
    "genePredToBed": "RSeQC BED12 from the annotation (UCSC)",
}

BIOINFORMATICS_TOOLS = {
    "fastqc": "Raw/post-trim read QC",
    "multiqc": "QC report aggregation",
    "fastp": "Read trimming",
    "STAR": "Reference alignment (STAR route)",
    "hisat2": "Reference alignment (HISAT2 route)",
    "salmon": "Pseudo-alignment quantification (Salmon route)",
    "gffread": "Transcriptome FASTA for the Salmon route",
    "featureCounts": "Gene-level counting",
    "samtools": "BAM indexing and summaries",
    "Rscript": "DESeq2/enrichment/figures",
    **OPTIONAL_ROUTE_TOOLS,
}

# The R-package probe LOAD-tests the whole Bioconductor stack (requireNamespace loads each
# namespace + its compiled code), which is slow cold — measured ~9s warm, so allow generous
# headroom on a cold/slow machine. It runs on a background thread (ReadinessCheckThread), so a
# long wait never blocks the UI. A short timeout here would false-fail a healthy-but-cold env.
R_PROBE_TIMEOUT_SEC = 120

WSL_ENV_NAME = "bulkseq"
WSL_TOOLS = {
    "snakemake": "Snakemake workflow engine",
    "fastqc": "Raw/post-trim read QC",
    "multiqc": "QC report aggregation",
    "fastp": "Read trimming",
    "STAR": "Reference alignment (STAR route)",
    "hisat2": "Reference alignment (HISAT2 route)",
    "salmon": "Pseudo-alignment quantification (Salmon route)",
    "gffread": "Transcriptome FASTA for the Salmon route",
    "featureCounts": "Gene-level counting",
    "samtools": "BAM indexing and summaries",
    "Rscript": "DESeq2/enrichment/figures",
    **OPTIONAL_ROUTE_TOOLS,
}

# R analysis packages that must be present for the differential-expression, enrichment, and
# optional-engine routes. Probed as one item so the R stack's completeness (not just "Rscript
# exists") is verified — including the 0.16.0 additions edgeR, limma-voom (limma) and GSVA.
# GEOquery + affy cover the microarray/limma route, so a core-only env that lacks the
# microarray R stack is flagged instead of passing silently (an error-127 trap). GO.db, DOSE,
# enrichplot and fgsea are the clusterProfiler enrichment cluster (GO.db is a transitive dep a
# solve can drop, which broke enrichment ~30 min into a run), and STRINGdb backs the PPI network
# — probing them here catches a broken enrichment/PPI env from Check Environment, before a run.
# Presence-checked (installed.packages), not load-tested, to stay under the 20s WSL probe timeout.
R_ANALYSIS_PACKAGES = ("DESeq2", "edgeR", "limma", "GSVA", "clusterProfiler", "GO.db", "DOSE",
                       "enrichplot", "fgsea", "STRINGdb", "apeglm", "ashr", "GEOquery", "affy",
                       "metaRNASeq", "metafor", "HTSFilter")


@dataclass(frozen=True)
class ReadinessItem:
    name: str
    status: str
    detail: str
    required_for: str


def check_readiness() -> list[ReadinessItem]:
    is_windows = sys.platform.startswith("win")
    items: list[ReadinessItem] = []
    items.append(ReadinessItem("Python", "PASS", sys.executable, "GUI"))
    for import_name, package_name in PYTHON_PACKAGES.items():
        status = "PASS" if importlib.util.find_spec(import_name) is not None else "FAIL"
        detail = "installed" if status == "PASS" else f"missing; install package {package_name}"
        items.append(ReadinessItem(package_name, status, detail, "GUI/core features"))
    for command, purpose in EXTERNAL_TOOLS.items():
        # WSL is a Windows-only execution path; on Linux the pipeline runs natively.
        if command == "wsl" and not is_windows:
            items.append(ReadinessItem("wsl", "PASS", "not applicable (native execution)", purpose))
            continue
        status = "PASS" if shutil.which(command) else ("WARNING" if command == "mamba" else "REVIEW_REQUIRED")
        detail = shutil.which(command) or "not found on PATH"
        items.append(ReadinessItem(command, status, detail, purpose))
    for command, purpose in BIOINFORMATICS_TOOLS.items():
        status = "PASS" if shutil.which(command) else "REVIEW_REQUIRED"
        not_found = "not found on PATH or inside this Windows session" if is_windows else "not found on PATH"
        detail = shutil.which(command) or not_found
        items.append(ReadinessItem(command, status, detail, purpose))
    # On Windows the bioinformatics tools live inside WSL; probe it. On Linux the native
    # PATH probes above ARE the environment check, so the WSL probe is skipped.
    if is_windows:
        # wsl.exe present is not enough: a distro must be installed AND start (a missing or
        # broken ext4.vhdx passes shutil.which yet fails every `wsl -- bash`). Probe a real
        # launch so a no-distro machine gets a clear "install a distribution" action instead
        # of a confusing "micromamba missing" that no in-WSL install can fix.
        if shutil.which("wsl") is None:
            items.append(ReadinessItem("WSL distribution", "REVIEW_REQUIRED",
                                       "wsl.exe is not available", "Linux execution"))
            items.append(ReadinessItem(f"WSL env:{WSL_ENV_NAME}", "REVIEW_REQUIRED",
                                       "waiting for WSL2", "Linux bioinformatics tools"))
        elif wsl_has_working_distro():
            items.append(ReadinessItem("WSL distribution", "PASS",
                                       "a Linux distribution is installed and starts", "Linux execution"))
            items.extend(check_wsl_bulkseq_environment())
        else:
            items.append(ReadinessItem("WSL distribution", "REVIEW_REQUIRED",
                                       "no Linux distribution is installed in WSL, or it will not start "
                                       "(a missing/broken virtual disk)", "Linux execution"))
            items.append(ReadinessItem(f"WSL env:{WSL_ENV_NAME}", "REVIEW_REQUIRED",
                                       "waiting for a WSL Linux distribution", "Linux bioinformatics tools"))
    else:
        items.append(_native_r_packages_item())
    return items


def check_wsl_bulkseq_environment(distro: str | None = None, env_name: str = WSL_ENV_NAME) -> list[ReadinessItem]:
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
        log_paths = _tool_paths_from_install_log(env_name)
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
    rp = _run_wsl(distro, _wsl_r_packages_probe_command(env_name, R_ANALYSIS_PACKAGES), timeout=R_PROBE_TIMEOUT_SEC)
    items.append(_r_packages_item("WSL R packages", _short_output(rp), rp.returncode == 0))
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


def _run_wsl(distro: str | None, command: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    # Pass the command base64-encoded and decode it inside WSL. A bare complex command
    # (loops, arrays, nested $()/quoting) does NOT survive subprocess -> wsl.exe command-line
    # reconstruction and silently mangles — the base64 payload is pure [A-Za-z0-9+/=], so it
    # round-trips intact and the real command runs exactly as written.
    b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    inner = f"echo {b64} | base64 -d | bash"
    cmd = ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", inner]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["wsl"], 1, "", str(exc))


def _wsl_env_prefix_command(env_name: str) -> str:
    # An if-elif chain, NOT a bash array or `for` loop: neither survives the
    # `wsl -- bash -lc "<string>"` subprocess round-trip (wsl.exe reconstructs the command line
    # and mangles the loop/array, so an existing env probed as "not found"). The three candidates
    # cover every install path the app's setup creates ($HOME/micromamba/envs is the default).
    e = env_name
    return (
        f'if [ -d "$HOME/micromamba/envs/{e}" ]; then echo "$HOME/micromamba/envs/{e}"; '
        f'elif [ -d "/root/micromamba/envs/{e}" ]; then echo "/root/micromamba/envs/{e}"; '
        f'elif [ -d "$HOME/.local/share/mamba/envs/{e}" ]; then echo "$HOME/.local/share/mamba/envs/{e}"; '
        'else exit 1; fi'
    )


def _wsl_tool_probe_command(env_name: str, tool: str) -> str:
    prefix_command = _wsl_env_prefix_command(env_name)
    return (
        f"tool={tool!r}; "
        f"env_prefix=$({prefix_command}); "
        "path=\"$env_prefix/bin/$tool\"; "
        "test -x \"$path\" && echo \"$path\""
    )


def _r_packages_check_code(packages: tuple[str, ...]) -> str:
    # One-liner R that prints OK or "cannot load: pkg,pkg". LOAD-tests each package
    # (requireNamespace actually loads the namespace + its compiled code), not just checks
    # presence — a package can be installed yet fail to load when a transitive dependency like
    # GO.db was dropped, or an r-base bump left it ABI-incompatible. A presence check
    # (installed.packages) would call that "OK" and hide the exact break this probe exists to
    # catch. This is why the caller gives it R_PROBE_TIMEOUT_SEC: a cold full-stack load is slow.
    pkg_vec = ", ".join(f'"{p}"' for p in packages)
    return (
        f'p<-c({pkg_vec}); '
        'ld<-function(x) isTRUE(tryCatch(suppressWarnings(suppressMessages('
        'requireNamespace(x, quietly=TRUE))), error=function(e) FALSE)); '
        'm<-p[!vapply(p, ld, logical(1))]; '
        'cat(if (length(m)==0) "OK" else paste("cannot load:", paste(m, collapse=",")))'
    )


def _wsl_r_packages_probe_command(env_name: str, packages: tuple[str, ...]) -> str:
    prefix_command = _wsl_env_prefix_command(env_name)
    return (
        f"env_prefix=$({prefix_command}); "
        f"\"$env_prefix/bin/Rscript\" -e '{_r_packages_check_code(packages)}'"
    )


def _r_packages_item(name: str, out: str, ok: bool) -> ReadinessItem:
    if ok and out == "OK":
        return ReadinessItem(name, "PASS", "R analysis stack installed (DE engines, enrichment incl. GO.db, PPI, microarray)",
                             "DESeq2 / engines / enrichment / GSVA")
    return ReadinessItem(name, "REVIEW_REQUIRED", out or "R analysis packages not verified",
                         "DESeq2 / engines / enrichment / GSVA")


def _native_r_packages_item() -> ReadinessItem:
    if shutil.which("Rscript") is None:
        return ReadinessItem("R packages", "REVIEW_REQUIRED", "Rscript not on PATH",
                             "DESeq2 / engines / enrichment / GSVA")
    try:
        rp = subprocess.run(["Rscript", "-e", _r_packages_check_code(R_ANALYSIS_PACKAGES)],
                            capture_output=True, text=True, timeout=R_PROBE_TIMEOUT_SEC, check=False)
        text = (rp.stdout or rp.stderr or "").strip()
        out = text.splitlines()[-1][:240] if text else ""
        return _r_packages_item("R packages", out, rp.returncode == 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ReadinessItem("R packages", "REVIEW_REQUIRED", str(exc), "DESeq2 / engines / enrichment / GSVA")


def _short_output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout or result.stderr or "").strip()
    if not text:
        return ""
    return text.splitlines()[-1][:240]


def _tool_paths_from_install_log(env_name: str = WSL_ENV_NAME) -> dict[str, str]:
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
        # Only trust a recorded path if it is for THIS env. A stale log from a differently
        # named env (e.g. a *_verify build env) must not stand in for the real one and mark
        # a missing/broken environment as PASS — the exact false-positive behind an error-127.
        if len(parts) >= 2 and parts[1].startswith("/") and f"/envs/{env_name}/" in parts[1]:
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
    # Plain, grouped view for end users: items sorted into Ready / Needs attention /
    # Optional, no raw status tokens or nested "(required_for)" jargon. The dense
    # per-item detail is kept only where it helps (things that are not ready).
    ready, attention, optional = [], [], []
    for item in items:
        if item.status == "PASS":
            ready.append(f"  ready    {item.name}")
        elif item.status == "WARNING":
            optional.append(f"  optional {item.name} - {item.detail}")
        else:
            attention.append(f"  missing  {item.name} - {item.detail}")

    blocks: list[str] = []
    if attention:
        blocks.append("Needs attention\n" + "\n".join(attention))
    if optional:
        blocks.append("Optional\n" + "\n".join(optional))
    if ready:
        blocks.append("Ready\n" + "\n".join(ready))
    return "\n\n".join(blocks)


def _native_readiness_actions(by_name: dict[str, ReadinessItem]) -> list[str]:
    actions: list[str] = []
    missing = [name for name in ("snakemake", "STAR", "featureCounts", "samtools", "fastp", "fastqc", "multiqc")
               if by_name.get(name, ReadinessItem(name, "REVIEW_REQUIRED", "", "")).status != "PASS"]
    if missing:
        actions.append(
            "Activate the bulkseq environment (e.g. micromamba activate bulkseq), or create it from "
            "workflow/envs/bulkseq.lock.yaml, so these tools are on PATH: " + ", ".join(missing) + ".")
    if by_name.get("Rscript", ReadinessItem("Rscript", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("Install the R/DESeq2 stack in the environment so Rscript is available for "
                       "DESeq2, enrichment, and figures.")
    if not actions:
        actions.append("Setup is ready for native Snakemake runs.")
    return actions


def next_readiness_actions(items: list[ReadinessItem]) -> list[str]:
    by_name = {item.name: item for item in items}
    if not sys.platform.startswith("win"):
        return _native_readiness_actions(by_name)
    actions: list[str] = []
    if by_name.get("wsl", ReadinessItem("wsl", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("Click Install/Enable WSL, then reboot Windows if prompted.")
        return actions
    if by_name.get("WSL distribution", ReadinessItem("WSL distribution", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("WSL2 is installed but has no working Linux distribution. Click Install Ubuntu "
                       "distribution (approve the Windows elevation prompt), then Re-check.")
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
    missing_alt = [t.replace("WSL ", "") for t in ("WSL salmon", "WSL gffread", "WSL hisat2") if by_name.get(t, ReadinessItem(t, "REVIEW_REQUIRED", "", "")).status != "PASS"]
    if missing_alt:
        actions.append("Salmon and HISAT2 aligner routes need additional tools not found in the env. Click Install/Repair Core WSL Env to install: " + ", ".join(missing_alt) + ".")
    if by_name.get("WSL Rscript", ReadinessItem("WSL Rscript", "REVIEW_REQUIRED", "", "")).status != "PASS":
        actions.append("After core tools pass, click Install Full R/DESeq2 Stack to enable DESeq2/enrichment/figure execution.")
    # Rscript exists but the Bioconductor packages will not load (a dropped GO.db or an r-base
    # drift): an in-place install cannot repair an ABI-inconsistent stack, so route to a clean
    # rebuild from the pinned lock instead of "Install Full Stack".
    elif by_name.get("WSL R packages", ReadinessItem("WSL R packages", "PASS", "", "")).status != "PASS":
        actions.append("Rscript is installed but the R/Bioconductor packages will not load (usually a dropped "
                       "GO.db or an r-base drift). An in-place install will not repair this — click Rebuild "
                       "from scratch to recreate the environment from the pinned lock.")
    if not actions:
        actions.append("Setup is ready for WSL-based Snakemake runs.")
    return actions


def has_wsl_core_environment(items: list[ReadinessItem]) -> bool:
    # Gate = the default STAR -> featureCounts -> DESeq2 route. hisat2/salmon/gffread are
    # intentionally excluded so a working STAR-route env is never flagged "incomplete";
    # the Salmon/HISAT2 routes are guarded at run time inside their Snakemake rules.
    by_name = {item.name: item for item in items}
    required = ("WSL env:bulkseq", "WSL snakemake", "WSL fastqc", "WSL multiqc", "WSL fastp", "WSL STAR", "WSL featureCounts", "WSL samtools")
    return all(by_name.get(name, ReadinessItem(name, "REVIEW_REQUIRED", "", "")).status == "PASS" for name in required)


def has_native_core_environment(items: list[ReadinessItem]) -> bool:
    # Native (Linux/macOS) counterpart of has_wsl_core_environment: the core tools are on PATH.
    by_name = {item.name: item for item in items}
    required = ("snakemake", "STAR", "featureCounts", "samtools", "fastp", "fastqc", "multiqc")
    return all(by_name.get(name, ReadinessItem(name, "REVIEW_REQUIRED", "", "")).status == "PASS" for name in required)
