from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from app.constants import APP_NAME, APP_VERSION
from app.core.paths import app_root
from app.ui.main_window import MainWindow
from app.ui.theme import PALETTES, apply_theme


def error_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME / "logs" / "error.log"


def _write_error_log(text: str) -> Path | None:
    # Try the per-user app log dir; fall back to the temp dir if it is unwritable
    # (restricted/frozen installs), so the trace is never lost silently.
    stamp = f"\n===== {datetime.now().isoformat(timespec='seconds')} =====\n{text}\n"
    for path in (error_log_path(), Path(tempfile.gettempdir()) / f"{APP_NAME} error.log"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(stamp)
            return path
        except Exception:
            continue
    return None


def _install_excepthook() -> None:
    # Convert an otherwise-silent crash into a logged, visible error and keep the
    # app alive. Unhandled exceptions in Qt slots are routed here by PySide6.
    def handler(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        written = _write_error_log(text)
        try:
            box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME,
                              f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc}\n\n"
                              "The app will keep running. You can send the error log to the developer.")
            box.addButton(QMessageBox.StandardButton.Ok)
            if written is not None:
                open_btn = box.addButton("Open log folder", QMessageBox.ButtonRole.ActionRole)
                box.exec()
                if box.clickedButton() is open_btn:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(written.parent)))
            else:
                box.exec()
        except Exception:
            pass

    sys.excepthook = handler


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


def _ppi_self_test(app, window) -> None:
    # Verify the bundled QtWebEngine/Chromium actually launches and renders in a
    # (frozen) build: load the PPI viewer, inject a tiny graph, read back the
    # cytoscape version + node count, print a PASS/FAIL line, then quit. This is
    # the only check that exercises QtWebEngineProcess + the vendored JS path.
    from PySide6.QtCore import QTimer

    from app.ui.ppi_viewer import WEBENGINE_AVAILABLE, PpiViewer

    import json as _json

    result = {"webengine": WEBENGINE_AVAILABLE, "version": None, "nodes": None}

    def _report(ok: bool, code: int) -> None:
        # A windowed (frozen) app has no stdout, so also write a sentinel file and
        # set the process exit code so a launcher can read PASS/FAIL.
        result["pass"] = ok
        line = f"PPI_SELFTEST result={result} {'PASS' if ok else 'FAIL'}"
        print(line, flush=True)
        out = os.environ.get("BULKSEQ_SELFTEST_OUT") or str(
            Path(tempfile.gettempdir()) / "bulkseq_ppi_selftest.json")
        try:
            Path(out).write_text(_json.dumps(result), encoding="utf-8")
        except Exception:
            pass
        # Defer the exit so it is delivered even when _report runs from the early
        # guard path (before app.exec() has started); a bare app.exit() there is lost.
        QTimer.singleShot(0, lambda: app.exit(code))

    def finish() -> None:
        ok = bool(result["version"]) and result["nodes"] == 3
        _report(ok, 0 if ok else 3)

    if not WEBENGINE_AVAILABLE:
        QTimer.singleShot(300, lambda: _report(False, 3))
        return

    try:
        viewer = PpiViewer()
    except Exception as exc:
        print(f"PPI_SELFTEST construction error: {exc}", flush=True)
        _report(False, 3)
        return
    # QtWebEngine imported but the viewer has no live web view (e.g. viewer.html not
    # bundled): fail cleanly with a sentinel + exit code instead of dereferencing
    # viewer.view (None) and hanging on the excepthook's modal dialog.
    if not viewer.available:
        _report(False, 3)
        return
    app._selftest_viewer = viewer  # keep a reference alive across the event loop
    viewer.resize(480, 360)
    viewer.show()

    def on_loaded(loaded: bool) -> None:
        viewer.load_graph({
            "nodes": [
                {"data": {"id": "A", "symbol": "A", "degree": 2, "log2FoldChange": 1.5}},
                {"data": {"id": "B", "symbol": "B", "degree": 1, "log2FoldChange": -2.0}},
                {"data": {"id": "C", "symbol": "C", "degree": 1}},
            ],
            "edges": [
                {"data": {"source": "A", "target": "B", "weight": 0.9}},
                {"data": {"source": "A", "target": "C", "weight": 0.5}},
            ],
        })

        def got_stats(s: str) -> None:
            try:
                import json as _json

                result["nodes"] = _json.loads(s).get("nodes")
            except Exception:
                pass
            viewer.probe_version(lambda v: (result.__setitem__("version", v), finish()))

        QTimer.singleShot(900, lambda: viewer.stats(got_stats))

    viewer.view.loadFinished.connect(on_loaded)
    QTimer.singleShot(20000, lambda: _report(False, 4))  # hard timeout: engine hung


def main() -> int:
    # QtWebEngine reads these at engine init (QApplication construction), so they
    # must be set first. Conservative flags that rendered reliably under flaky GPU
    # drivers and in the frozen sandbox; software GL is the safe default.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox --in-process-gpu")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    app = QApplication(sys.argv)
    _install_excepthook()  # log + surface unhandled errors instead of crashing
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
    # Self-test mode: construct the window, then verify the bundled QtWebEngine
    # actually renders the PPI viewer before exiting. Used to confirm a packaged
    # (frozen) build launches without import/path errors and ships a working
    # Chromium helper.
    if os.environ.get("BULKSEQ_SELFTEST") == "1":
        _ppi_self_test(app, window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
