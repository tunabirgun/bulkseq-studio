from __future__ import annotations

import importlib.metadata
import json
import re
import sys
from pathlib import Path

import yaml


CONDA_SPEC = re.compile(r"([A-Za-z0-9_.-]+)==([^=\s]+)=([^=\s]+)")
PIP_SPEC = re.compile(r"([A-Za-z0-9_.-]+)==([^=\s]+)")


def read_lock(path: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        raise ValueError("lock has no dependency list")
    conda: dict[str, tuple[str, str]] = {}
    pip: dict[str, str] = {}
    for item in data["dependencies"]:
        if isinstance(item, str):
            match = CONDA_SPEC.fullmatch(item)
            if match is None or match.group(1) in conda:
                raise ValueError(f"malformed or duplicate Conda lock entry: {item!r}")
            conda[match.group(1)] = (match.group(2), match.group(3))
        elif isinstance(item, dict) and set(item) == {"pip"} and isinstance(item["pip"], list):
            for pin in item["pip"]:
                match = PIP_SPEC.fullmatch(pin) if isinstance(pin, str) else None
                if match is None or match.group(1) in pip:
                    raise ValueError(f"malformed or duplicate pip lock entry: {pin!r}")
                pip[match.group(1)] = match.group(2)
        else:
            raise ValueError(f"unsupported lock entry: {item!r}")
    if not conda:
        raise ValueError("lock has no Conda packages")
    return conda, pip


def check_inventory(lock: Path, prefix: Path, pip_distribution=None) -> tuple[list[str], list[str]]:
    expected, pip_expected = read_lock(lock)
    metadata_dir = prefix / "conda-meta"
    records = sorted(metadata_dir.glob("*.json")) if metadata_dir.is_dir() else []
    if not records:
        return [f"no Conda records in {metadata_dir}"], []
    installed: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    for path in records:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            problems.append(f"unreadable Conda record {path.name}: {exc}")
            continue
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key]
                                             for key in ("name", "version", "build")):
            problems.append(f"malformed Conda record {path.name}")
            continue
        name = item["name"]
        if name in installed:
            problems.append(f"duplicate installed Conda package {name}")
        installed[name] = (item["version"], item["build"])
    for name in sorted(expected.keys() - installed.keys()):
        problems.append(f"missing Conda package {name}")
    extras = sorted(installed.keys() - expected.keys())
    for name in sorted(expected.keys() & installed.keys()):
        if installed[name] != expected[name]:
            problems.append(f"Conda package {name}: expected {expected[name]}, found {installed[name]}")
    distribution = pip_distribution or importlib.metadata.distribution
    selected = prefix.resolve()
    for name, wanted in sorted(pip_expected.items()):
        try:
            package = distribution(name)
            origin = Path(package.locate_file("")).resolve()
        except Exception as exc:
            problems.append(f"pip package {name}: unavailable ({exc})")
            continue
        if selected != origin and selected not in origin.parents:
            problems.append(f"pip package {name}: metadata outside selected environment: {origin}")
        elif package.version != wanted:
            problems.append(f"pip package {name}: expected {wanted}, found {package.version}")
    return problems, extras


def main() -> int:
    if len(sys.argv) != 3:
        print("LOCK_INVENTORY_FAIL: expected LOCK_FILE ENV_PREFIX", file=sys.stderr)
        return 2
    lock, prefix = map(Path, sys.argv[1:])
    if prefix.resolve() != Path(sys.prefix).resolve():
        print("LOCK_INVENTORY_FAIL: validator is not running inside the selected environment",
              file=sys.stderr)
        return 1
    try:
        expected, pip_expected = read_lock(lock)
        problems, extras = check_inventory(lock, prefix)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"LOCK_INVENTORY_FAIL: {exc}", file=sys.stderr)
        return 1
    if problems:
        for item in problems[:12]:
            print(f"LOCK_INVENTORY_FAIL: {item}", file=sys.stderr)
        if len(problems) > 12:
            print(f"LOCK_INVENTORY_FAIL: {len(problems) - 12} further problems", file=sys.stderr)
        return 1
    print(f"LOCK_INVENTORY_PASS:{len(expected)}:{len(pip_expected)}:{len(extras)}")
    if extras:
        print(f"LOCK_INVENTORY_WARNING: retained additional Conda packages: {','.join(extras)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
