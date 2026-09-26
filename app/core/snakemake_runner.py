from __future__ import annotations

import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.constants import WSL_ENV_NAME, WSL_MAMBA_ROOT, WSL_MICROMAMBA
from app.core.config_models import AppConfig
from app.core.paths import UnsupportedUncPathError, windows_to_wsl_path, without_bundle_library_path

# Marker prefix exported into the WSL process environment so the whole process
# tree can be found and killed from a separate `wsl` invocation (terminating the
# Windows wsl.exe relay alone leaves snakemake/STAR running inside the WSL VM).
RUN_TAG_PREFIX = "BULKSEQ_RUN_TAG"


def _new_run_tag() -> str:
    return f"{RUN_TAG_PREFIX}_{uuid.uuid4().hex}"


# Snakemake prints one of these on any rule/workflow failure. Both front ends watch for
# them because the WSL launcher runs through `micromamba run`, which returns exit 0 even
# when snakemake failed — so the process exit code alone can report a failed run as
# succeeded. A definitive error line marks the run failed regardless of the code.
FAILURE_MARKERS = re.compile(
    r"Error in rule\s|WorkflowError|Exiting because a job execution failed"
    r"|MissingOutputException"
    # The GUI escalates these as a broken R/Bioconductor environment rather than an ordinary
    # rule failure, but a run that hits them has not produced valid output either — the CLI
    # has no rebuild-offer path, so it must report failure here.
    r"|will not load in the bulkseq env|there is no package called|unable to load shared object"
)


# How long the consumer waits on the queue before looking for a pending signal, and how long
# a Ctrl-C waits for the reader to notice the closed pipe after the tree was killed.
_OUTPUT_POLL_SEC = 0.1
_READER_JOIN_SEC = 5.0


def run_snakemake_sync(project_root, config: AppConfig, mode: str, *, exec_profile: str = "local",
                        on_line=None) -> int:
    """Run a Snakemake command to completion, streaming output and returning an exit code.

    Launch-equivalent to the GUI's SnakemakeRunner.start(): same cwd, child env, and stream
    decoding, built through the same Popen setup so the two code paths cannot drift apart.
    Detects a snakemake-reported failure the same way the GUI does (FAILURE_MARKERS), so a
    masked exit-0 from `micromamba run` is still reported as a non-zero exit here. A Ctrl-C
    stops the whole process tree the way the GUI's Stop does, not just this relay process.

    A reader thread drains the pipe and the calling thread consumes a queue on a short
    timeout. Reading the pipe in the calling thread instead blocks it inside the C-level
    read, where a signal is only checked once the read returns: during a long quiet phase
    (an aligner running for an hour without printing) Ctrl-C is then not seen until the
    next output line, so the run keeps going. One code path on every platform -- the same
    block is what makes the interrupt land on Windows, in WSL and on macOS alike.
    """
    project_root = Path(project_root)
    use_wsl = sys.platform.startswith("win") and exec_profile == "local"
    run_tag = _new_run_tag() if use_wsl else None
    command = build_snakemake_command(project_root, config, mode, use_wsl=use_wsl,
                                       run_tag=run_tag, exec_profile=exec_profile)
    emit = on_line or (lambda line: print(line, file=sys.stderr))
    failed_in_output = False
    runner = SnakemakeRunner(project_root, command)
    process = runner.start()
    assert process.stdout is not None
    lines: queue.SimpleQueue[str] = queue.SimpleQueue()
    reader = threading.Thread(target=_pump_output, args=(process.stdout, lines), daemon=True)
    reader.start()

    def consume(raw: str) -> bool:
        line = raw.rstrip("\n")
        emit(line)
        return bool(FAILURE_MARKERS.search(line))

    try:
        while reader.is_alive():
            try:
                raw = lines.get(timeout=_OUTPUT_POLL_SEC)
            except queue.Empty:
                raw = None
            # Fall through rather than `continue`: a signal raised while leaving the except
            # block escapes the KeyboardInterrupt handler below (CPython 3.12, reproduced
            # 40/40), and the interrupt is the one thing this loop exists to notice.
            if raw is not None:
                failed_in_output |= consume(raw)
        code = process.wait()
    except KeyboardInterrupt:
        runner.stop()
        reader.join(timeout=_READER_JOIN_SEC)
        raise
    # The reader can queue its last lines between one is_alive() check and the next, and the
    # tail is exactly where "Error in rule" appears -- so finish the queue before the verdict.
    while True:
        try:
            failed_in_output |= consume(lines.get_nowait())
        except queue.Empty:
            break
    if failed_in_output and code == 0:
        return 1
    return code


def _pump_output(stream, lines: "queue.SimpleQueue[str]") -> None:
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass


def _snakemake_child_env(use_wsl: bool) -> dict[str, str]:
    # Dot decimal separator for the native (Linux) run too, so a comma-decimal host
    # locale cannot leak "0,05" into tool output. (WSL runs set this inside _wrap_wsl.)
    env = {**without_bundle_library_path(dict(os.environ)), "LC_NUMERIC": "C"}
    if not use_wsl:
        # The WSL branch exports PATH inside _wrap_wsl; do the equivalent here so a
        # native run finds the environment's tools.
        prefix = native_path_prefix()
        if prefix:
            env["PATH"] = os.pathsep.join([*prefix, env.get("PATH", "")])
        # Rule shells dereference MAMBA_ROOT_PREFIX (trimming.smk's Trimmomatic adapter
        # lookup among them) and Snakemake runs them under `set -u`, so an unset variable
        # aborts the job. WSL runs get it from the login shell; give a native run the same
        # value the rules would have defaulted to.
        env["MAMBA_ROOT_PREFIX"] = env.get("MAMBA_ROOT_PREFIX") or str(native_mamba_root())
    return env


def _launch_snakemake_popen(command: "SnakemakeCommand", project_root: Path) -> subprocess.Popen[str]:
    """The one Popen construction both SnakemakeRunner.start() and run_snakemake_sync() use,
    so cwd, child env, stream decoding and process-group setup cannot drift between the GUI
    and CLI launch paths."""
    creationflags = 0
    if sys.platform.startswith("win"):
        # Own process group so a native taskkill /T reaches the whole tree, and
        # CREATE_NO_WINDOW so the wsl.exe console does not pop up over the GUI
        # when launched from the windowed (no-console) packaged app.
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    # The pipeline runs in a UTF-8 Linux environment; decode as such and never let one
    # undecodable byte kill the reader (the default is the console code page, strict).
    return subprocess.Popen(
        command.command,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        text=True,
        bufsize=1,
        creationflags=creationflags,
        # POSIX counterpart of CREATE_NEW_PROCESS_GROUP above: setsid() puts the
        # child in its own process group (pgid == pid) so Stop can signal the whole
        # tree. Without it, terminating the Snakemake process leaves its children
        # -- STAR, featureCounts, Rscript -- running for hours on the user's machine.
        start_new_session=not sys.platform.startswith("win"),
        env=_snakemake_child_env(command.use_wsl),
    )


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


def _samples_sheet(project_root: Path) -> Path:
    """The sheet the Snakefile will actually parse: `config.yaml`'s `input.samples`, resolved
    against the project root because Snakemake runs with the project root as its working
    directory. Falls back to the conventional location when the config is absent or unreadable
    — the same file the Snakefile's own default would name."""
    import yaml  # local: keeps module import cheap, mirroring the pandas import below

    relative = "config/samples.tsv"
    try:
        loaded = yaml.safe_load(
            (project_root / "config" / "config.yaml").read_text(encoding="utf-8")
        )
        section = loaded.get("input") if isinstance(loaded, dict) else None
        configured = str((section or {}).get("samples") or "").strip() if isinstance(section, dict) else ""
        if configured:
            relative = configured
    except (OSError, ValueError, UnicodeDecodeError, yaml.YAMLError):
        pass
    candidate = Path(relative)
    return candidate if candidate.is_absolute() else project_root / candidate


def _is_multistudy(project_root: Path | None) -> bool:
    """True when the project's samples.tsv is a genuine multi-study sheet (a 'dataset' column with
    more than one distinct non-empty value) — an EXACT mirror of the Snakefile's MULTI_DATASET, using
    the identical pandas parse so NA-family tokens (NA/NULL/None/nan/N/A) coerce to empty on both sides
    and the two never disagree. The meta-analysis figure/report rules are DEFINED only in that case, so
    figures-mode must not force them otherwise (naming an undefined rule aborts the whole regenerate
    run); a stale results/meta/ left from an earlier multi-study run must not trigger them once the
    sheet is edited down to one study. Unknown root (None) -> True, matching _target_input_exists so
    arg-structure tests keep prior behaviour."""
    if project_root is None:
        return True
    samples = _samples_sheet(Path(project_root))
    if not samples.exists():
        return False
    try:
        import pandas as pd
        df = pd.read_csv(samples, sep="\t", dtype=str).fillna("")
        if "dataset" not in df.columns:
            return False
        return df["dataset"].astype(str).str.strip().replace("", pd.NA).nunique(dropna=True) > 1
    except (OSError, ValueError):
        # ValueError covers pandas EmptyDataError/ParserError and UnicodeDecodeError (a UnicodeError,
        # itself a ValueError) — an unreadable/empty/garbled sheet is treated as not multi-study.
        return False


def snakemake_run_state(project_root: Path | None) -> dict[str, bool]:
    """Whether a project holds an interrupted, resumable Snakemake run. `.snakemake/` IS the durable
    saved state, so this survives closing and reopening the app: a `locks/` dir with any entry means a
    run was holding the working directory (a hard-killed / app-closed run), and an `incomplete/` dir
    with any entry means some outputs were left half-written. Either one -> resumable, and Resume
    (--rerun-incomplete, after an unlock) continues only the missing/incomplete steps. Pure filesystem,
    no subprocess, so it is safe to call synchronously on the UI thread when a project loads."""
    empty = {"resumable": False, "locked": False, "incomplete": False}
    if project_root is None:
        return empty
    sm = Path(project_root) / ".snakemake"

    def _nonempty(d: Path) -> bool:
        try:
            return d.is_dir() and any(d.iterdir())
        except OSError:
            return False

    locked = _nonempty(sm / "locks")
    incomplete = _nonempty(sm / "incomplete")
    return {"resumable": locked or incomplete, "locked": locked, "incomplete": incomplete}


# Execution targets. "local" runs everything on this machine (or inside WSL2 on Windows);
# the others hand each job to a scheduler through a Snakemake executor plugin, configured
# by the matching profile under workflow/profiles/site/.
EXEC_PROFILES = ("local", "slurm", "kubernetes")


def site_profile_dir(exec_profile: str) -> str:
    """Profile path, deliberately RELATIVE to the project root.

    Snakemake resolves --profile against the launching process's working directory, and
    SnakemakeRunner sets cwd=project_root. Keeping it relative therefore both resolves
    correctly and keeps any absolute Windows path out of the `bash -lc` string that WSL
    runs, which is what lets one code path serve both platforms.
    """
    return f"workflow/profiles/site/{exec_profile}"


def build_snakemake_args(
    config: AppConfig, mode: str = "run", project_root: Path | None = None,
    exec_profile: str = "local",
) -> list[str]:
    """Snakemake argument vector, independent of how it is launched."""
    args = [
        "snakemake",
        "--snakefile",
        "workflow/Snakefile",
    ]
    if exec_profile == "local":
        # Local scheduling pools sized to this machine: Snakemake packs jobs so their
        # summed threads/mem stay inside them.
        args += [
            "--cores",
            str(config.resources.total_threads),
            "--resources",
            f"mem_mb={config.resources.total_memory_gb * 1000}",
            # Cap concurrent FASTQ downloads (each opens a few connections); more than a
            # handful at once makes ENA refuse connections under load. The download_fastq
            # rule consumes downloads=1, so at most 3 run in parallel.
            "downloads=3",
        ]
    else:
        # A cluster executor turns each rule's own `resources:` into the scheduler's
        # allocation (sbatch --mem, a pod resource request). A global `--resources
        # mem_mb=N` must NOT be passed here: Snakemake CLAMPS every rule's request down to
        # the pool rather than refusing, so a laptop-sized default (8 GB) would submit
        # star_align — which declares 24 GB — with 8 GB and have it OOM-killed hours in.
        # Job count and the thread ceiling come from the profile instead (see
        # workflow/profiles/site/*/config.yaml), because `--jobs` alone silently doubles as
        # the thread cap when `--cores` is absent.
        args += ["--profile", site_profile_dir(exec_profile), "--resources", "downloads=3"]
    args += ["--configfile", "config/config.yaml"]
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
        targets = ["figures", "set_overlap"]
        # sample-correlation + the Wilcoxon diagnostic need a per-sample count matrix, which a
        # deseq2-results upload does not have (the synthetic objects RDS carries no dds/vsd), so
        # they are not part of that mode's normal run — do not force them there.
        if config.input.type != "deseq2_results":
            targets += ["sample_correlation", "wilcoxon_sensitivity"]
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
        # GSVA writes a styled heatmap, so a restyle must re-render it. Mirror the Snakefile's
        # GSVA_ON exactly (gsva AND a custom gene-set file AND a per-sample matrix, i.e. not a
        # deseq2-results upload), because forcing an undefined rule aborts the regenerate. Gate on
        # the rule's INPUTS as well as its score table: `results/export/` is not protected by
        # reclaim_run_space.sh, so a reclaimed run can keep gsva_scores.csv with no matrix left to
        # re-run from, which would be a MissingInputException instead of a restyle.
        if (config.workflow.gsva and config.gene_sets.custom_gene_sets
                and config.input.type != "deseq2_results"
                and _target_input_exists(project_root, "results/gsva/gsva_scores.csv")
                and _target_input_exists(project_root, "results/export/normalized_expression_matrix.csv")
                and _target_input_exists(project_root, config.gene_sets.custom_gene_sets)):
            targets.append("gsva")
        # Multi-study meta-analysis comparative figures are style-consuming too, so a restyle
        # regenerates them from the existing meta result (no re-run of the per-study DESeq2). Mirror
        # the Snakefile's META_MODE exactly (meta_analysis AND multi-study AND a count-based input):
        # the meta rules are undefined when the sheet is single-study OR the input is microarray /
        # uploaded DE results, and forcing an undefined rule aborts the whole regenerate run. Gate on
        # the CURRENT sheet + input type, not merely on stale meta outputs left on disk.
        _meta_on = (config.workflow.meta_analysis and _is_multistudy(project_root)
                    and config.input.type not in ("microarray", "deseq2_results"))
        if _meta_on and _target_input_exists(
            project_root, "results/meta/meta_analysis_results.csv"
        ):
            targets.append("meta_figures")
            # Per-study figures are style-consuming too. The rule is an aggregator whose declared
            # output is the manifest, so its presence is what marks "it has run"; its inputs are the
            # meta result (checked above) and the pooled DE table.
            if (_target_input_exists(project_root, "results/meta/per_study/manifest.json")
                    and _target_input_exists(project_root, "results/deseq2/deseq2_results.csv")):
                targets.append("meta_per_study")
            if config.workflow.enrichment and _target_input_exists(
                project_root, "results/meta/meta_enrichment_objects.rds"
            ):
                targets.append("meta_enrichment_figures")
        # Re-embed the restyled figures into the self-contained HTML reports, which inline every
        # figure as base64 — otherwise the shared report keeps showing the pre-restyle figures.
        # Their inputs already exist after a completed run, so this re-runs only the report step.
        if _target_input_exists(project_root, "results/reports/run_summary.txt"):
            targets.append("html_report")
        if _meta_on and _target_input_exists(
            project_root, "results/reports/meta_analysis_summary.json"
        ):
            targets.append("meta_report")
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

    The optional run tag is exported so stop() can locate every inheriting process
    through `/proc/<pid>/environ`, even when bash replaces itself with micromamba.
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
    exec_profile: str = "local",
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
    args = build_snakemake_args(config, mode, project_root, exec_profile=exec_profile)
    if use_wsl:
        try:
            wsl_root = windows_to_wsl_path(project_root)
        except UnsupportedUncPathError as exc:
            raise RuntimeError(f"The project folder is on a network share that WSL cannot open: {exc}") from exc
        inner = _wrap_wsl(args, wsl_root, run_tag)
        cmd = ["wsl"]
        if distro:
            cmd += ["-d", distro]
        cmd += ["--", "bash", "-lc", inner]
        return SnakemakeCommand(
            cmd, subprocess.list2cmdline(cmd), use_wsl=True, distro=distro, run_tag=run_tag
        )
    return SnakemakeCommand(args, subprocess.list2cmdline(args), use_wsl=False)


def native_mamba_root() -> Path:
    """The micromamba root a native (non-WSL) run uses.

    Byte-for-byte the default the workflow's shell rules apply
    (``${MAMBA_ROOT_PREFIX:-$HOME/micromamba}``), so the environment handed to Snakemake and
    the expansion inside a rule cannot disagree.
    """
    return Path(os.environ.get("MAMBA_ROOT_PREFIX") or (Path.home() / "micromamba"))


def native_path_prefix() -> list[str]:
    """Directories to prepend to PATH for a native (non-WSL) run.

    The WSL branch does this inside _wrap_wsl; the native branch had no equivalent, so
    a run launched from a shell that had not activated the environment resolved tools
    from the ambient PATH instead of the one it was configured to use.

    Only existing directories are returned, so a host without the environment (every
    Windows box, and a Linux box using an already-activated shell) is unaffected.
    """
    root = native_mamba_root()
    dirs: list[str] = []
    env_bin = root / "envs" / WSL_ENV_NAME / "bin"
    if env_bin.is_dir():
        dirs.append(str(env_bin))
    return dirs


def build_unlock_command(
    project_root: Path,
    config: AppConfig,
    use_wsl: bool = False,
    distro: str | None = None,
) -> SnakemakeCommand:
    """Standalone `snakemake --unlock` command (no run tag needed)."""
    return build_snakemake_command(project_root, config, mode="unlock", use_wsl=use_wsl, distro=distro)


def build_wsl_kill_command(run_tag: str, distro: str | None = None, signal: str = "TERM") -> list[str]:
    """`wsl` invocation that kills every process inheriting the unique run tag.

    A non-interactive bash may replace itself with its final command, so the tag
    is not guaranteed to remain in any command line. The exported environment
    variable is inherited by micromamba, Snakemake, and rule processes; scanning
    `/proc/*/environ` therefore remains valid after that exec optimisation.
    """
    if signal not in {"TERM", "KILL"}:
        raise ValueError(f"Unsupported WSL stop signal: {signal!r}")
    if not run_tag.startswith(f"{RUN_TAG_PREFIX}_") or not run_tag.replace("_", "").isalnum():
        raise ValueError("Invalid WSL run tag")
    tag_assignment = shlex.quote(f"{run_tag}=1")
    inner = (
        "pids=''; "
        "for env_file in /proc/[0-9]*/environ; do "
        "pid=${env_file#/proc/}; pid=${pid%/environ}; "
        f"if (tr '\\0' '\\n' < \"$env_file\") 2>/dev/null | "
        f"grep -Fqx -- {tag_assignment}; then pids=\"$pids $pid\"; fi; "
        "done; "
        f"if [ -n \"$pids\" ]; then kill -{signal} $pids 2>/dev/null || true; fi"
    )
    cmd = ["wsl"]
    if distro:
        cmd += ["-d", distro]
    # Use WSL exec mode. With the legacy ``-- bash -lc`` form, wsl.exe routes
    # the command through an outer shell on current WSL builds; that shell
    # expands ``$env_file``/``$pids`` before the intended Bash sees them and
    # silently turns Stop into a no-op.
    cmd += ["--exec", "bash", "-lc", inner]
    return cmd


def _run_quiet(cmd: list[str], timeout: float = 30.0, cwd: Path | None = None) -> None:
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
            cwd=cwd,
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
        self.process = _launch_snakemake_popen(self.command, self.project_root)
        return self.process

    def _child_env(self) -> dict[str, str]:
        return _snakemake_child_env(self.use_wsl)

    def _signal_native_group(self, sig: int) -> bool:
        """Signal the child's whole process group. True if the signal was delivered.

        Falls back to the single process when the group cannot be resolved (the child
        already exited, or start_new_session did not apply).
        """
        if self.process is None:
            return False
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

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
            return
        # POSIX (native Linux): signal the process group, mirroring what
        # taskkill /T does on Windows and what build_wsl_kill_command does in the VM.
        # SIGTERM first so Snakemake can unlock the working directory, then SIGKILL
        # for anything that ignored it.
        if not self._signal_native_group(signal.SIGTERM):
            self.process.terminate()
            return
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._signal_native_group(signal.SIGKILL)

    def _reap_local(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            # Prefer the group on POSIX: a bare terminate()/kill() here reaches only
            # the relay handle and would let the tool processes escape the same way
            # _stop_native_tree used to.
            if not (not sys.platform.startswith("win") and self._signal_native_group(signal.SIGTERM)):
                try:
                    self.process.terminate()
                except OSError:
                    pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if not (not sys.platform.startswith("win") and self._signal_native_group(signal.SIGKILL)):
                try:
                    self.process.kill()
                except OSError:
                    pass

    def unlock(self, config: AppConfig) -> None:
        """Synchronously run `snakemake --unlock` to clear a stale directory lock."""
        cmd = build_unlock_command(self.project_root, config, use_wsl=self.use_wsl, distro=self.distro)
        _run_quiet(cmd.command, timeout=60, cwd=self.project_root)
