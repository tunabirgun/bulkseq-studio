from __future__ import annotations

import os
import platform
import shlex
import signal
import subprocess
import uuid
from pathlib import Path

from app.core.paths import UnsupportedUncPathError, app_root, windows_to_wsl_path
from app.core.snakemake_runner import RUN_TAG_PREFIX, build_wsl_kill_command


def windows_wsl_admin_script() -> Path:
    return app_root() / "scripts" / "setup_windows_wsl_admin.ps1"


def windows_wsl_admin_launcher() -> Path:
    return app_root() / "scripts" / "launch_wsl_setup_admin.bat"


def wsl_bioenv_script() -> Path:
    return app_root() / "scripts" / "setup_wsl_bioenv.sh"


def new_setup_run_tag() -> str:
    """Unique marker exported into the install process environment.

    Same mechanism (and the same prefix, so build_wsl_kill_command accepts it) the run
    uses: killing the Windows wsl.exe relay leaves micromamba resolving inside the VM,
    holding the setup lock for up to 30 minutes. The tag is inherited by every child, so
    the /proc/*/environ sweep finds them all even after bash execs micromamba.
    """
    return f"{RUN_TAG_PREFIX}_{uuid.uuid4().hex}"


def build_wsl_admin_install_command(distro: str = "Ubuntu") -> list[str]:
    return [str(windows_wsl_admin_launcher()), distro]


def launch_wsl_admin_install(distro: str = "Ubuntu") -> subprocess.Popen[str]:
    return subprocess.Popen(build_wsl_admin_install_command(distro), text=True)


def build_wsl_bioenv_command(env_name: str = "bulkseq", distro: str | None = None,
                             profile: str = "core", rebuild: bool = False,
                             run_tag: str | None = None) -> list[str]:
    try:
        repo = windows_to_wsl_path(app_root())
        script = windows_to_wsl_path(wsl_bioenv_script())
    except UnsupportedUncPathError as exc:
        raise RuntimeError(f"The application is installed on a network share that WSL cannot open: {exc}") from exc
    # BULKSEQ_REBUILD=1 tells the setup script to remove and recreate the env from scratch
    # (a clean rebuild), instead of an in-place update that can leave R/Bioconductor mixed.
    # Every interpolated value is shell-quoted: an install path containing an apostrophe
    # (C:\Users\O'Brien\...) otherwise closed the quoting and broke the command apart.
    prefix = "export BULKSEQ_REBUILD=1 && " if rebuild else ""
    tag = f"export {run_tag}=1 && " if run_tag else ""
    inner = (f"{tag}{prefix}cd {shlex.quote(repo)} && bash {shlex.quote(script)} "
             f"{shlex.quote(env_name)} {shlex.quote(profile)}")
    return ["wsl"] + (["-d", distro] if distro else []) + ["--", "bash", "-lc", inner]


def launch_wsl_bioenv_install(env_name: str = "bulkseq", distro: str | None = None,
                              profile: str = "core", rebuild: bool = False,
                              run_tag: str | None = None) -> subprocess.Popen[str]:
    return subprocess.Popen(
        build_wsl_bioenv_command(env_name, distro, profile, rebuild, run_tag),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        stdin=subprocess.DEVNULL,
    )


def build_native_bioenv_command(env_name: str = "bulkseq", profile: str = "core") -> list[str]:
    # Native Linux: run the same setup script directly (no `wsl` wrapper). It creates or
    # `env update`s the micromamba environment from the profile's yaml, which installs any
    # tools missing from an older environment (the repair path). The script itself exits on
    # any non-Linux host, so refuse here with a message the GUI can show instead of surfacing
    # its "Unsupported platform" exit code.
    if platform.system() == "Darwin":
        raise NotImplementedError(
            "macOS is not supported: the pipeline environment installs on Linux (natively "
            "or inside WSL2 on Windows) only.")
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
        # Own process group so stop_bioenv_install can signal micromamba and its children,
        # not just the bash wrapper.
        start_new_session=True,
    )


def build_bioenv_kill_command(run_tag: str, distro: str | None = None,
                              signal_name: str = "TERM") -> list[str]:
    """`wsl` invocation that kills every install process carrying `run_tag`."""
    return build_wsl_kill_command(run_tag, distro, signal=signal_name)


def stop_bioenv_install(process: subprocess.Popen[str] | None, run_tag: str | None = None,
                        native: bool = False, distro: str | None = None) -> None:
    """Stop an install and everything it started, not just the local relay handle.

    WSL: the tagged tree inside the VM (the relay's death leaves micromamba running and the
    setup lock held). Native: the process group created by launch_native_bioenv_install.
    """
    if process is None or process.poll() is not None:
        return
    if native:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (AttributeError, OSError):
            pass
    elif run_tag:
        try:
            subprocess.run(build_bioenv_kill_command(run_tag, distro),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    process.terminate()
