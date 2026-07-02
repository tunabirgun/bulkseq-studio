from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.metadata import load_metadata, validate_metadata
from app.core.project import ProjectManager
from app.core.runtime_estimator import estimate_runtime
from app.core.sanity_checks import write_check


def main() -> int:
    # UTF-8 stdout so printing metadata that carries a non-ASCII glyph (e.g. a Greek delta
    # in a condition) never raises UnicodeEncodeError on a cp1252 console.
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Create and validate BulkSeq Studio benchmark projects.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List bundled benchmark datasets.")

    create = subparsers.add_parser("create", help="Create a benchmark project.")
    create.add_argument("--benchmark", default="pasilla_paired_subset")
    create.add_argument("--workdir", required=True)
    create.add_argument("--name", default=None)
    create.add_argument("--validate", action="store_true")

    validate = subparsers.add_parser("validate", help="Validate an existing benchmark project.")
    validate.add_argument("--project", required=True)

    args = parser.parse_args()
    if args.command == "list":
        for item in load_benchmark_catalog():
            print(f"{item['id']}\t{item['name']}\t{item['organism_name']}")
        return 0
    if args.command == "create":
        root = create_benchmark_project(args.benchmark, Path(args.workdir), args.name)
        print(root)
        if args.validate:
            _validate_project(root)
        return 0
    if args.command == "validate":
        _validate_project(Path(args.project))
        return 0
    return 1


def _validate_project(root: Path) -> None:
    manager = ProjectManager()
    config = manager.load_config(root)
    samples = load_metadata(root / "config" / "samples.tsv")
    messages = validate_metadata(samples, allow_pending_sra=config.input.type == "sra")
    write_check(root, "01_input_validation", messages)
    estimate = estimate_runtime(config, samples)
    print("Validation")
    for message in messages:
        print(f"{message['status']}: {message['message']}")
    print(f"Runtime estimate: {estimate['range']}")


if __name__ == "__main__":
    raise SystemExit(main())
