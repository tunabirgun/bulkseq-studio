from __future__ import annotations

import os
import sys

from PySide6.QtCore import QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from app.constants import APP_NAME, APP_VERSION
from app.core.paths import app_root
from app.ui.main_window import MainWindow
from app.ui.theme import PALETTES, apply_theme


def _make_splash(mode: str) -> QSplashScreen:
    # A branded loading card shown while the main window builds, so the user sees
    # progress and cannot interact with a half-constructed window.
    pal = PALETTES.get(mode, PALETTES["light"])
    w, h = 460, 280
    pix = QPixmap(w, h)
    pix.fill(QColor(pal["SURFACE"]))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(QColor(pal["BORDER"]))
    p.drawRect(0, 0, w - 1, h - 1)
    logo = app_root() / "app" / "assets" / "icons" / "bulkseq_256.png"
    if logo.exists():
        lp = QPixmap(str(logo)).scaled(108, 108, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((w - lp.width()) // 2, 38, lp)
    p.setPen(QColor(pal["TEXT"]))
    p.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
    p.drawText(QRect(0, 158, w, 32), Qt.AlignmentFlag.AlignHCenter, "BulkSeq Studio")
    p.setPen(QColor(pal["MUTED_TEXT"]))
    p.setFont(QFont("Segoe UI", 9))
    p.drawText(QRect(0, 192, w, 20), Qt.AlignmentFlag.AlignHCenter,
               f"Reproducible bulk RNA-seq  ·  v{APP_VERSION}")
    p.end()
    return QSplashScreen(pix)


def main() -> int:
    app = QApplication(sys.argv)
    # One shared QSettings identity backs theme, window geometry, and splitter state.
    app.setOrganizationName("BulkSeq")
    app.setApplicationName(APP_NAME)
    raw_mode = QSettings().value("theme_mode", "light")
    mode = raw_mode if raw_mode in ("light", "dark") else "light"
    apply_theme(app, mode=mode)
    icon_path = app_root() / "app" / "assets" / "icons" / "bulkseq.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Splash while the (slow) main window builds. Skipped in headless self-test.
    splash = None
    if os.environ.get("BULKSEQ_SELFTEST") != "1":
        splash = _make_splash(mode)
        splash.showMessage(
            "Loading…",
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor(PALETTES[mode]["MUTED_TEXT"]),
        )
        splash.show()
        app.processEvents()

    window = MainWindow()             # minimum size is set in __init__
    window.resize(1280, 820)          # default when no stored geometry
    window._restore_geometry_state()  # overrides default only if a valid saved geometry exists
    window.show()
    if splash is not None:
        splash.finish(window)
    # Self-test mode: construct the window, pump the event loop briefly, then exit.
    # Used to verify a packaged (frozen) build launches without import/path errors.
    if os.environ.get("BULKSEQ_SELFTEST") == "1":
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
