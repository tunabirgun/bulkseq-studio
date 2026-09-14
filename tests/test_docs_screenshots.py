"""The site's interface screenshots are derived, and the deriving script is held to the site.

The handbook embeds a small set of interface images. Before scripts/capture_docs_screenshots.py
existed they were hand-supplied, so nothing connected a renamed or newly embedded image to the
thing that produces it, and an image could illustrate a release that no longer existed. This
test reads both sides -- the names the site references and the names the capture script writes --
and fails when they diverge in either direction. It does not launch Qt: capturing needs a real
desktop, so the images themselves are refreshed by running the script, not by the suite.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "docs_src" / "content.mjs"
SCRIPT = ROOT / "scripts" / "capture_docs_screenshots.py"
IMAGE_DIR = ROOT / "docs" / "assets" / "images"


def _site_images() -> set[str]:
    """Image file names the site embeds through its imageFigure helper."""
    names = set(re.findall(r"imageFigure\('([^']+)'", CONTENT.read_text(encoding="utf-8")))
    assert names, "no imageFigure call found in docs_src/content.mjs"
    return names


def _script_images() -> dict[str, str]:
    """The published-name -> page map the capture script declares, read without importing it."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SHOTS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("scripts/capture_docs_screenshots.py declares no SHOTS map")


def test_every_embedded_image_has_a_capture_recipe() -> None:
    missing = sorted(_site_images() - set(_script_images()))
    assert not missing, f"the site embeds images nothing captures: {missing}"


def test_the_capture_script_produces_no_image_the_site_ignores() -> None:
    extra = sorted(set(_script_images()) - _site_images())
    assert not extra, f"the capture script writes images the site does not use: {extra}"


def test_every_declared_image_is_present_and_is_a_png() -> None:
    for name in _script_images():
        path = IMAGE_DIR / name
        assert path.exists(), f"{name} is declared but absent from docs/assets/images"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"


def test_the_pages_named_are_real_interface_pages() -> None:
    """A page name the capture script cannot navigate to would fail only at capture time."""
    source = (ROOT / "scripts" / "capture_gui_matrix.py").read_text(encoding="utf-8")
    match = re.search(r"^PAGE_NAMES\s*=\s*(\[[^\]]*\]|\([^)]*\))", source, re.MULTILINE)
    assert match, "capture_gui_matrix.py does not declare PAGE_NAMES"
    pages = set(ast.literal_eval(match.group(1)))
    unknown = sorted(set(_script_images().values()) - pages)
    assert not unknown, f"capture script names pages the interface does not have: {unknown}"
