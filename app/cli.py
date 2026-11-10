from __future__ import annotations

"""BulkSeq Studio command line.

Reuses app.core wholesale — the same project scaffolding, config model, sample-sheet
validation and Snakemake command builder the GUI uses. That reuse is the point: the CLI
must not become a second, subtly different way to run the same science.

The rule this file must keep: it NEVER hand-assembles a snakemake argument vector.
app.core.snakemake_runner.build_snakemake_command() is the single function that turns an
AppConfig into an argv, and both front ends call it, so GUI/CLI equivalence is structural
rather than something a test has to hope holds.

stdout carries only what a command was asked to produce. Banner, progress and warnings go
to stderr, so `bulkseq config show --json > c.json` is always clean.
"""

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.cli_banner import print_banner
from app.constants import APP_VERSION
from app.core.config_models import AppConfig
from app.core.metadata import load_metadata
from app.core.preflight_checks import input_validation_messages
from app.core.paths import project_configured_path
from app.core.project import ProjectExistsError, ProjectManager, is_project_root
from app.core.sanity_checks import aggregate_status
from app.core.slurm_script import ALLOCATION_SCRIPT, finishing_command, render_allocation_script
from app.core.snakemake_runner import (
    EXEC_PROFILES,
    ONLINE_ANALYSIS_RULES,
    PREFETCH_RULES,
    RUN_STAGES,
    SnakemakeRunner,
    build_snakemake_command,
    cluster_launch_problems,
    defined_rules,
    run_snakemake_sync,
    snakemake_run_state,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INVALID = 3       # not a project, an unreadable or invalid config, a workflow copy that cannot be refreshed, or a host that cannot launch the cluster target
EXIT_GATE = 4          # `bulkseq check` found a FAIL, or the sample sheet is missing or unreadable
EXIT_RUN_FAILED = 5    # the Snakemake run itself failed
EXIT_INTERRUPTED = 130

# Listed by --help. Exit 1 is Python's own status for an unhandled error, not one of ours.
EXIT_CODE_HELP = (
    (EXIT_OK, "success; for check, also when the worst finding is WARNING or REVIEW_REQUIRED"),
    (1, "unexpected error, printed with a traceback; please report it"),
    (EXIT_USAGE, "usage error: no command, or an invalid option"),
    (EXIT_INVALID, "not a project, an unreadable or invalid config, the workflow copy could not be refreshed, "
                   "or this host cannot launch the chosen cluster target"),
    (EXIT_GATE, "check found a FAIL, or the sample sheet is missing or unreadable"),
    (EXIT_RUN_FAILED, "the workflow run failed"),
    (EXIT_INTERRUPTED, "interrupted with Ctrl-C"),
)


class ConfigLoadError(Exception):
    """config/config.yaml exists but cannot be parsed, or does not validate."""


def _configure_streams() -> None:
    # UTF-8 on both streams before anything prints: sample metadata can carry a non-ASCII
    # glyph (a Greek delta in a genotype, say), and a cp1252 console would otherwise raise
    # UnicodeEncodeError. Mirrors app/benchmark_cli.py.
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _emit(payload, args) -> None:
    """Write a result to stdout, as JSON when --json is set."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
    elif isinstance(payload, str):
        print(payload)
    elif isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
    else:
        print(payload)


def _resolve_project(args) -> Path | None:
    selected = getattr(args, "project", None)
    root = Path(selected).expanduser().resolve() if selected else Path.cwd()
    if not is_project_root(root):
        _err(f"Not a BulkSeq Studio project (no config/config.yaml): {root}\n"
             f"Create one with:  bulkseq project create --name NAME --workdir DIR")
        return None
    return root


def _load(root: Path) -> AppConfig:
    try:
        return ProjectManager().load_config(root)
    except ValidationError as exc:
        fields = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()[:3])
        raise ConfigLoadError(f"Invalid config/config.yaml in {root}: {fields}") from exc
    except yaml.YAMLError as exc:
        detail = str(exc).strip().splitlines()
        raise ConfigLoadError(
            f"Unreadable config/config.yaml in {root}: {detail[0] if detail else 'YAML syntax error'}") from exc


def _metadata_for_project_validation(root: Path, frame):
    """Resolve relative FASTQ cells for validation without changing the saved sample sheet."""
    resolved = frame.copy()
    for column in ("fastq_1", "fastq_2"):
        if column in resolved.columns:
            resolved[column] = resolved[column].map(
                lambda value: str(project_configured_path(root, value)) if str(value).strip() else ""
            )
    return resolved


# ---------------------------------------------------------------- config get/set helpers

def _walk_config(config: AppConfig, dotted: str, create: bool = False):
    """Resolve a dotted key against the pydantic model, returning (owner, field, value).

    A mapping field (execution.rule_resources) is walked by key; with ``create`` a missing
    entry is added with its model's defaults, so `config set execution.rule_resources.
    star_index.slurm_partition bigmem` works on a project that has no entry yet.
    """
    parts = dotted.split(".")
    owner = config
    for index, name in enumerate(parts[:-1]):
        if isinstance(owner, dict):
            if name not in owner:
                value_type = _mapping_value_type(config, parts[:index])
                if not create or value_type is None:
                    raise KeyError(dotted)
                owner[name] = value_type()
            owner = owner[name]
            continue
        if not hasattr(owner, name):
            raise KeyError(dotted)
        owner = getattr(owner, name)
    leaf = parts[-1]
    if isinstance(owner, dict):
        return owner, leaf, owner.get(leaf, "")
    if not hasattr(owner, leaf):
        raise KeyError(dotted)
    return owner, leaf, getattr(owner, leaf)


def _mapping_value_type(config: AppConfig, path: list[str]):
    """The model class stored in the mapping field at ``path`` (e.g. RuleSlurmPlacement)."""
    import typing

    owner = config
    for name in path[:-1]:
        owner = getattr(owner, name)
    field = type(owner).model_fields.get(path[-1]) if hasattr(type(owner), "model_fields") else None
    args = typing.get_args(field.annotation) if field else ()
    return args[1] if len(args) == 2 and isinstance(args[1], type) else None


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _coerce(current, raw: str):
    """Parse a command-line string against the current value's type.

    Validation proper is pydantic's job — this only turns "0.01" into a float and "true"
    into a bool so the model sees the right kind of thing.
    """
    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"expected true/false, got {raw!r}")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if current is None or isinstance(current, str):
        return raw
    if isinstance(current, (list, dict)) and raw.strip()[:1] in ("[", "{"):
        # Structured values as YAML/JSON, e.g. a contrast list or execution.rule_resources.
        return yaml.safe_load(raw)
    if isinstance(current, list):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if hasattr(current, "model_dump") and raw.strip()[:1] == "{":
        return type(current).model_validate(yaml.safe_load(raw))
    return raw


# ---------------------------------------------------------------------------- commands

def cmd_version(args) -> int:
    _emit({"version": APP_VERSION} if args.json else f"BulkSeq Studio {APP_VERSION}", args)
    return EXIT_OK


def cmd_project_create(args) -> int:
    manager = ProjectManager()
    workdir = Path(args.workdir).expanduser().resolve()
    try:
        root = manager.create_project(args.name, workdir, overwrite=args.overwrite)
    except ProjectExistsError as exc:
        if exc.is_file:
            _err(f"Project destination is an existing file: {exc.root}\n"
                 "Choose a new project name or working directory.")
        elif exc.is_project:
            _err(f"A project already exists at {exc.root}\n"
                 f"Creating it again would reset its sample sheet, contrasts and settings to "
                 f"empty defaults.\nPass --overwrite if that is what you intend.")
        else:
            _err(f"Project destination is occupied: {exc.root}\n{exc}\n"
                 "Pass --overwrite only if you intend to scaffold into this directory.")
        return EXIT_INVALID
    except (OSError, ValueError) as exc:
        _err(f"Project creation failed: {exc}")
        return EXIT_INVALID
    _emit({"project_root": str(root)} if args.json else f"Created {root}", args)
    return EXIT_OK


def cmd_project_info(args) -> int:
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    config = _load(root)
    samples_file = project_configured_path(root, config.input.samples)
    n_samples = 0
    if samples_file.is_file():
        try:
            n_samples = len(load_metadata(samples_file))
        except Exception:  # noqa: BLE001 - an unreadable sheet is reported as 0, not fatal
            n_samples = 0
    payload = {
        "project_root": str(root),
        "project_name": config.project.name,
        "input_type": config.input.type,
        "samples": n_samples,
        "aligner": config.workflow.aligner,
        "de_engine": getattr(config.workflow, "de_engine", "deseq2"),
        "design_formula": config.deseq2.design_formula,
        "alpha": config.deseq2.alpha,
        "organism": config.reference.organism_name,
    }
    _emit(payload, args)
    return EXIT_OK


def cmd_config_show(args) -> int:
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    data = _load(root).model_dump(mode="json")
    if args.section:
        if args.section not in data:
            _err(f"No such config section: {args.section}\n"
                 f"Sections: {', '.join(sorted(data))}")
            return EXIT_INVALID
        data = {args.section: data[args.section]}
    print(json.dumps(data, indent=2, default=str))
    return EXIT_OK


def cmd_config_get(args) -> int:
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    try:
        _, _, value = _walk_config(_load(root), args.key)
    except KeyError:
        _err(f"No such config key: {args.key}")
        return EXIT_INVALID
    _emit({args.key: value} if args.json else str(value), args)
    return EXIT_OK


def cmd_config_set(args) -> int:
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    manager = ProjectManager()
    config = manager.load_config(root)
    try:
        owner, leaf, current = _walk_config(config, args.key, create=True)
    except KeyError:
        _err(f"No such config key: {args.key}\n"
             f"List them with:  bulkseq config show")
        return EXIT_INVALID
    try:
        if isinstance(owner, dict) and leaf not in owner:
            # A new entry of a mapping field: parse the value as that entry's model.
            value_type = _mapping_value_type(config, args.key.split(".")[:-1])
            new_value = value_type.model_validate(yaml.safe_load(args.value)) if value_type else _coerce(current, args.value)
        else:
            new_value = _coerce(current, args.value)
    except (ValueError, yaml.YAMLError) as exc:
        _err(f"Cannot parse value for {args.key}: {exc}")
        return EXIT_INVALID

    # Validate through the model rather than trusting the assignment: pydantic owns the
    # constraints (an alpha above 1, a negative threshold), and its message is better than
    # anything reinvented here. The file is only written once validation passes, so a
    # rejected value leaves config.yaml untouched.
    try:
        if isinstance(owner, dict):
            owner[leaf] = new_value
        else:
            setattr(owner, leaf, new_value)
        # warnings=False: a mapping entry assigned above is still a plain dict until validated.
        config = AppConfig.model_validate(config.model_dump(mode="json", warnings=False))
    except Exception as exc:  # noqa: BLE001 - pydantic raises its own ValidationError type
        _err(f"Invalid value for {args.key}: {exc}")
        return EXIT_INVALID

    manager.save_config(root, config)
    _emit({args.key: new_value} if args.json else f"{args.key} = {new_value}", args)
    return EXIT_OK


def cmd_samples_show(args) -> int:
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    config = _load(root)
    path = project_configured_path(root, config.input.samples)
    if not path.is_file():
        _err(f"No sample sheet yet: {path}")
        return EXIT_INVALID
    try:
        frame = load_metadata(path)
    except Exception as exc:  # noqa: BLE001 - report malformed user tables without a traceback
        _err(f"Could not read sample sheet {path}: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(frame.to_dict(orient="records"), indent=2, default=str))
    else:
        print(frame.to_string(index=False))
    return EXIT_OK


def cmd_check(args) -> int:
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    config = _load(root)
    path = project_configured_path(root, config.input.samples)
    if not path.is_file():
        _err(f"No sample sheet: {path}")
        return EXIT_GATE
    try:
        frame = load_metadata(path)
    except Exception as exc:  # noqa: BLE001 - report malformed user tables without a traceback
        _err(f"Could not read sample sheet {path}: {exc}")
        return EXIT_GATE
    # The same findings the interface's Start gate records, so both front doors agree.
    messages = input_validation_messages(config, root, _metadata_for_project_validation(root, frame))
    worst = aggregate_status(messages)
    if args.json:
        print(json.dumps({"status": worst, "messages": messages}, indent=2))
    else:
        for message in messages:
            print(f"[{message['status']}] {message['message']}")
        print(f"\nOverall: {worst}")
    return EXIT_OK if worst != "FAIL" else EXIT_GATE


def cmd_print_command(args) -> int:
    """Show the exact Snakemake invocation, without running it.

    Built by the same function the GUI calls, so what is printed here is what a run
    actually executes.
    """
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    config = _load(root)
    # A cluster executor submits jobs from wherever the CLI runs; it does not go through
    # WSL. Only the local target needs the Windows->WSL2 hop.
    use_wsl = sys.platform.startswith("win") and args.exec_profile == "local"
    command = build_snakemake_command(root, config, args.mode, use_wsl=use_wsl,
                                      exec_profile=args.exec_profile)
    if args.json:
        print(json.dumps({"mode": args.mode, "exec_profile": args.exec_profile,
                          "use_wsl": use_wsl, "argv": command.command,
                          "display": command.display}, indent=2))
    else:
        print(command.display)
    return EXIT_OK


def cmd_run(args) -> int:
    """Run the Snakemake workflow to completion, streaming its output to stderr.

    Goes through run_snakemake_sync(), which calls the same build_snakemake_command() as
    print-command and detects a snakemake-reported failure the way the GUI does, so a
    masked exit-0 from `micromamba run` under WSL is still reported as a failure here.

    Mirrors the GUI's resume flow: `--mode resume` against a lock left by a killed/crashed
    run must unlock first (the GUI's _resume_interrupted does this before launching
    --rerun-incomplete), or Snakemake refuses to start against a locked directory.
    """
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    config = _load(root)
    stage = getattr(args, "stage", "all")
    if stage != "all" and args.exec_profile != "local":
        _err("--stage splits a run across hosts with the local executor; use it with --exec-profile local "
             "(see `bulkseq slurm-script`).")
        return EXIT_USAGE
    errors, warnings = cluster_launch_problems(config, args.exec_profile, args.mode, project_root=root)
    for message in warnings:
        _err(f"Warning: {message}")
    if errors:
        for message in errors:
            _err(message)
        return EXIT_INVALID
    # Mirrors the GUI's pre-run re-sync (main_window.py's launch flow): an existing project
    # keeps its own copy of workflow/, so an app update's workflow fix would not reach a CLI
    # run unless re-synced here too. A failed sync must block execution because its provenance
    # cannot identify a verified workflow tree.
    try:
        synced = ProjectManager().sync_workflow_if_outdated(root)
        if synced:
            print(f"Updated project workflow scripts to match this app version ({synced}).", file=sys.stderr)
    except Exception as exc:
        print(f"Could not refresh project workflow scripts: {exc}", file=sys.stderr)
        return EXIT_INVALID
    if args.mode == "resume" and snakemake_run_state(root).get("locked"):
        use_wsl = sys.platform.startswith("win") and args.exec_profile == "local"
        unlock_cmd = build_snakemake_command(root, config, "unlock", use_wsl=use_wsl,
                                              exec_profile=args.exec_profile)
        SnakemakeRunner(root, unlock_cmd).unlock(config)
    options = {"cores": getattr(args, "cores", None), "memory_gb": getattr(args, "memory_gb", None)}
    if stage != "all":
        rules = defined_rules(root, config)
        if rules is None:
            _err("Could not list the workflow's rules for this stage; run `bulkseq run --mode dry-run` "
                 "to see why.")
            return EXIT_RUN_FAILED
        if stage in ("prefetch", "online") and not set(rules) & set(PREFETCH_RULES if stage == "prefetch" else ONLINE_ANALYSIS_RULES):
            print(f"This project has no {stage} stage: none of its steps belongs to it.", file=sys.stderr)
            return EXIT_OK
        options.update(stage=stage, defined_rules=rules)
    with _hangup_stops_run(), _run_log(root, args.exec_profile, stage) as emit:
        code = run_snakemake_sync(root, config, args.mode, exec_profile=args.exec_profile, on_line=emit, **options)
    return EXIT_OK if code == 0 else EXIT_RUN_FAILED


@contextlib.contextmanager
def _hangup_stops_run():
    """Treat a closed terminal (SIGHUP) or a SIGTERM like Ctrl-C.

    Without this a dropped SSH session kills only this relay: Snakemake runs on in its own
    process group with nowhere to write, and on a cluster its queued jobs are orphaned. Raising
    KeyboardInterrupt sends it through the same stop path as Ctrl-C, which cancels the jobs.
    Run inside tmux or screen (or as a batch job) to survive a disconnect instead.
    """
    previous = {}

    def interrupt(signum, _frame):
        raise KeyboardInterrupt

    for name in ("SIGHUP", "SIGTERM"):
        if hasattr(signal, name):
            try:
                previous[name] = signal.signal(getattr(signal, name), interrupt)
            except (ValueError, OSError):
                pass
    try:
        yield
    finally:
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)


@contextlib.contextmanager
def _run_log(root: Path, exec_profile: str, stage: str):
    """Mirror a cluster, split-stage or in-allocation run's output into logs/ (outlives the terminal)."""
    def show(line: str) -> None:
        print(line, file=sys.stderr)

    if exec_profile == "local" and stage == "all" and not os.environ.get("SLURM_JOB_ID"):
        yield show
        return
    path = root / "logs" / f"bulkseq_run_{datetime.now():%Y%m%d_%H%M%S}_{exec_profile}_{stage}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        print(f"Recording this run's output in {path}", file=sys.stderr)

        def tee(line: str) -> None:
            show(line)
            try:
                handle.write(line + "\n")
                handle.flush()
            except OSError:
                pass
        yield tee


def cmd_slurm_script(args) -> int:
    """Write (and optionally submit) the single-allocation job script."""
    root = _resolve_project(args)
    if root is None:
        return EXIT_INVALID
    config = _load(root)
    execution = config.execution
    script = root / ALLOCATION_SCRIPT
    # Slurm does not create the directory of --output; a missing one fails the job at once.
    (root / "logs" / "slurm").mkdir(parents=True, exist_ok=True)
    script.write_text(render_allocation_script(root, config), encoding="utf-8", newline="\n")
    print(f"Wrote {script}", file=sys.stderr)
    if not args.submit:
        _emit({"script": str(script)} if args.json else f"Submit it with:  sbatch {script}", args)
        return EXIT_OK
    sbatch = shutil.which("sbatch")
    if not sbatch:
        _err("'sbatch' is not on PATH; run this on the cluster's login node.")
        return EXIT_INVALID
    if not execution.compute_nodes_have_internet and not args.no_prefetch:
        print("Downloading inputs on this host first (compute nodes have no internet: "
              "execution.compute_nodes_have_internet is false)...", file=sys.stderr)
        prefetch = argparse.Namespace(**{**vars(args), "mode": "resume", "exec_profile": "local", "stage": "prefetch",
                                         "cores": execution.local_cores, "memory_gb": None})
        code = cmd_run(prefetch)
        if code != EXIT_OK:
            _err("The download stage failed, so nothing was submitted. Fix it and rerun this command.")
            return code
    result = subprocess.run([sbatch, "--parsable", str(script)], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        _err(f"sbatch refused the job:\n{result.stderr.strip()}")
        return EXIT_RUN_FAILED
    job = result.stdout.strip().split(";")[0]
    follow = f"squeue -j {job}; tail -f {root / 'logs' / 'slurm' / f'allocation-{job}.log'}"
    finish = ("" if execution.compute_nodes_have_internet else
              f"\nWhen it has finished, complete the online steps, figures and reports here:  {finishing_command(root, config)}")
    _emit({"job_id": job, "script": str(script)} if args.json
          else f"Submitted job {job}. Follow it with:  {follow}{finish}", args)
    return EXIT_OK


# ------------------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--project", metavar="PATH",
                        default=argparse.SUPPRESS,
                        help="project root (default: current directory)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output on stdout; implies --quiet")
    common.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="suppress the banner")

    parser = argparse.ArgumentParser(
        prog="bulkseq",
        description="BulkSeq Studio — reproducible bulk RNA-seq from the command line.",
        epilog="exit status:\n" + "\n".join(f"  {code:>3}  {meaning}" for code, meaning in EXIT_CODE_HELP),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common])
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("version", parents=[common], help="print the version").set_defaults(
        func=cmd_version)

    project = sub.add_parser("project", parents=[common], help="create and inspect projects")
    project_sub = project.add_subparsers(dest="project_command", metavar="SUBCOMMAND")
    create = project_sub.add_parser("create", parents=[common], help="scaffold a new project")
    create.add_argument("--name", required=True)
    create.add_argument("--workdir", required=True)
    create.add_argument("--overwrite", action="store_true",
                        help="reset an existing project's configuration (destructive)")
    create.set_defaults(func=cmd_project_create)
    project_sub.add_parser("info", parents=[common], help="summarise the project").set_defaults(
        func=cmd_project_info)

    config = sub.add_parser("config", parents=[common], help="read and edit the configuration")
    config_sub = config.add_subparsers(dest="config_command", metavar="SUBCOMMAND")
    show = config_sub.add_parser("show", parents=[common], help="print the configuration")
    show.add_argument("--section", help="limit to one section, e.g. deseq2")
    show.set_defaults(func=cmd_config_show)
    get = config_sub.add_parser("get", parents=[common], help="read one value")
    get.add_argument("key", metavar="KEY", help="dotted key, e.g. deseq2.alpha")
    get.set_defaults(func=cmd_config_get)
    setter = config_sub.add_parser("set", parents=[common], help="write one value")
    setter.add_argument("key", metavar="KEY")
    setter.add_argument("value", metavar="VALUE")
    setter.set_defaults(func=cmd_config_set)

    samples = sub.add_parser("samples", parents=[common], help="inspect the sample sheet")
    samples_sub = samples.add_subparsers(dest="samples_command", metavar="SUBCOMMAND")
    samples_sub.add_parser("show", parents=[common], help="print the sample sheet").set_defaults(
        func=cmd_samples_show)

    sub.add_parser("check", parents=[common],
                   help="validate the sample sheet and design").set_defaults(func=cmd_check)

    printcmd = sub.add_parser("print-command", parents=[common],
                              help="show the Snakemake invocation without running it")
    printcmd.add_argument("--mode", default="run",
                          choices=["run", "dry-run", "resume", "unlock", "figures"])
    printcmd.add_argument("--exec-profile", default="local", choices=list(EXEC_PROFILES),
                          dest="exec_profile",
                          help="where jobs run: this machine, or a cluster scheduler")
    printcmd.set_defaults(func=cmd_print_command)

    run = sub.add_parser("run", parents=[common],
                         help="run the Snakemake workflow to completion")
    run.add_argument("--mode", default="run", choices=["run", "dry-run", "resume"])
    run.add_argument("--exec-profile", default="local", choices=list(EXEC_PROFILES),
                     dest="exec_profile",
                     help="where jobs run: this machine, or a cluster scheduler")
    run.add_argument("--stage", default="all", choices=list(RUN_STAGES),
                     help="run part of the workflow with the local executor: prefetch = only the downloads "
                          "(a host with internet); compute = all but the online steps after differential "
                          "expression and what depends on them (a node without internet); online = only "
                          "those online steps (a host with internet); all = everything still missing")
    run.add_argument("--cores", type=_positive_int, default=None,
                     help="CPU cores for this run only (local executor; default resources.total_threads)")
    run.add_argument("--memory-gb", type=_positive_int, default=None, dest="memory_gb",
                     help="memory in GB for this run only (local executor; default resources.total_memory_gb)")
    run.set_defaults(func=cmd_run)

    script = sub.add_parser("slurm-script", parents=[common],
                            help="write the job script that runs the whole workflow in one Slurm allocation")
    script.add_argument("--submit", action="store_true",
                        help="download the inputs on this host, then submit the script with sbatch")
    script.add_argument("--no-prefetch", action="store_true", dest="no_prefetch",
                        help="with --submit: skip the download stage (the inputs are already present)")
    script.set_defaults(func=cmd_slurm_script)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.json = getattr(args, "json", False)
    args.quiet = getattr(args, "quiet", False)
    if args.json:
        args.quiet = True

    if not getattr(args, "func", None):
        print_banner(APP_VERSION, "reproducible bulk RNA-seq",
                     json_output=getattr(args, "json", False),
                     quiet=getattr(args, "quiet", False))
        parser.print_help()
        return EXIT_USAGE

    try:
        return args.func(args)
    except ConfigLoadError as exc:
        _err(str(exc))
        return EXIT_INVALID
    except KeyboardInterrupt:
        _err("\nInterrupted.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
