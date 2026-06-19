from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from app.constants import APP_NAME
from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.resize(1280, 820)
    window.show()
    # Self-test mode: construct the window, pump the event loop briefly, then exit.
    # Used to verify a packaged (frozen) build launches without import/path errors.
    if os.environ.get("BULKSEQ_SELFTEST") == "1":
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
