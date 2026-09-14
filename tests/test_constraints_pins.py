from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _requirement_names() -> set[str]:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    return {re.split(r"[><=!~]", line, 1)[0].strip() for line in text.splitlines() if line.strip()}


def _constraint_pins() -> dict[str, str]:
    text = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    pins = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        assert "==" in line, f"constraints.txt line is not an exact pin: {line!r}"
        name, version = line.split("==", 1)
        pins[name.strip()] = version.strip()
    return pins


def test_every_requirement_has_an_exact_constraint_pin() -> None:
    names = _requirement_names()
    pins = _constraint_pins()
    assert names == set(pins), (
        f"constraints.txt must pin exactly the requirements.txt names: "
        f"missing={names - set(pins)} extra={set(pins) - names}"
    )


def test_constraints_pins_are_exact_versions() -> None:
    for name, version in _constraint_pins().items():
        assert re.match(r"^[0-9][0-9A-Za-z.]*$", version), f"{name} is not an exact version: {version!r}"


def test_negative_extra_constraint_name_is_caught() -> None:
    names = _requirement_names()
    pins = dict(_constraint_pins())
    pins["some-unrelated-package"] = "1.0.0"
    assert names != set(pins)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _exact_pins(path: Path) -> list[tuple[str, str]]:
    pins = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        assert "==" in line, f"{path.name} line is not an exact pin: {line!r}"
        name, version = line.split("==", 1)
        assert re.match(r"^[0-9][0-9A-Za-z.]*$", version.strip()), (
            f"{path.name} pins {name.strip()} at a non-exact version: {version!r}")
        pins.append((_normalise(name), version.strip()))
    return pins


def _assert_build_pins(path: Path) -> None:
    names = [name for name, _ in _exact_pins(path)]
    assert names, f"{path.name} declares no build dependency"
    assert len(names) == len(set(names)), f"{path.name} names a package twice: {names}"


def test_build_requirements_are_exactly_pinned_and_named_once() -> None:
    # requirements-build.txt has no constraints file of its own: it is the pin. A range here
    # means the packaged executable is built against whatever PyInstaller happens to be
    # current, which is the drift constraints.txt exists to prevent for the runtime.
    _assert_build_pins(ROOT / "requirements-build.txt")


def test_build_and_runtime_constraint_names_are_disjoint() -> None:
    # A name pinned in both files can be installed twice at two versions depending on which file
    # a given install reads.
    build = {name for name, _ in _exact_pins(ROOT / "requirements-build.txt")}
    runtime = {_normalise(name) for name in _constraint_pins()}
    overlap = sorted(build & runtime)
    assert not overlap, f"pinned in both requirements-build.txt and constraints.txt: {overlap}"


def test_build_pin_gate_rejects_a_range_and_a_duplicated_name(tmp_path: Path) -> None:
    text = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    ranged = tmp_path / "ranged.txt"
    ranged.write_text(text.replace("==", ">=", 1), encoding="utf-8")
    with pytest.raises(AssertionError, match="not an exact pin"):
        _assert_build_pins(ranged)

    first = [line for line in text.splitlines() if line.strip() and not line.startswith("#")][0]
    duplicated = tmp_path / "duplicated.txt"
    name = first.split("==", 1)[0]
    duplicated.write_text(text + f"\n{name.upper()}==0.0.1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="names a package twice"):
        _assert_build_pins(duplicated)
