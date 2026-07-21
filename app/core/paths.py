from __future__ import annotations

import os
import re
import shutil
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


def wsl_unc_distro(path: str | Path) -> str | None:
    """The distro name inside a WSL-native UNC path (\\\\wsl.localhost\\<distro>\\...), else None."""
    m = re.match(r"^//wsl(?:\.localhost|\$)/([^/]+)", str(path).replace("\\", "/"), re.IGNORECASE)
    return m.group(1) if m else None


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
    # A distro that will not start (broken/unmounted ext4.vhdx) makes wsl.exe print its own
    # error to STDOUT and return non-zero; without these guards that error text would be taken
    # as $HOME and pasted into the working-directory field. A real $HOME is an absolute POSIX
    # path, so require returncode 0 AND a leading '/'.
    if proc.returncode != 0:
        return None
    home = (proc.stdout or "").strip()
    return home if home.startswith("/") else None


def wsl_has_working_distro(timeout: int = 45) -> bool:
    """True only if a WSL distribution is installed AND actually starts.

    `shutil.which("wsl")` (wsl.exe present) and `wsl -l -q` (a distro registered) both pass on a
    machine whose distro will not launch — a missing or broken ext4.vhdx. This runs a trivial
    command INSIDE the default distro and checks for a marker: it fails (returncode != 0, no
    marker) when no distro is installed or the distro cannot mount, and succeeds only when Linux
    is genuinely reachable. Read from stdout as bytes: a failed launch emits UTF-16LE wsl.exe
    error text, and the ASCII marker simply will not be found in it.
    """
    if shutil.which("wsl") is None:
        return False
    try:
        proc = subprocess.run(
            ["wsl", "--", "bash", "-lc", "echo BULKSEQ_WSL_OK"],
            capture_output=True,
            timeout=timeout,
            creationflags=_wsl_quiet_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and b"BULKSEQ_WSL_OK" in (proc.stdout or b"")


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


def wsl_vhdx_basepath(distro: str | None = None) -> Path | None:
    """Windows folder that stores the distro's ext4.vhdx — the drive that physically backs WSL.

    Read from the WSL registry (HKCU\\...\\Lxss\\<guid>\\BasePath), which stays correct even
    when the vhdx was relocated off C:. Falls back to %LOCALAPPDATA% then %SystemDrive% (WSL's
    default install location). Windows only; None elsewhere or when nothing resolves.
    Pass `distro` explicitly (from wsl_unc_distro) to avoid the wsl.exe probe in wsl_default_distro.
    """
    if not sys.platform.startswith("win"):
        return None
    name = distro or wsl_default_distro()
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Lxss") as lxss:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(lxss, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(lxss, sub) as key:
                        reg_name = str(winreg.QueryValueEx(key, "DistributionName")[0])
                        if name is not None and reg_name.lower() != name.lower():
                            continue
                        base = str(winreg.QueryValueEx(key, "BasePath")[0])
                except OSError:
                    continue
                if base.startswith("\\\\?\\"):  # extended-length prefix
                    base = base[4:]
                p = Path(base)
                if p.exists():
                    return p
    except OSError:
        pass
    for val in (os.environ.get("LOCALAPPDATA"), os.environ.get("SystemDrive")):
        if val and Path(val).exists():
            return Path(val)
    return None


def usable_disk_free_bytes(path: str | Path) -> int:
    """Free bytes actually usable at `path`, corrected for the WSL2 vhdx over-report.

    shutil.disk_usage on a WSL-native UNC path returns the ext4.vhdx *virtual* capacity
    (default ~1 TB), not the physical free space on the Windows drive that backs it: the
    sparse vhdx can only grow into that drive's free space. Report min(vhdx-reported-free,
    backing-drive-free) so a low-disk warning reflects the real limit, not a phantom terabyte.
    A plain Windows/Linux path is returned unchanged.
    """
    virtual_free = shutil.disk_usage(str(path)).free
    if not is_wsl_unc_path(path):
        return virtual_free
    base = wsl_vhdx_basepath(wsl_unc_distro(path))
    if base is None:
        return virtual_free
    try:
        host_free = shutil.disk_usage(str(base)).free
    except OSError:
        return virtual_free
    return min(virtual_free, host_free)
