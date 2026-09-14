"""Capture the documentation site's interface screenshots at the size the site uses.

The site embeds a small, fixed set of interface images. They must be retaken whenever the
window they show changes, or the handbook illustrates a release that no longer exists. This
script reuses the synthetic project and sanitisation from ``capture_gui_matrix``, so the
images never carry a real project path, and writes straight into ``docs/assets/images``.

Run it, then rebuild the site: ``node docs_src/build.mjs``.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme

from capture_gui_matrix import (  # noqa: E402
    PAGE_NAMES,
    _create_synthetic_project,
    _navigate,
    _sanitize_visible_paths,
    _settle,
    _set_theme,
)

# Published name -> the page it shows. The site's markup references these file names, so a
# rename here is a broken image there; docs_src/content.mjs is the other half of this pair.
SHOTS = {
    "four-stage-navigator.png": "project",
    "run-monitor.png": "run-monitor",
}
SIZE = (1366, 700)


def capture(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("BulkSeq Studio QA")
    app.setApplicationName("BulkSeq Studio docs screenshots")
    QSettings().clear()
    QSettings().setValue("theme_mode", "light")
    apply_theme(app, "light")

    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="bulkseq-docs-shots-") as temporary:
        root = _create_synthetic_project(Path(temporary))
        window = MainWindow()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.resize(*SIZE)
        window.move(40, 40)
        window.show()
        _settle(300)
        window._load_project(root)
        _sanitize_visible_paths(window, project_open=True)
        _set_theme(window, "light")
        window.resize(*SIZE)
        _settle(260)

        for name, page in SHOTS.items():
            if page not in PAGE_NAMES:
                raise SystemExit(f"unknown page {page!r}; known pages: {sorted(PAGE_NAMES)}")
            _navigate(window, PAGE_NAMES.index(page))
            _settle(240)
            target = out_dir / name
            image = window.grab().toImage()
            if image.width() != SIZE[0] or image.height() != SIZE[1]:
                raise SystemExit(f"{name}: grabbed {image.width()}x{image.height()}, expected {SIZE[0]}x{SIZE[1]}")
            if not image.save(str(target), "PNG"):
                raise SystemExit(f"{name}: could not write {target}")
            written.append(target)
            print(f"  {name}  {page}  {target.stat().st_size} bytes")
        window.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "assets" / "images",
                        help="Directory to write into (default: the site's image directory)")
    args = parser.parse_args()
    capture(args.out.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
