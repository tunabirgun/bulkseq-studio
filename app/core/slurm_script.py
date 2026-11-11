"""Single-allocation Slurm mode: the whole workflow in one batch job on one node.

Per-job submission (``bulkseq run --exec-profile slurm``) suits clusters that bill the cores a
job asks for. Centres that allocate and bill whole nodes (some with a minimum charge per
job) or refuse small jobs (partitions with a minimum core count) charge
every one-thread step as a full node. There the workflow runs with the local executor inside
one allocation instead, sized from the allocation itself.
"""

from __future__ import annotations

import posixpath
import shlex
import sys
from pathlib import Path

from app.core.config_models import AppConfig

ALLOCATION_SCRIPT = "config/slurm_allocation.sbatch"
ALLOCATION_LOG = "logs/slurm/allocation-%j.log"


def container_binds(project_root: Path, config: AppConfig) -> list[str]:
    """Directories the container must see: the project, the temporary directory, the folders of the
    configured reference files and of the sample sheet's FASTQ files, and execution.container_binds."""
    # Cluster paths are POSIX strings whatever host renders the script, so they are handled as
    # text with posixpath rather than through this host's Path class.
    root = Path(project_root).resolve()
    folders: list[str] = [str(root)]
    if config.execution.tmpdir:
        folders.append(config.execution.tmpdir)
    reference = config.reference
    for value in (reference.genome_fasta, reference.annotation_file, reference.transcriptome_fasta):
        if value and str(value).startswith("/"):
            folders.append(posixpath.dirname(str(value)))
    for value in (reference.star_index, reference.hisat2_index, reference.salmon_index):
        # Index settings name a directory (STAR, Salmon) or a file prefix inside one (HISAT2).
        if value and str(value).startswith("/"):
            folders.append(str(value) if Path(value).is_dir() else posixpath.dirname(str(value)))
    sheet = root / config.input.samples
    if sheet.is_file():
        try:
            from app.core.metadata import load_metadata
            frame = load_metadata(sheet)
            for column in ("fastq_1", "fastq_2"):
                if column in frame.columns:
                    for value in frame[column].dropna().astype(str):
                        if value.startswith("/"):
                            folders.append(posixpath.dirname(value))
        except Exception:  # noqa: BLE001 - an unreadable sheet is reported by `bulkseq check`, not here
            pass
    folders.extend(config.execution.container_binds)
    # Keep each folder once, and drop one that lies inside another already kept.
    kept: list[str] = []
    for folder in sorted({folder.rstrip("/") or "/" for folder in folders}, key=len):
        if not any(folder == other or folder.startswith(other.rstrip("/") + "/") for other in kept):
            kept.append(folder)
    return kept


def bulkseq_command(config: AppConfig, python: str | None = None) -> str:
    """How `bulkseq` is invoked on this project's cluster: the host command, or through the image."""
    if config.execution.container_image:
        return f"{config.execution.container_runtime} run {shlex.quote(config.execution.container_image)}"
    return "bulkseq"


def finishing_command(project_root: Path, config: AppConfig) -> str:
    return (f"{bulkseq_command(config)} run -C {shlex.quote(str(Path(project_root).resolve()))} "
            f"--cores {config.execution.local_cores}")


def _hhmm(minutes: int) -> str:
    days, rest = divmod(int(minutes), 1440)
    return f"{days}-{rest // 60:02d}:{rest % 60:02d}:00"


def allocation_directives(config: AppConfig, job_name: str) -> list[str]:
    execution = config.execution
    lines = [
        f"--job-name={job_name}",
        "--nodes=1",
        "--ntasks=1",
        f"--time={_hhmm(execution.allocation_time_min)}",
    ]
    if execution.allocation_cpus:
        lines.append(f"--cpus-per-task={execution.allocation_cpus}")
    else:
        lines.append("--exclusive")
    lines.append(f"--mem={execution.allocation_mem_gb}G" if execution.allocation_mem_gb else "--mem=0")
    for key, option in (("slurm_partition", "--partition"), ("slurm_qos", "--qos"),
                        ("slurm_constraint", "--constraint"), ("slurm_clusters", "--clusters"),
                        ("slurm_reservation", "--reservation"), ("slurm_exclude_nodes", "--exclude")):
        value = getattr(execution, key)
        if value:
            lines.append(f"{option}={value}")
    if execution.slurm_account and not execution.slurm_no_account:
        lines.append(f"--account={execution.slurm_account}")
    if execution.slurm_requeue == "requeue":
        lines.append("--requeue")
    elif execution.slurm_requeue == "no-requeue":
        lines.append("--no-requeue")
    lines.extend(execution.slurm_extra.split())
    return lines


def render_allocation_script(project_root: Path, config: AppConfig, python: str | None = None) -> str:
    """The sbatch script text. ``python`` is the interpreter that runs BulkSeq (this one by default).

    The job runs in resume mode, so submitting the script again after a walltime kill unlocks the
    project and repeats the incomplete steps; on a fresh project resume is a plain run.
    """
    execution = config.execution
    root = Path(project_root).resolve()
    python = python or sys.executable
    # The directory that holds the `app` package, so `-m app.cli` also works from a source
    # checkout that was never pip-installed.
    package_root = Path(__file__).resolve().parents[2]
    stage = "all" if execution.compute_nodes_have_internet else "compute"
    name = "bulkseq-" + "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in config.project.name)[:40]
    # The log path and working directory are absolute, so the script may be submitted from any
    # folder; cmd_slurm_script creates logs/slurm, which Slurm does not.
    directives = allocation_directives(config, name) + [f"--chdir={root}", f"--output={root / ALLOCATION_LOG}"]
    header = "\n".join(f"#SBATCH {line}" for line in directives)
    tmpdir = f"export TMPDIR={shlex.quote(execution.tmpdir)}\nmkdir -p \"$TMPDIR\"\n" if execution.tmpdir else ""
    finish = ("" if stage == "all" else
              "\n# Compute nodes have no internet (execution.compute_nodes_have_internet: false), so the\n"
              "# steps that query online services after differential expression, and the figures, check\n"
              "# summary and reports that depend on them, were left out. When this job has ended, finish\n"
              "# on the login node:\n"
              f"#   {finishing_command(root, config)}\n"
              "# or, where the login node may not run the R steps, run only the online steps there and\n"
              "# submit this script once more for the rest:\n"
              f"#   {bulkseq_command(config)} run -C {shlex.quote(str(root))} --stage online --cores {execution.local_cores}\n"
              f"#   sbatch {shlex.quote(str(root / ALLOCATION_SCRIPT))} all\n")
    if execution.container_image:
        # The image carries the environment and BulkSeq itself (packaging/apptainer/bulkseq.def);
        # the project folder and the temporary directory are bound in at the same paths.
        launcher = (f"{execution.container_runtime} exec "
                    + " ".join(f"--bind {shlex.quote(b)}" for b in container_binds(root, config))
                    + f" {shlex.quote(execution.container_image)} python")
        pythonpath = ""
    else:
        launcher = shlex.quote(python)
        pythonpath = f"export PYTHONPATH={shlex.quote(str(package_root))}${{PYTHONPATH:+:$PYTHONPATH}}\n"
    return f"""#!/bin/bash
{header}
#
# Written by `bulkseq slurm-script` from the project's execution settings; regenerate it after
# changing them rather than editing it. Submit with: sbatch {ALLOCATION_SCRIPT}
{finish}
set -euo pipefail
cd {shlex.quote(str(root))}
{pythonpath}{tmpdir}
# Size the local executor from the allocation, keeping 5% of the memory for the system.
CPUS="${{SLURM_CPUS_PER_TASK:-${{SLURM_CPUS_ON_NODE:-1}}}}"
MEM_MB="${{SLURM_MEM_PER_NODE:-0}}"
if [ "$MEM_MB" -le 0 ] 2>/dev/null; then
    MEM_MB=$(awk '/MemTotal/ {{print int($2 / 1024)}}' /proc/meminfo)
fi
MEM_GB=$(( MEM_MB * 95 / 100 / 1000 ))
echo "BulkSeq allocation job $SLURM_JOB_ID on $(hostname): $CPUS CPUs, $MEM_GB GB for the workflow"

# The first argument overrides the stage: `sbatch {ALLOCATION_SCRIPT} all` runs whatever is still
# missing, for the remainder after a login-node `--stage online` run.
exec {launcher} -m app.cli run -C {shlex.quote(str(root))} --exec-profile local --mode resume \\
    --stage "${{1:-{stage}}}" --cores "$CPUS" --memory-gb "$MEM_GB"
"""
