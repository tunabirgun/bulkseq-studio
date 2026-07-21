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
    # The WSL2 VM's RAM/CPU caps (0 if WSL is unavailable). The pipeline runs in
    # WSL, so these — not the Windows host totals — are the binding constraints.
    wsl_ram_gb: float = 0.0
    wsl_cpus: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_system(path: Path | None = None) -> SystemResources:
    disk_path = str(path or Path.cwd())
    vm = psutil.virtual_memory()
    wsl_ram, wsl_cpus = _wsl_caps()
    return SystemResources(
        os=f"{psutil.WINDOWS and 'Windows' or 'POSIX'}",
        cpu_model=_cpu_name(),
        physical_cores=psutil.cpu_count(logical=False) or 1,
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
    )


def _wsl_caps() -> tuple[float, int]:
    """RAM (GB) and CPU count actually available inside the WSL2 VM, or (0, 0).

    The pipeline runs in WSL, whose memory is capped (default ~50% of host) well
    below the Windows total; recommending against the host RAM over-subscribes
    memory-heavy jobs (STAR) and thrashes swap.
    """
    if not sys.platform.startswith("win"):
        return 0.0, 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Labeled lines so a login-shell banner/MOTD can't shift the parse.
    probe = "echo RAM=$(awk '/MemTotal/{print $2}' /proc/meminfo); echo CPU=$(nproc)"
    try:
        proc = subprocess.run(
            ["wsl", "--", "bash", "-lc", probe],
            capture_output=True, text=True, timeout=15, check=False, creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0
    ram_gb, cpus = 0.0, 0
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("RAM=") and line[4:].isdigit():
            ram_gb = round(int(line[4:]) / (1024 ** 2), 1)  # kB -> GB
        elif line.startswith("CPU=") and line[4:].isdigit():
            cpus = int(line[4:])
    return ram_gb, cpus


def recommend_profile(system: SystemResources, profile: str = "balanced") -> dict[str, int | str]:
    # The pipeline executes in WSL, so cap recommendations by the WSL2 limits when
    # known — otherwise STAR's declared memory exceeds the VM and swaps.
    threads = max(system.wsl_cpus or system.logical_threads, 1)
    ram = max(system.wsl_ram_gb or system.total_ram_gb, 1)
    if profile == "low":
        total_threads = max(1, int(threads * 0.45))
        total_memory_gb = max(2, int(ram * 0.55))
    elif profile == "high":
        total_threads = max(1, int(threads * 0.9))
        total_memory_gb = max(4, int(ram - 2))
    else:
        total_threads = max(1, int(threads * 0.75))
        reserve = 8 if ram >= 32 else 4
        total_memory_gb = max(4, int(min(ram * 0.75, ram - reserve)))
    star_threads = min(total_threads, 12)
    # STAR is the memory bottleneck, so it may use the whole allocated budget.
    star_mem = total_memory_gb
    return {
        "profile": profile,
        "total_threads": total_threads,
        "total_memory_gb": total_memory_gb,
        "fastp_threads": min(4, total_threads),
        "star_align_threads": star_threads,
        "star_align_memory_gb": star_mem,
        "featurecounts_threads": min(6, total_threads),
        "deseq2_threads": min(2, total_threads),
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
