from __future__ import annotations

import re
from pathlib import Path

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
