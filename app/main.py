from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.constants import APP_NAME
from app.core.paths import app_root
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    # One shared QSettings identity backs theme, window geometry, and splitter state.
    app.setOrganizationName("BulkSeq")
    app.setApplicationName(APP_NAME)
    mode = QSettings().value("theme_mode", "light")
    apply_theme(app, mode=mode if mode in ("light", "dark") else "light")
    icon_path = app_root() / "app" / "assets" / "icons" / "bulkseq.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.setMinimumSize(900, 640)   # logical px; Qt scales per-DPI automatically
    window.resize(1280, 820)          # default when no stored geometry
    window._restore_geometry_state()  # overrides default only if a valid saved geometry exists
    window.show()
    # Self-test mode: construct the window, pump the event loop briefly, then exit.
    # Used to verify a packaged (frozen) build launches without import/path errors.
    if os.environ.get("BULKSEQ_SELFTEST") == "1":
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
