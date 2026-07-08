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


def _target_input_exists(project_root: Path | None, rel_path: str) -> bool:
    """True if an optional figures-mode target's input file is present on disk.

    Forcing a rule whose input does not exist raises MissingInputException and
    fails the whole "Regenerate figures" run, so optional targets are gated on
    their real input rather than only the config flag. When project_root is
    unknown (None), assume present so callers that do not pass it (e.g. tests
    asserting arg structure) keep the previous behaviour.
    """
    if project_root is None:
        return True
    return (Path(project_root) / rel_path).exists()


def build_snakemake_args(
    config: AppConfig, mode: str = "run", project_root: Path | None = None
) -> list[str]:
    """Snakemake argument vector, independent of how it is launched."""
    args = [
        "snakemake",
        "--snakefile",
        "workflow/Snakefile",
        "--cores",
        str(config.resources.total_threads),
        "--resources",
        f"mem_mb={config.resources.total_memory_gb * 1000}",
        # Cap concurrent FASTQ downloads (each opens a few connections); more than a handful
        # at once makes ENA refuse connections under load. The download_fastq rule consumes
        # downloads=1, so at most 3 run in parallel.
        "downloads=3",
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
        # All style-consuming rules, so "Regenerate figures" restyles the whole figure
        # set, not just the core DESeq2 figures. Optional rules are gated on their config
        # so they are only forced when their inputs exist (else MissingInputException).
        targets = ["figures", "sample_correlation", "wilcoxon_sensitivity", "set_overlap"]
        if config.workflow.enrichment and _target_input_exists(
            project_root, "results/enrichment/enrichment_objects.rds"
        ):
            targets.append("enrichment_figures")
        if config.ppi.enabled and _target_input_exists(
            project_root, "results/deseq2/deseq2_results.csv"
        ):
            targets.append("network_string")
        if config.gene_sets.custom_gene_list and _target_input_exists(
            project_root, "results/deseq2/deseq2_objects.rds"
        ):
            targets.append("genes_of_interest")
        if (config.gene_sets.custom_gene_sets or config.gene_sets.functional_annotation_table) and _target_input_exists(
            project_root, "results/enrichment/custom_enrichment_objects.rds"
        ):
            targets.append("custom_enrichment_figure")
        args += ["--forcerun", *targets, "--allowed-rules", *targets]
    elif mode == "goi":
        # Produce only the genes-of-interest outputs from the existing DESeq2 object
        # (no re-alignment / re-DESeq2 / other figures). The GOI output files are
        # named explicitly as leading positional targets, and --allowed-rules limits
        # execution to the genes_of_interest rule so nothing upstream re-runs.
        goi_outputs = [
            "results/figures/goi_heatmap.png",
            "results/figures/goi_expression.png",
            "results/genes_of_interest/goi_normalized_counts.csv",
            "results/genes_of_interest/goi_report.txt",
        ]
        for offset, target in enumerate(goi_outputs):
            args.insert(1 + offset, target)
        args += ["--forcerun", "genes_of_interest", "--allowed-rules", "genes_of_interest"]
    elif mode == "ppi":
        # Rebuild only the STRING PPI network from the existing DESeq2 results with
        # the current ppi settings (score threshold / hub labels); no re-align,
        # re-DESeq2, or other rules.
        ppi_outputs = [
            "results/networks/string_ppi.graphml",
            "results/figures/ppi_network.png",
            "results/networks/ppi_hub_genes.csv",
        ]
        for offset, target in enumerate(ppi_outputs):
            args.insert(1 + offset, target)
        args += ["--forcerun", "network_string", "--allowed-rules", "network_string"]
    elif mode == "term":
        # Build ONLY the enrichment-term heatmap from an existing DESeq2 object, on the gene
        # list the app wrote to config/enrichment_term.txt. No re-align / re-DESeq2 / other rules.
        term_outputs = [
            "results/figures/term_heatmap.png",
            "results/figures/term_expression.png",
            "results/enrichment/terms/term_normalized_counts.csv",
            "results/enrichment/terms/term_report.txt",
        ]
        for offset, target in enumerate(term_outputs):
            args.insert(1 + offset, target)
        args += ["--forcerun", "enrichment_term_heatmap", "--allowed-rules", "enrichment_term_heatmap"]
    return args


def _wrap_wsl(args: list[str], wsl_root: str, run_tag: str | None) -> str:
    """Inner `bash -lc` string that activates the env and runs snakemake.

    The optional run tag is exported (and therefore present in the argv of this
    login shell) so stop() can locate and kill the whole tree with `pkill -f`.
    """
    tag_prefix = f"export {run_tag}=1 && " if run_tag else ""
    # Put the env bin on PATH in the parent snakemake process so EVERY child inherits it:
    # both bare-command shell rules (fastp, STAR, featureCounts, ...) and script: rules that
    # resolve Rscript from PATH. micromamba run does not reliably propagate the activated PATH
    # to rule subshells in all configurations, which otherwise fails command lookups even when
    # the tool is installed.
    return (
        f"{tag_prefix}"
        f"cd {shlex.quote(wsl_root)} && "
        f'export MAMBA_ROOT_PREFIX="{WSL_MAMBA_ROOT}" && '
        f'export PATH="{WSL_MAMBA_ROOT}/envs/{WSL_ENV_NAME}/bin:${{PATH}}" && '
        # Force a dot decimal separator for every tool (R especially) so a comma-decimal
        # host locale can't make the pipeline write "0,05" into CSVs that Python then misparses.
        'export LC_NUMERIC=C && '
        f'"{WSL_MICROMAMBA}" run -n {WSL_ENV_NAME} {shlex.join(args)}'
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
    args = build_snakemake_args(config, mode, project_root)
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
            # Dot decimal separator for the native (Linux) run too, so a comma-decimal host
            # locale cannot leak "0,05" into tool output. (WSL runs set this inside _wrap_wsl.)
            env={**os.environ, "LC_NUMERIC": "C"},
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
