from __future__ import annotations

import gc
import os
import signal
import sys

import pytest


WSL_PROBE_MAX_MS = 2 * 15_000  # Two sequential subprocess timeouts in app/core/paths.py.


def _gui_thread_wait_ms() -> int:
    return WSL_PROBE_MAX_MS


def _running_gui_threads(qt_core) -> list:
    from shiboken6 import getCppPointer

    current_thread = getCppPointer(qt_core.QThread.currentThread())
    threads = []
    for obj in gc.get_objects():
        if isinstance(obj, qt_core.QThread):
            try:
                if getCppPointer(obj) != current_thread and obj.isRunning():
                    threads.append(obj)
            except RuntimeError:
                pass
    return threads


def _abort_gui_teardown(message: str) -> None:
    os.write(2, f"GUI teardown: {message}\n".encode("utf-8"))
    os.kill(os.getpid(), signal.SIGTERM)
    raise RuntimeError(message)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run every test in its own temporary working directory.

    Several tests create scratch projects via relative paths (``manual_test_*``).
    Without isolation those land in the repository tree and accumulate as litter.
    Application code resolves its own paths from ``__file__`` (see app.core.paths),
    so changing the working directory does not affect it.
    """
    monkeypatch.chdir(tmp_path)

    # Keep ordinary in-process GUI tests on the static PPI surface. Constructing
    # and destroying a Chromium-backed QWebEngineView for every MainWindow made
    # compact Windows test runs timing-sensitive and could fast-fail QtCore while
    # the first window was being torn down. The dedicated PPI integration test
    # launches its own Python process and still exercises the real WebEngine,
    # keyboard route, vendored page, and a clean process exit end to end.
    if "app.ui.main_window" in sys.modules or "app.ui.ppi_viewer" in sys.modules:
        from app.ui import ppi_viewer

        monkeypatch.setattr(ppi_viewer, "WEBENGINE_AVAILABLE", False)
    yield

    # GUI tests share one QApplication. Merely closing a PySide window can leave
    # its C++ widget tree pending deletion through Python signal cycles; hundreds
    # of those stale trees made later stylesheet/layout tests progressively slower
    # and pushed the complete suite past five minutes. Keep non-GUI tests free of
    # a Qt import, but deterministically drain widgets once Qt is already loaded.
    qt_widgets = sys.modules.get("PySide6.QtWidgets")
    if qt_widgets is None:
        return
    app = qt_widgets.QApplication.instance()
    if app is None:
        return
    widgets = list(app.topLevelWidgets())
    if not widgets:
        return
    for widget in widgets:
        try:
            widget.close()
        except RuntimeError:
            continue
    app.processEvents()
    qt_core = sys.modules.get("PySide6.QtCore")
    if qt_core is not None:
        # QWebEngine can still be delivering a queued load callback immediately
        # after close. Let one real event-loop turn complete before scheduling C++
        # deletion; forcing DeferredDelete synchronously was fast but sporadically
        # aborted compact `pytest -q` runs during Chromium teardown.
        loop = qt_core.QEventLoop()
        qt_core.QTimer.singleShot(15, loop.quit)
        loop.exec()
        late_threads = []
        for thread in _running_gui_threads(qt_core):
            if not thread.wait(_gui_thread_wait_ms()):
                late_threads.append(thread)
        for thread in late_threads:
            if not thread.wait(WSL_PROBE_MAX_MS):
                _abort_gui_teardown("running QThread exceeded cleanup bound: "
                                    f"{type(thread).__name__}")
        if _running_gui_threads(qt_core):
            _abort_gui_teardown("a QThread started during teardown")
        app.processEvents()
        for widget in widgets:
            try:
                widget.deleteLater()
            except RuntimeError:
                continue
        loop = qt_core.QEventLoop()
        qt_core.QTimer.singleShot(15, loop.quit)
        loop.exec()
        qt_core.QCoreApplication.sendPostedEvents(
            None,
            qt_core.QEvent.Type.DeferredDelete,
        )
        app.processEvents()
        if late_threads:
            pytest.fail("GUI teardown: running QThread exceeded the wait bound: "
                        + ", ".join(type(thread).__name__ for thread in late_threads))
