"""Capability probes shared by the tests that shell out to bash or to R.

Each probe answers one question: can *this* host run the thing the test needs, and
with what command prefix. A test that asks for R packages gets a runtime that has
proved it can load them, so a distribution R without Bioconductor is never mistaken
for the pipeline's own R. On Windows the only bash and the only pipeline R live in
WSL2, so the WSL candidates are tried after the native ones.

The prefix is returned together with the path translation it needs: every path
handed to the returned command - the script to run, and any path embedded in it -
must go through that callable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from app.core.paths import windows_to_wsl_path

Runtime = tuple[list[str], Callable[[Path], str]]

# The pipeline's R is installed by scripts/setup_wsl_bioenv.sh into a micromamba prefix
# and is never on PATH. "$HOME" is expanded by the shell on the WSL side and by
# Path.home() natively.
_ENV_RSCRIPT = (
    "$HOME/micromamba/envs/bulkseq/bin/Rscript",
    "/root/micromamba/envs/bulkseq/bin/Rscript",
    "$HOME/.local/share/mamba/envs/bulkseq/bin/Rscript",
)


def _on_windows() -> bool:
    return sys.platform.startswith("win")


def _posix(path: Path) -> str:
    return path.as_posix()


@lru_cache(maxsize=None)
def _wsl() -> Optional[str]:
    """The wsl.exe that runs a command, or None.

    wsl.exe ships with Windows even when no distribution is installed (the GitHub
    runner): only a distribution that actually runs a command counts.
    """
    if not _on_windows():
        return None
    wsl = shutil.which("wsl")
    if wsl is None:
        return None
    try:
        probe = subprocess.run([wsl, "--", "true"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return wsl if probe.returncode == 0 else None


@lru_cache(maxsize=None)
def bash_runtime() -> Optional[Runtime]:
    """Prefix that runs `bash <script>`, with its path translation, or None."""
    wsl = _wsl()
    if wsl:
        return [wsl, "bash"], windows_to_wsl_path
    if _on_windows():
        return None
    bash = shutil.which("bash")
    return ([bash], _posix) if bash else None


def _requires(packages: tuple[str, ...]) -> str:
    # wsl.exe relays its arguments through a shell, and Windows only quotes an argument
    # that contains a space: an unspaced "quit(status=0)" reaches bash as a syntax error.
    # Every expression below keeps a space so it survives that relay.
    if not packages:
        return "quit(status = 0)"
    names = ",".join(f'"{name}"' for name in packages)
    return (
        f"quit(status=if (all(vapply(c({names}), requireNamespace, logical(1), "
        "quietly=TRUE))) 0 else 1)"
    )


def _runs_with(command: list[str], packages: tuple[str, ...]) -> bool:
    try:
        probe = subprocess.run(
            [*command, "-e", _requires(packages)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def _executable(path: str) -> bool:
    # /root/micromamba is one of the prefixes the installer uses, and stat()ing it as an
    # ordinary user raises rather than returning False.
    try:
        return os.access(path, os.X_OK) and Path(path).is_file()
    except OSError:
        return False


def _native_candidates() -> list[str]:
    home = str(Path.home())
    found = shutil.which("Rscript")
    candidates = [found] if found else []
    if not _on_windows():
        candidates += [path.replace("$HOME", home) for path in _ENV_RSCRIPT]
    return [path for path in candidates if _executable(path)]


def _wsl_candidates(wsl: str) -> list[str]:
    listing = "; ".join(
        f'[ -x "{path}" ] && echo "{path}"'
        for path in ("$(command -v Rscript)", *_ENV_RSCRIPT)
    )
    try:
        found = subprocess.run(
            [wsl, "--", "bash", "-lc", f"{listing}; true"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    paths = (line.strip() for line in found.stdout.splitlines() if line.strip())
    return list(dict.fromkeys(paths))


@lru_cache(maxsize=None)
def rscript_runtime(*packages: str) -> Optional[Runtime]:
    """Prefix that runs `Rscript --vanilla <script>`, with its path translation, or None.

    Every candidate must prove it can load each named package before it is accepted;
    with no package named, that it starts at all. Native R first, then WSL.
    """
    for rscript in _native_candidates():
        command = [rscript, "--vanilla"]
        if _runs_with(command, packages):
            return command, _posix
    wsl = _wsl()
    if wsl:
        for rscript in _wsl_candidates(wsl):
            command = [wsl, "--", rscript, "--vanilla"]
            if _runs_with(command, packages):
                return command, windows_to_wsl_path
    return None


def reset_probe_cache() -> None:
    _wsl.cache_clear()
    bash_runtime.cache_clear()
    rscript_runtime.cache_clear()
