from __future__ import annotations

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


def build_wsl_bioenv_command(env_name: str = "bulkseq", distro: str | None = None, profile: str = "core") -> list[str]:
    repo = windows_to_wsl_path(app_root())
    script = windows_to_wsl_path(wsl_bioenv_script())
    inner = f"cd '{repo}' && bash '{script}' '{env_name}' '{profile}'"
    return ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", inner]


def launch_wsl_bioenv_install(env_name: str = "bulkseq", distro: str | None = None, profile: str = "core") -> subprocess.Popen[str]:
    return subprocess.Popen(
        build_wsl_bioenv_command(env_name, distro, profile),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        stdin=subprocess.DEVNULL,
    )
