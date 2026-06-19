from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.core.config_models import AppConfig
from app.core.paths import windows_to_wsl_path


@dataclass
class SnakemakeCommand:
    command: list[str]
    display: str


def build_snakemake_command(project_root: Path, config: AppConfig, mode: str = "run", use_wsl: bool = False, distro: str = "Ubuntu") -> SnakemakeCommand:
    base = [
        "snakemake",
        "--snakefile",
        "workflow/Snakefile",
        "--cores",
        str(config.resources.total_threads),
        "--resources",
        f"mem_mb={config.resources.total_memory_gb * 1000}",
        "--use-conda",
        "--configfile",
        "config/config.yaml",
    ]
    if mode == "dry-run":
        base.insert(1, "-n")
    elif mode == "resume":
        base.insert(1, "--rerun-incomplete")
    elif mode == "unlock":
        base = ["snakemake", "--snakefile", "workflow/Snakefile", "--unlock", "--configfile", "config/config.yaml"]

    if use_wsl:
        wsl_root = windows_to_wsl_path(project_root)
        inner = f"cd {shlex.quote(wsl_root)} && {shlex.join(base)}"
        cmd = ["wsl", "-d", distro, "--", "bash", "-lc", inner]
        return SnakemakeCommand(cmd, subprocess.list2cmdline(cmd))
    return SnakemakeCommand(base, subprocess.list2cmdline(base))


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
