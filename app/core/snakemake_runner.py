from __future__ import annotations

import os
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.constants import WSL_ENV_NAME, WSL_MAMBA_ROOT, WSL_MICROMAMBA
from app.core.config_models import AppConfig
from app.core.paths import windows_to_wsl_path

# Marker prefix embedded in the WSL `bash -lc` argv so the whole process tree can
# be found and killed from a separate `wsl` invocation (terminating the Windows
# wsl.exe relay alone leaves snakemake/STAR running inside the WSL VM).
RUN_TAG_PREFIX = "BULKSEQ_RUN_TAG"


def _new_run_tag() -> str:
    return f"{RUN_TAG_PREFIX}_{uuid.uuid4().hex}"


@dataclass
class SnakemakeCommand:
    command: list[str]
    display: str
    use_wsl: bool = False
    distro: str | None = None
    run_tag: str | None = None


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
    elif mode == "recover":
        # Resume after an incomplete/locked run: rerun-incomplete is the safe
        # forward step once the directory has been unlocked.
        args.insert(1, "--rerun-incomplete")
    elif mode == "unlock":
        args = ["snakemake", "--snakefile", "workflow/Snakefile", "--unlock", "--configfile", "config/config.yaml"]
    elif mode == "figures":
        # Re-render only the figure rules with the current style. --forcerun
        # forces them; --allowed-rules forbids running any other rule, so the
        # regenerate uses the existing DESeq2 rds and never re-aligns or re-runs
        # DESeq2 even if an upstream output's mtime looks stale. The GOI target is
        # included only when it exists as a rule (custom_gene_list set), matching
        # the `if _GOI:` rule guard.
        targets = ["figures"]
        if config.gene_sets.custom_gene_list:
            targets.append("genes_of_interest")
        args += ["--forcerun", *targets, "--allowed-rules", *targets]
    return args


def _wrap_wsl(args: list[str], wsl_root: str, run_tag: str | None) -> str:
    """Inner `bash -lc` string that activates the env and runs snakemake.

    The optional run tag is exported (and therefore present in the argv of this
    login shell) so stop() can locate and kill the whole tree with `pkill -f`.
    """
    tag_prefix = f"export {run_tag}=1 && " if run_tag else ""
    return (
        f"{tag_prefix}"
        f"cd {shlex.quote(wsl_root)} && "
        f'export MAMBA_ROOT_PREFIX="{WSL_MAMBA_ROOT}" && '
        f"{WSL_MICROMAMBA} run -n {WSL_ENV_NAME} {shlex.join(args)}"
    )


def build_snakemake_command(
    project_root: Path,
    config: AppConfig,
    mode: str = "run",
    use_wsl: bool = False,
    distro: str | None = None,
    run_tag: str | None = None,
) -> SnakemakeCommand:
    """Build the launch command.

    On WSL the tools live in a micromamba environment that an unactivated login
    shell does not put on PATH, so snakemake is invoked through
    ``micromamba run -n <env>`` (the same mechanism the readiness check probes).
    ``--use-conda`` is intentionally omitted: no rule declares a ``conda:``
    directive, the whole pipeline runs inside the single activated environment.

    A unique ``run_tag`` is embedded in the WSL ``bash -lc`` argv so the entire
    process tree (which survives killing the Windows wsl.exe relay) can be found
    and terminated by :func:`build_wsl_kill_command`.
    """
    args = build_snakemake_args(config, mode)
    if use_wsl:
        wsl_root = windows_to_wsl_path(project_root)
        inner = _wrap_wsl(args, wsl_root, run_tag)
        cmd = ["wsl"]
        if distro:
            cmd += ["-d", distro]
        cmd += ["--", "bash", "-lc", inner]
        return SnakemakeCommand(
            cmd, subprocess.list2cmdline(cmd), use_wsl=True, distro=distro, run_tag=run_tag
        )
    return SnakemakeCommand(args, subprocess.list2cmdline(args), use_wsl=False)


def build_unlock_command(
    project_root: Path,
    config: AppConfig,
    use_wsl: bool = False,
    distro: str | None = None,
) -> SnakemakeCommand:
    """Standalone `snakemake --unlock` command (no run tag needed)."""
    return build_snakemake_command(project_root, config, mode="unlock", use_wsl=use_wsl, distro=distro)


def build_wsl_kill_command(run_tag: str, distro: str | None = None, signal: str = "TERM") -> list[str]:
    """`wsl` invocation that kills the tagged login shell and all descendants.

    The tag is unique per launch and present in the launching shell's argv, so
    `pkill -<sig> -f <tag>` reliably targets that shell; killing it terminates
    micromamba -> snakemake -> STAR/featureCounts as its child tree. Runs inside
    the same WSL distro as the launch so it shares the VM and its process table.
    """
    inner = f"pkill -{signal} -f {shlex.quote(run_tag)} || true"
    cmd = ["wsl"]
    if distro:
        cmd += ["-d", distro]
    cmd += ["--", "bash", "-lc", inner]
    return cmd


def _run_quiet(cmd: list[str], timeout: float = 30.0) -> None:
    """Fire-and-forget a short cleanup command; never raise."""
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class SnakemakeRunner:
    def __init__(self, project_root: Path, command: SnakemakeCommand) -> None:
        self.project_root = project_root
        self.command = command
        self.process: subprocess.Popen[str] | None = None
        self.use_wsl = command.use_wsl
        self.distro = command.distro
        self.run_tag = command.run_tag
        self._stopped = False

    def start(self) -> subprocess.Popen[str]:
        creationflags = 0
        if sys.platform.startswith("win"):
            # Own process group so a native taskkill /T reaches the whole tree, and
            # CREATE_NO_WINDOW so the wsl.exe console does not pop up over the GUI
            # when launched from the windowed (no-console) packaged app.
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        self.process = subprocess.Popen(
            self.command.command,
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        return self.process

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        """Terminate the whole process tree, not just the local relay handle.

        WSL: send SIGTERM (then SIGKILL after a grace) to the tagged tree inside
        the VM, then terminate the Windows wsl.exe relay. Native: taskkill /T to
        walk the Windows process tree. Either way the local handle is reaped so
        the next run starts clean.
        """
        if self._stopped:
            return
        self._stopped = True
        if self.use_wsl and self.run_tag:
            self._stop_wsl_tree()
        elif self.process is not None and self.process.poll() is None:
            self._stop_native_tree()
        self._reap_local()

    def _stop_wsl_tree(self) -> None:
        assert self.run_tag is not None
        _run_quiet(build_wsl_kill_command(self.run_tag, self.distro, signal="TERM"))
        if self.process is not None:
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _run_quiet(build_wsl_kill_command(self.run_tag, self.distro, signal="KILL"))
        else:
            _run_quiet(build_wsl_kill_command(self.run_tag, self.distro, signal="KILL"))

    def _stop_native_tree(self) -> None:
        assert self.process is not None
        if sys.platform.startswith("win"):
            _run_quiet(["taskkill", "/F", "/T", "/PID", str(self.process.pid)])
        else:
            self.process.terminate()

    def _reap_local(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except OSError:
                pass

    def unlock(self, config: AppConfig) -> None:
        """Synchronously run `snakemake --unlock` to clear a stale directory lock."""
        cmd = build_unlock_command(self.project_root, config, use_wsl=self.use_wsl, distro=self.distro)
        _run_quiet(cmd.command, timeout=60)
