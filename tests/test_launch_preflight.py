from __future__ import annotations

import os
from pathlib import Path
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import yaml

from app.core.config_models import default_config
from app.core.preflight import PreflightFingerprintValidation
from app.ui.main_window import MainWindow
import app.ui.main_window as main_window_module


def _window_with_project(tmp_path: Path) -> MainWindow:
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "inputs").mkdir()
    (root / "inputs" / "counts.tsv").write_text(
        "gene_id\tS1\nGENE1\t10\n", encoding="utf-8",
    )
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\nS1\tcontrol\n", encoding="utf-8",
    )
    config = default_config("launch-preflight", root)
    config.input.type = "count_matrix"
    config.input.count_matrix = "inputs/counts.tsv"
    (root / "config" / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    window = MainWindow()
    window.project_root = root
    window.config = config
    window._refresh_export_buttons()
    window.show()
    app.processEvents()
    return window


def test_protected_launch_routes_to_background_preflight_before_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window_with_project(tmp_path)
    requested: list[str] = []
    try:
        monkeypatch.setattr(window.manager, "sync_workflow_if_outdated", lambda root: None)
        monkeypatch.setattr(main_window_module, "save_metadata", lambda *args, **kwargs: None)
        monkeypatch.setattr(window, "_save_workflow_settings", lambda *, validate: True)
        monkeypatch.setattr(window, "_save_resources", lambda: None)
        monkeypatch.setattr(window, "_apply_figure_style", lambda: None)
        monkeypatch.setattr(window, "_begin_launch_preflight", requested.append)
        monkeypatch.setattr(
            window,
            "_run_gate_ok",
            lambda **kwargs: pytest.fail("the synchronous launch gate was reached"),
        )

        window._start_snakemake_impl("run")

        assert requested == ["run"]
        assert window.runner is None
    finally:
        window.close()


def test_launch_fingerprint_keeps_event_loop_responsive_and_continues_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window_with_project(tmp_path)
    started = threading.Event()
    release = threading.Event()
    outcome = PreflightFingerprintValidation(True, "current", "a", "a")
    continued: list[tuple[str, object, Path | None]] = []

    def slow_validate(root, *, cancel_requested=None):
        started.set()
        while not release.wait(0.01):
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("cancelled")
        return outcome

    def record_continue(mode, *, _validated_preflight=None, _validated_root=None):
        continued.append((mode, _validated_preflight, _validated_root))

    try:
        monkeypatch.setattr(main_window_module, "validate_current_preflight", slow_validate)
        monkeypatch.setattr(window, "_start_snakemake_impl", record_continue)

        window._begin_launch_preflight("run")
        assert started.wait(2)
        assert window._launch_preflight_worker is not None
        assert window._launch_preflight_worker.isRunning()
        assert window.progress.minimum() == 0
        assert window.progress.maximum() == 0
        assert all(not button.isEnabled() for button in window.run_action_buttons.values())
        run_index = window.tabs.indexOf(window.run_monitor_page)
        assert window.tabs.widget(run_index).isEnabled()
        assert all(
            not window.tabs.widget(index).isEnabled()
            for index in range(window.tabs.count())
            if index != run_index
        )
        assert window.theme_toggle.isEnabled()
        window._begin_launch_preflight("run")

        ui_tick = {"ran": False}
        QTimer.singleShot(0, lambda: ui_tick.__setitem__("ran", True))
        QApplication.processEvents()
        assert ui_tick["ran"] is True

        release.set()
        worker = window._launch_preflight_worker
        assert worker is not None and worker.wait(3000)
        QApplication.processEvents()

        assert continued == [("run", outcome, window.project_root)]
        assert window._launch_preflight_worker is None
        assert window.progress.maximum() == 100
        assert all(window.tabs.widget(index).isEnabled() for index in range(window.tabs.count()))
    finally:
        release.set()
        worker = window._launch_preflight_worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        window.close()


def test_invalid_background_preflight_blocks_command_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window_with_project(tmp_path)
    outcome = PreflightFingerprintValidation(False, "changed", "old", "new")
    prompts: list[bool] = []
    try:
        monkeypatch.setattr(
            main_window_module,
            "validate_current_preflight",
            lambda root, *, cancel_requested=None: outcome,
        )
        monkeypatch.setattr(
            main_window_module,
            "build_snakemake_command",
            lambda *args, **kwargs: pytest.fail("command built before a valid gate"),
        )
        monkeypatch.setattr(
            window,
            "_prompt_for_pre_run_validation",
            lambda: prompts.append(True) or True,
        )

        window._begin_launch_preflight("run")
        worker = window._launch_preflight_worker
        assert worker is not None and worker.wait(3000)
        QApplication.processEvents()

        assert window.runner is None
        assert window.tabs.currentIndex() == 7
        assert prompts == [True]
        assert window.sanity_run_button.hasFocus()
    finally:
        window.close()


def test_closing_cancels_launch_preflight_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window_with_project(tmp_path)
    started = threading.Event()

    def wait_for_cancel(root, *, cancel_requested=None):
        started.set()
        while cancel_requested is None or not cancel_requested():
            threading.Event().wait(0.01)
        raise RuntimeError("cancelled")

    monkeypatch.setattr(main_window_module, "validate_current_preflight", wait_for_cancel)
    window._begin_launch_preflight("run")
    assert started.wait(2)
    worker = window._launch_preflight_worker
    assert worker is not None and worker.isRunning()

    window.close()

    assert not worker.isRunning()


def test_settings_change_while_launch_check_runs_requires_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window_with_project(tmp_path)
    started = threading.Event()
    release = threading.Event()
    outcome = PreflightFingerprintValidation(True, "current", "a", "a")
    continued: list[str] = []
    prompts: list[bool] = []

    def slow_validate(root, *, cancel_requested=None):
        started.set()
        release.wait(2)
        return outcome

    try:
        monkeypatch.setattr(main_window_module, "validate_current_preflight", slow_validate)
        monkeypatch.setattr(
            window,
            "_start_snakemake_impl",
            lambda mode, **kwargs: continued.append(mode),
        )
        monkeypatch.setattr(
            window,
            "_prompt_for_pre_run_validation",
            lambda: prompts.append(True) or True,
        )

        window._begin_launch_preflight("resume")
        assert started.wait(2)
        assert window.config is not None
        window.config.project.name = "changed-during-launch-validation"
        release.set()
        worker = window._launch_preflight_worker
        assert worker is not None and worker.wait(3000)
        QApplication.processEvents()

        assert continued == []
        assert prompts == [True]
        assert window.tabs.currentIndex() == 7
        assert window.sanity_run_button.hasFocus()
        assert "out of date" in window.sanity_state_label.text().lower()
    finally:
        release.set()
        worker = window._launch_preflight_worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        window.close()
