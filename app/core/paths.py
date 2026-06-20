from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# WSL-native UNC paths surface on Windows as \\wsl.localhost\<distro>\... or the
# legacy \\wsl$\<distro>\... . Both already live on the Linux filesystem, so the
# translation is just stripping the \\wsl...\<distro> prefix (not a /mnt mount).
_WSL_UNC = re.compile(r"^//wsl(?:\.localhost|\$)/[^/]+/?(.*)$", re.IGNORECASE)


def app_root() -> Path:
    # When frozen by PyInstaller, bundled data (app/data, workflow, scripts) lives
    # under sys._MEIPASS; in development it is the repository root.
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_path(name: str) -> Path:
    return app_root() / "app" / "data" / name


def workflow_root() -> Path:
    return app_root() / "workflow"


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def windows_to_wsl_path(path: str | Path) -> str:
    # A WSL-native UNC path is already on the Linux filesystem: map
    # \\wsl.localhost\<distro>\home\user\... -> /home/user/... (no /mnt prefix).
    unc = _WSL_UNC.match(str(path).replace("\\", "/"))
    if unc:
        return "/" + unc.group(1).lstrip("/")
    p = Path(path).resolve()
    drive = p.drive.rstrip(":").lower()
    rest = "/".join(p.parts[1:])
    if drive:
        return f"/mnt/{drive}/{rest}"
    return str(p).replace("\\", "/")


def is_wsl_unc_path(path: str | Path) -> bool:
    """True if the path is a WSL-native UNC path (already on the Linux filesystem)."""
    return bool(_WSL_UNC.match(str(path).replace("\\", "/")))


def _wsl_quiet_flags() -> int:
    # Hide the wsl.exe console window on Windows so the GUI is never covered.
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def wsl_default_distro() -> str | None:
    """Name of the default WSL distribution, or None if WSL is unavailable.

    `wsl -l -v` marks the default distro with a leading '*' and emits UTF-16LE.
    """
    try:
        proc = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True,
            timeout=15,
            creationflags=_wsl_quiet_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or b"").decode("utf-16-le", errors="ignore")
    for line in text.splitlines():
        stripped = line.replace("\x00", "").strip()
        if stripped.startswith("*"):
            parts = stripped.lstrip("* ").split()
            return parts[0] if parts else None
    return None


def wsl_home(distro: str | None = None) -> str | None:
    """The Linux $HOME of the given (or default) WSL distro, or None on failure."""
    args = ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", "echo $HOME"]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_wsl_quiet_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    home = (proc.stdout or "").strip()
    return home or None


def wsl_recommended_workdir(subdir: str = "BulkSeqProjects") -> str | None:
    """Windows UNC path to a projects folder on the WSL-native filesystem.

    Returns e.g. \\\\wsl.localhost\\Ubuntu-24.04\\home\\user\\BulkSeqProjects, which
    is the fast Linux filesystem (no 9P /mnt boundary). None if WSL is unavailable.
    """
    distro = wsl_default_distro()
    if not distro:
        return None
    home = wsl_home(distro)
    if not home:
        return None
    return "\\\\wsl.localhost\\" + distro + home.replace("/", "\\") + "\\" + subdir
