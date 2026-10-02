from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD_MODE = "BULKSEQ_GUI_TEARDOWN_CHILD"


@pytest.mark.skipif(CHILD_MODE not in os.environ, reason="subprocess helper")
def test_child_delayed_startup_worker(tmp_path):
    import time

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    import app.ui.main_window as main_window

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    main_window.wsl_recommended_workdir = lambda: time.sleep(5)
    main_window.shutil.which = lambda name, *args, **kwargs: "wsl" if name == "wsl" else None
    if os.environ[CHILD_MODE] in {"timeout", "hard-timeout"}:
        import conftest

        conftest._gui_thread_wait_ms = lambda: 1
        if os.environ[CHILD_MODE] == "hard-timeout":
            conftest.WSL_PROBE_MAX_MS = 1
    app = QApplication.instance() or QApplication([])
    globals()["_app_owner"] = app
    globals()["_main_thread_owner"] = app.thread()
    window = main_window.MainWindow()
    window.show()
    app.processEvents()


def _run_child(mode: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "BULKSEQ_SKIP_READINESS_DIALOG": "1",
           CHILD_MODE: mode}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider",
         "tests/test_gui_teardown_lifecycle.py::test_child_delayed_startup_worker"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, check=False,
    )


def test_delayed_startup_worker_finishes_before_widget_deletion():
    done = _run_child("normal")
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert "1 passed" in done.stdout, done.stdout
    assert "QThread: Destroyed while thread" not in done.stderr, done.stderr


def test_inventory_excludes_retained_application_thread():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore
    from PySide6.QtWidgets import QApplication
    from shiboken6 import getCppPointer

    import conftest

    app = QApplication.instance() or QApplication([])
    app_thread = app.thread()
    current_thread = QtCore.QThread.currentThread()
    assert app_thread.isRunning()
    assert getCppPointer(app_thread) == getCppPointer(current_thread)
    assert all(getCppPointer(thread) != getCppPointer(app_thread)
               for thread in conftest._running_gui_threads(QtCore))


def test_teardown_timeout_reports_failure_without_destroying_running_thread():
    done = _run_child("timeout")
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert "GUI teardown: running QThread exceeded" in done.stdout, done.stdout
    assert "QThread: Destroyed while thread" not in done.stderr, done.stderr


def test_teardown_hard_timeout_exits_nonzero_with_diagnostic():
    done = _run_child("hard-timeout")
    assert done.returncode != 0, (done.returncode, done.stdout, done.stderr)
    assert "GUI teardown: running QThread exceeded cleanup bound" in done.stderr, done.stderr
    assert "QThread: Destroyed while thread" not in done.stderr, done.stderr
