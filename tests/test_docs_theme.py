"""The documentation theme button must name the theme that is actually showing.

docs/assets/theme.js runs in the browser, so this drives it under Node against a stub page:
the operating system switches from dark to light while the page follows it, and the button
label must follow too. It once stayed on "Dark theme" because only a click relabelled it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

THEME_JS = Path(__file__).resolve().parents[1] / "docs" / "assets" / "theme.js"

HARNESS = r"""
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const mediaListeners = [], windowListeners = {};
const media = { matches: process.argv[3] === 'dark', addEventListener: (_, fn) => mediaListeners.push(fn) };
const name = { textContent: '' }, hint = { textContent: '' };
const button = { title: '', querySelector: sel => (sel === '.theme-name' ? name : hint), addEventListener() {} };
let ready = false;
const document = { documentElement: { dataset: {} }, getElementById: id => (id === 'theme-trigger' && ready ? button : null) };
const window = { matchMedia: () => media, addEventListener: (type, fn) => { (windowListeners[type] ||= []).push(fn); } };
vm.runInNewContext(readFileSync(process.argv[2], 'utf8'), { window, document, localStorage: { getItem: () => null, setItem() {} } });
ready = true;
(windowListeners.DOMContentLoaded || []).forEach(fn => fn());
const start = { theme: document.documentElement.dataset.theme, label: name.textContent };
media.matches = !media.matches;
mediaListeners.forEach(fn => fn());
console.log(JSON.stringify({ start, end: { theme: document.documentElement.dataset.theme, label: name.textContent, title: button.title } }));
"""


def _follow_system_switch(tmp_path: Path, script: Path, starting: str) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed, so the theme script cannot be exercised here")
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(["node", str(harness), str(script), starting], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize(("starting", "ending"), [("dark", "light"), ("light", "dark")])
def test_button_follows_an_operating_system_theme_switch(tmp_path, starting, ending) -> None:
    result = _follow_system_switch(tmp_path, THEME_JS, starting)
    assert result["start"] == {"theme": starting, "label": f"{starting.title()} theme"}
    assert result["end"]["theme"] == ending
    assert result["end"]["label"] == f"{ending.title()} theme"
    assert result["end"]["title"] == f"Switch to the {starting} theme"


def test_harness_catches_a_button_that_ignores_the_switch(tmp_path) -> None:
    # Negative control: relabelling only on click must fail the check above.
    stale = THEME_JS.read_text(encoding="utf-8").replace(
        "const apply = () => { root.dataset.theme = resolved(); label(); };",
        "const apply = () => { root.dataset.theme = resolved(); };")
    assert stale != THEME_JS.read_text(encoding="utf-8"), "negative-control fixture drifted from theme.js"
    broken = tmp_path / "theme.js"
    broken.write_text(stale, encoding="utf-8")
    result = _follow_system_switch(tmp_path, broken, "dark")
    assert result["end"]["theme"] == "light" and result["end"]["label"] == "Dark theme"
