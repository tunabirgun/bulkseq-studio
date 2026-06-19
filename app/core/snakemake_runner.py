from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.constants import WSL_ENV_NAME, WSL_MAMBA_ROOT, WSL_MICROMAMBA
from app.core.config_models import AppConfig
from app.core.paths import windows_to_wsl_path


@dataclass
class SnakemakeCommand:
    command: list[str]
    display: str


def build_snakemake_args(config: AppConfig, mode: str = "run") -> list[str]:
    """Snakemake argument vector, independent of how it is launched."""
    args = [
        "snakemake",
        "--snakefile",
        "workflow/Snakefile",
        "--cores",
        str(config.resources.total_threads),
        "--resources",
        f"mem_mb={config.resources.total_memory_gb * 1000}",
        "--configfile",
        "config/config.yaml",
    ]
    if mode == "dry-run":
        args.insert(1, "-n")
    elif mode == "resume":
        args.insert(1, "--rerun-incomplete")
    elif mode == "unlock":
        args = ["snakemake", "--snakefile", "workflow/Snakefile", "--unlock", "--configfile", "config/config.yaml"]
    return args


def build_snakemake_command(
    project_root: Path,
    config: AppConfig,
    mode: str = "run",
    use_wsl: bool = False,
    distro: str | None = None,
) -> SnakemakeCommand:
    """Build the launch command.

    On WSL the tools live in a micromamba environment that an unactivated login
    shell does not put on PATH, so snakemake is invoked through
    ``micromamba run -n <env>`` (the same mechanism the readiness check probes).
    ``--use-conda`` is intentionally omitted: no rule declares a ``conda:``
    directive, the whole pipeline runs inside the single activated environment.
    """
    args = build_snakemake_args(config, mode)
    if use_wsl:
        wsl_root = windows_to_wsl_path(project_root)
        inner = (
            f'cd {shlex.quote(wsl_root)} && '
            f'export MAMBA_ROOT_PREFIX="{WSL_MAMBA_ROOT}" && '
            f'{WSL_MICROMAMBA} run -n {WSL_ENV_NAME} {shlex.join(args)}'
        )
        cmd = ["wsl"]
        if distro:
            cmd += ["-d", distro]
        cmd += ["--", "bash", "-lc", inner]
        return SnakemakeCommand(cmd, subprocess.list2cmdline(cmd))
    return SnakemakeCommand(args, subprocess.list2cmdline(args))


class SnakemakeRunner:
    def __init__(self, project_root: Path, command: SnakemakeCommand) -> None:
        self.project_root = project_root
        self.command = command
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> subprocess.Popen[str]:
        self.process = subprocess.Popen(
            self.command.command,
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        return self.process

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
