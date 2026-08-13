from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import psutil

from app.core.paths import usable_disk_free_bytes


@dataclass
class SystemResources:
    os: str
    cpu_model: str
    physical_cores: int
    logical_threads: int
    total_ram_gb: float
    available_ram_gb: float
    # Physical free space usable at disk_path. For a WSL-native path this is the drive
    # that backs the vhdx, NOT the vhdx's ~1 TB virtual size (see usable_disk_free_bytes).
    # There is deliberately no disk_total field: the WSL vhdx total is a phantom terabyte,
    # so a future percentage must not divide by it.
    disk_free_gb: float
    disk_path: str
    wsl_available: bool
    conda_available: bool
    mamba_available: bool
    snakemake_available: bool
    # The WSL2 VM's RAM/logical-CPU caps (0 if WSL is unavailable). Snakemake's
    # --cores budget is scheduled against these logical vCPUs.
    wsl_ram_gb: float = 0.0
    wsl_cpus: int = 0
    # Diagnostic topology only; this does not bound Snakemake's logical CPU budget.
    # Appended with a default so existing positional construction remains compatible.
    wsl_physical_cores: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_system(path: Path | None = None) -> SystemResources:
    disk_path = str(path or Path.cwd())
    vm = psutil.virtual_memory()
    host_physical = psutil.cpu_count(logical=False) or 1
    caps = _wsl_caps()
    # Preserve compatibility with callers/tests that monkeypatch the historical
    # private probe to return only RAM and logical CPUs.
    wsl_ram, wsl_cpus = caps[:2]
    wsl_physical = caps[2] if len(caps) >= 3 else 0
    return SystemResources(
        os=f"{psutil.WINDOWS and 'Windows' or 'POSIX'}",
        cpu_model=_cpu_name(),
        physical_cores=host_physical,
        logical_threads=psutil.cpu_count(logical=True) or 1,
        total_ram_gb=round(vm.total / (1024**3), 1),
        available_ram_gb=round(vm.available / (1024**3), 1),
        disk_free_gb=round(usable_disk_free_bytes(disk_path) / (1024**3), 1),
        disk_path=disk_path,
        wsl_available=_command_available(["wsl", "--status"]),
        conda_available=shutil.which("conda") is not None,
        mamba_available=shutil.which("mamba") is not None,
        snakemake_available=shutil.which("snakemake") is not None,
        wsl_ram_gb=wsl_ram,
        wsl_cpus=wsl_cpus,
        wsl_physical_cores=wsl_physical,
    )


def _parse_lscpu_physical_cores(lines: list[str]) -> int:
    """Count unique socket/core pairs from ``lscpu -p=CORE,SOCKET`` output."""
    pairs: set[tuple[int, int]] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            core, socket = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if core < 0 or socket < 0:
            continue
        pairs.add((socket, core))
    return len(pairs)


def _parse_wsl_probe(stdout: str) -> tuple[float, int, int]:
    """Parse labeled RAM/logical-CPU lines plus a guarded lscpu CSV block."""
    ram_gb, cpus = 0.0, 0
    lscpu_lines: list[str] = []
    in_lscpu = False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line == "LSCPU_BEGIN":
            in_lscpu = True
            continue
        if line == "LSCPU_END":
            in_lscpu = False
            continue
        if in_lscpu:
            lscpu_lines.append(line)
        elif line.startswith("RAM=") and line[4:].isdigit():
            ram_gb = round(int(line[4:]) / (1024 ** 2), 1)  # kB -> GB
        elif line.startswith("CPU=") and line[4:].isdigit():
            cpus = int(line[4:])
    physical = _parse_lscpu_physical_cores(lscpu_lines)
    if cpus > 0 and physical > cpus:
        physical = 0
    return ram_gb, cpus, physical


def _wsl_caps() -> tuple[float, int, int]:
    """RAM, logical CPUs, and physical cores available inside WSL2."""
    if not sys.platform.startswith("win"):
        return 0.0, 0, 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Labeled boundaries make the parse immune to login-shell banners. Parseable
    # lscpu emits one socket/core pair per logical CPU, so unique pairs count
    # physical cores without assuming a fixed SMT ratio.
    probe = (
        "echo RAM=$(awk '/MemTotal/{print $2}' /proc/meminfo); "
        "echo CPU=$(nproc); echo LSCPU_BEGIN; "
        "lscpu -p=CORE,SOCKET 2>/dev/null || true; echo LSCPU_END"
    )
    try:
        proc = subprocess.run(
            ["wsl", "--", "bash", "-lc", probe],
            capture_output=True, text=True, timeout=15, check=False, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0, 0
    return _parse_wsl_probe(proc.stdout or "")


def recommend_rule_threads(total_threads: int) -> dict[str, int]:
    """Derive per-rule thread requests from the schedulable CPU pool.

    Alignment is the long pole for the bundled sequencing benchmarks.  Give each
    alignment at most half of the global pool so Snakemake can run two 24-GB STAR
    jobs concurrently when the memory budget permits it.  Small pools retain the
    established four-thread request, clamped to the pool by construction.
    """
    total = max(int(total_threads), 1)
    half_pool = max(total // 2, 1)
    alignment_threads = min(total, 12, max(4, half_pool))
    secondary_alignment_threads = min(total, 8, max(4, half_pool))
    return {
        "fasterq_dump": min(4, total),
        "fastqc": 1,
        "fastp": min(4, total),
        "sortmerna": min(4, total),
        "star_index": min(12, total),
        "star_align": alignment_threads,
        "hisat2_align": secondary_alignment_threads,
        "salmon_quant": secondary_alignment_threads,
        "featurecounts": min(6, total),
        "deseq2": min(2, total),
        "multiqc": 1,
    }


def recommend_profile(system: SystemResources, profile: str = "balanced") -> dict[str, int | str]:
    # Snakemake --cores counts schedulable logical CPUs. The pipeline executes in
    # WSL, so use its vCPU allocation when known and the host logical count otherwise.
    schedulable_cpus = max(int(system.wsl_cpus or system.logical_threads), 1)
    # Keep memory capped by WSL when known so STAR does not exceed the VM and swap.
    ram = max(system.wsl_ram_gb or system.total_ram_gb, 1)
    if profile == "low":
        total_threads = max(1, (schedulable_cpus * 45) // 100)
        total_memory_gb = max(2, int(ram * 0.55))
    elif profile == "high":
        total_threads = max(1, (schedulable_cpus * 90) // 100)
        total_memory_gb = max(4, int(ram - 2))
    else:
        total_threads = max(1, (schedulable_cpus * 75) // 100)
        reserve = 8 if ram >= 32 else 4
        total_memory_gb = max(4, int(min(ram * 0.75, ram - reserve)))
    rule_threads = recommend_rule_threads(total_threads)
    # Match the workflow's declared 24-GB STAR reservation, clamped only when the
    # entire selected pool is smaller.  Keeping this per-job value allows two
    # alignments in a 60-GB pool while preserving headroom for the scheduler/UI.
    star_mem = min(total_memory_gb, 24)
    return {
        "profile": profile,
        "total_threads": total_threads,
        "total_memory_gb": total_memory_gb,
        "fastp_threads": rule_threads["fastp"],
        "star_align_threads": rule_threads["star_align"],
        "star_align_memory_gb": star_mem,
        "featurecounts_threads": rule_threads["featurecounts"],
        "deseq2_threads": rule_threads["deseq2"],
    }


def _command_available(command: list[str]) -> bool:
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cpu_name() -> str:
    # wmic is deprecated/removed on newer Windows 11 builds; read the processor
    # name from the registry on Windows, fall back to platform.processor().
    if psutil.WINDOWS:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return str(value).strip()
            finally:
                winreg.CloseKey(key)
        except Exception:
            pass
    return platform.processor() or "Unknown CPU"
