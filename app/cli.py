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
import json
import sys
from pathlib import Path

from app.cli_banner import print_banner
from app.constants import APP_VERSION
from app.core.config_models import AppConfig
from app.core.metadata import load_metadata, validate_metadata
from app.core.project import ProjectExistsError, ProjectManager, is_project_root
from app.core.snakemake_runner import EXEC_PROFILES, build_snakemake_command

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INVALID = 3       # bad config key/value, or a project that is not one
EXIT_GATE = 4          # sanity checks refuse the run


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
    root = Path(args.project).expanduser().resolve() if args.project else Path.cwd()
    if not is_project_root(root):
        _err(f"Not a BulkSeq Studio project (no config/config.yaml): {root}\n"
             f"Create one with:  bulkseq project create --name NAME --workdir DIR")
        return None
    return root


def _load(root: Path) -> AppConfig:
    return ProjectManager().load_config(root)


# ---------------------------------------------------------------- config get/set helpers

def _walk_config(config: AppConfig, dotted: str):
    """Resolve a dotted key against the pydantic model, returning (owner, field, value)."""
    parts = dotted.split(".")
    owner = config
    for name in parts[:-1]:
        if not hasattr(owner, name):
            raise KeyError(dotted)
        owner = getattr(owner, name)
    leaf = parts[-1]
    if not hasattr(owner, leaf):
        raise KeyError(dotted)
    return owner, leaf, getattr(owner, leaf)


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
    if isinstance(current, list):
        return [item.strip() for item in raw.split(",") if item.strip()]
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
        _err(f"A project already exists at {exc.root}\n"
             f"Creating it again would reset its sample sheet, contrasts and settings to "
             f"empty defaults.\nPass --overwrite if that is what you intend.")
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
    samples_file = root / "config" / "samples.tsv"
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
        owner, leaf, current = _walk_config(config, args.key)
    except KeyError:
        _err(f"No such config key: {args.key}\n"
             f"List them with:  bulkseq config show")
        return EXIT_INVALID
    try:
        new_value = _coerce(current, args.value)
    except ValueError as exc:
        _err(f"Cannot parse value for {args.key}: {exc}")
        return EXIT_INVALID

    # Validate through the model rather than trusting the assignment: pydantic owns the
    # constraints (an alpha above 1, a negative threshold), and its message is better than
    # anything reinvented here. The file is only written once validation passes, so a
    # rejected value leaves config.yaml untouched.
    try:
        setattr(owner, leaf, new_value)
        AppConfig.model_validate(config.model_dump(mode="json"))
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
    path = root / "config" / "samples.tsv"
    if not path.is_file():
        _err(f"No sample sheet yet: {path}")
        return EXIT_INVALID
    frame = load_metadata(path)
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
    path = root / "config" / "samples.tsv"
    if not path.is_file():
        _err(f"No sample sheet: {path}")
        return EXIT_GATE
    frame = load_metadata(path)
    allow_pending = config.input.type == "fastq" and bool(
        getattr(config.input, "sra_accessions", None))
    messages = validate_metadata(frame, allow_pending_sra=allow_pending)
    worst = "PASS"
    for message in messages:
        if message["status"] == "FAIL":
            worst = "FAIL"
        elif message["status"] in ("REVIEW_REQUIRED", "WARNING") and worst == "PASS":
            worst = message["status"]
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


# ------------------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--project", metavar="PATH",
                        help="project root (default: current directory)")
    common.add_argument("--json", action="store_true",
                        help="machine-readable output on stdout; implies --quiet")
    common.add_argument("--quiet", action="store_true", help="suppress the banner")

    parser = argparse.ArgumentParser(
        prog="bulkseq",
        description="BulkSeq Studio — reproducible bulk RNA-seq from the command line.",
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

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "json", False):
        args.quiet = True

    if not getattr(args, "func", None):
        print_banner(APP_VERSION, "reproducible bulk RNA-seq",
                     json_output=getattr(args, "json", False),
                     quiet=getattr(args, "quiet", False))
        parser.print_help()
        return EXIT_USAGE

    try:
        return args.func(args)
    except KeyboardInterrupt:
        _err("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
