from __future__ import annotations

import os
import statistics
import time
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel

from app.ui import main_window as main_window_module
from app.ui import readiness_dialog
from app.ui.main_window import MainWindow
from app.ui.theme import (
    PALETTES,
    _STATIC_QSS_PROPERTY,
    _THEME_MODE_PROPERTY,
    apply_theme,
    status_color,
)


def _isolated_window(monkeypatch) -> tuple[QApplication, MainWindow, tuple[str, str]]:
    app = QApplication.instance() or QApplication([])
    previous = (app.organizationName(), app.applicationName())
    app.setOrganizationName("BulkSeqStudioThemeTest")
    app.setApplicationName(uuid4().hex)
    QSettings().clear()
    QSettings().setValue("theme_mode", "light")
    app.setProperty(_STATIC_QSS_PROPERTY, False)
    app.setProperty(_THEME_MODE_PROPERTY, None)
    app.setStyleSheet("")
    apply_theme(app, "light")
    monkeypatch.setattr(readiness_dialog.ReadinessDialog, "refresh", lambda self: None)
    window = MainWindow()
    window.resize(1093, 614)
    window.show()
    app.processEvents()
    return app, window, previous


def _dispose_window(app: QApplication, window: MainWindow) -> None:
    """Release WebEngine-backed widgets so timing tests do not inherit old windows."""
    if window.readiness_dialog is not None:
        window.readiness_dialog.close()
        window.readiness_dialog.deleteLater()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_actual_theme_button_repaints_dialog_progress_viewers_and_restores(monkeypatch) -> None:
    app, window, previous = _isolated_window(monkeypatch)
    try:
        window._set_progress_status("FAIL")
        window.show_readiness_dialog()
        app.processEvents()
        dialog = window.readiness_dialog
        assert dialog is not None and dialog.isVisible()
        empty_label = window.ppi_viewer.findChild(QLabel, "ppiEmptyState")
        assert empty_label is not None

        def snapshot() -> tuple[str, str, str, str, str]:
            app.processEvents()
            return (
                app.palette().color(QPalette.ColorRole.Window).name(),
                window.grab().toImage().pixelColor(5, 100).name(),
                dialog.styleSheet(),
                window.progress.styleSheet(),
                empty_label.styleSheet(),
            )

        light = snapshot()
        window.theme_toggle.click()
        app.processEvents()
        dark = snapshot()
        assert QSettings().value("theme_mode") == "dark"
        assert dark[0] == PALETTES["dark"]["BACKGROUND"].lower()
        assert dark[0] != light[0] and dark[1] != light[1]
        assert PALETTES["dark"]["BACKGROUND"] in dark[2]
        assert status_color("FAIL", "dark") in dark[3]
        assert PALETTES["dark"]["MUTED_TEXT"].lower() in dark[4].lower()

        window.theme_toggle.click()
        app.processEvents()
        assert QSettings().value("theme_mode") == "light"
        assert snapshot() == light
    finally:
        _dispose_window(app, window)
        QSettings().clear()
        app.setOrganizationName(previous[0])
        app.setApplicationName(previous[1])


def _theme_toggle_durations_ms(app: QApplication, window: MainWindow, count: int = 12) -> list[float]:
    durations: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        window.theme_toggle.click()
        app.processEvents()
        durations.append((time.perf_counter() - started) * 1000)
    return durations


def _assert_warm_toggles_stay_on_the_fast_path(durations: list[float]) -> None:
    warm = sorted(durations[2:])
    p95 = warm[max(0, int(len(warm) * 0.95) - 1)]
    assert statistics.median(warm) <= 150.0
    assert p95 <= 250.0
    # The first two toggles repolish a freshly shown window and are measurably slower (752 ms observed on a shared CI runner); only the warm toggles are held to the ceilings.
    assert max(warm) <= 500.0


def test_warm_theme_toggle_stays_on_the_fast_path(monkeypatch) -> None:
    app, window, previous = _isolated_window(monkeypatch)
    try:
        window.tabs.setCurrentIndex(10)  # Outputs: graphics scene + nested inspectors.
        app.processEvents()
        durations = _theme_toggle_durations_ms(app, window)
        _assert_warm_toggles_stay_on_the_fast_path(durations)
    finally:
        _dispose_window(app, window)
        QSettings().clear()
        app.setOrganizationName(previous[0])
        app.setApplicationName(previous[1])


def test_forced_slow_toggle_path_fails_the_fast_path_gate(monkeypatch) -> None:
    """Negative control: force every toggle onto the cold/QSS-reparsing branch
    (the fast-path property theme.apply_theme checks) and confirm the timing
    gate above actually catches it, rather than passing unconditionally."""
    app, window, previous = _isolated_window(monkeypatch)
    try:
        window.tabs.setCurrentIndex(10)
        app.processEvents()
        real_apply_theme = main_window_module.apply_theme

        def _always_cold_apply_theme(app_, mode):
            app_.setProperty(_STATIC_QSS_PROPERTY, False)
            real_apply_theme(app_, mode)

        monkeypatch.setattr(main_window_module, "apply_theme", _always_cold_apply_theme)
        durations = _theme_toggle_durations_ms(app, window)
        with pytest.raises(AssertionError):
            _assert_warm_toggles_stay_on_the_fast_path(durations)
    finally:
        _dispose_window(app, window)
        QSettings().clear()
        app.setOrganizationName(previous[0])
        app.setApplicationName(previous[1])
