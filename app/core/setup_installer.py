from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.core.paths import app_root, windows_to_wsl_path


def windows_wsl_admin_script() -> Path:
    return app_root() / "scripts" / "setup_windows_wsl_admin.ps1"


def windows_wsl_admin_launcher() -> Path:
    return app_root() / "scripts" / "launch_wsl_setup_admin.bat"


def wsl_bioenv_script() -> Path:
    return app_root() / "scripts" / "setup_wsl_bioenv.sh"


def build_wsl_admin_install_command(distro: str = "Ubuntu") -> list[str]:
    return [str(windows_wsl_admin_launcher()), distro]


def launch_wsl_admin_install(distro: str = "Ubuntu") -> subprocess.Popen[str]:
    return subprocess.Popen(build_wsl_admin_install_command(distro), text=True)


def build_wsl_bioenv_command(env_name: str = "bulkseq", distro: str | None = None,
                             profile: str = "core", rebuild: bool = False) -> list[str]:
    repo = windows_to_wsl_path(app_root())
    script = windows_to_wsl_path(wsl_bioenv_script())
    # BULKSEQ_REBUILD=1 tells the setup script to remove and recreate the env from scratch
    # (a clean rebuild), instead of an in-place update that can leave R/Bioconductor mixed.
    prefix = "export BULKSEQ_REBUILD=1 && " if rebuild else ""
    inner = f"{prefix}cd '{repo}' && bash '{script}' '{env_name}' '{profile}'"
    return ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", inner]


def launch_wsl_bioenv_install(env_name: str = "bulkseq", distro: str | None = None,
                              profile: str = "core", rebuild: bool = False) -> subprocess.Popen[str]:
    return subprocess.Popen(
        build_wsl_bioenv_command(env_name, distro, profile, rebuild),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        stdin=subprocess.DEVNULL,
    )


def build_native_bioenv_command(env_name: str = "bulkseq", profile: str = "core") -> list[str]:
    # Native Linux/macOS: run the same setup script directly (no `wsl` wrapper). It
    # creates or `env update`s the micromamba environment from the profile's yaml,
    # which installs any tools missing from an older environment (the repair path).
    return ["bash", str(wsl_bioenv_script()), env_name, profile]


def launch_native_bioenv_install(env_name: str = "bulkseq", profile: str = "core",
                                 rebuild: bool = False) -> subprocess.Popen[str]:
    # A clean rebuild is requested via the BULKSEQ_REBUILD env var the setup script reads.
    env = None
    if rebuild:
        env = dict(os.environ)
        env["BULKSEQ_REBUILD"] = "1"
    return subprocess.Popen(
        build_native_bioenv_command(env_name, profile),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        stdin=subprocess.DEVNULL,
        env=env,
    )
