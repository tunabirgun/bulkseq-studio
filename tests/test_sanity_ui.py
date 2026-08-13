from __future__ import annotations

import os
from pathlib import Path
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from app.core.config_models import default_config
from app.core.preflight import PreflightFingerprintValidation, write_input_validation_with_fingerprint
from app.ui.main_window import MainWindow
import app.ui.main_window as main_window_module


def _window() -> MainWindow:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    return window


def _configure_current_project(window: MainWindow, root: Path, status: str) -> None:
    """Attach a minimal result-only project and record the exact state validated.

    Gate tests must exercise the real fingerprint contract.  Monkeypatching the
    status reader would recreate the unsafe legacy behaviour these tests exist
    to prevent.
    """
    import yaml

    config = default_config("gate-test", root)
    config.input.type = "count_matrix"
    config.input.count_matrix = "inputs/counts.tsv"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "counts.tsv").write_text(
        "gene_id\tS1\tS2\nGENE1\t10\t12\n", encoding="utf-8")
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\nS1\tcontrol\nS2\ttreated\n", encoding="utf-8")
    (root / "config" / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    window.project_root = root
    window.config = config
    write_input_validation_with_fingerprint(
        root,
        {
            "check": "01_input_validation",
            "status": status,
            "messages": ["Synthetic gate finding"] if status != "PASS" else [],
        },
    )


def test_no_project_and_no_check_states_do_not_offer_orphaned_approval(tmp_path: Path) -> None:
    window = _window()
    try:
        window._update_sanity_state({})
        assert not window.sanity_run_button.isEnabled()
        assert not window.sanity_refresh_button.isEnabled()
        assert not window.sanity_go_project.isHidden()
        assert window.approve_review.isHidden()
        assert "Open or create a project" in window.sanity_state_label.text()

        window.project_root = tmp_path
        window._update_sanity_state({})
        assert window.sanity_run_button.isEnabled()
        assert not window.sanity_refresh_button.isEnabled()
        assert window.approve_review.isHidden()
        assert "No checks yet" in window.sanity_state_label.text()
    finally:
        window.close()


@pytest.mark.parametrize("status", ["PASS", "WARNING"])
def test_pass_and_warning_states_never_request_approval(tmp_path: Path, status: str) -> None:
    window = _window()
    try:
        window.project_root = tmp_path
        window._update_sanity_state({"01_input_validation": status})
        assert window.sanity_refresh_button.isEnabled()
        assert not window.sanity_text.isHidden()
        assert window.approve_review.isHidden()
        assert status in window.sanity_text.toPlainText()
    finally:
        window.close()


def test_stale_validation_prompt_has_direct_primary_route_and_cancel() -> None:
    window = _window()
    try:
        observed: dict[str, object] = {}

        def choose(button_name: str) -> None:
            dialog = next(
                widget for widget in QApplication.topLevelWidgets()
                if widget.objectName() == "preRunValidationDialog"
            )
            observed["title"] = dialog.accessibleName()
            open_button = dialog.findChild(QPushButton, "preRunValidationOpenButton")
            cancel_button = dialog.findChild(QPushButton, "preRunValidationCancelButton")
            assert open_button is not None and open_button.isDefault()
            assert open_button.property("primary") is True
            assert cancel_button is not None
            button = dialog.findChild(QPushButton, button_name)
            assert button is not None
            button.click()

        QTimer.singleShot(0, lambda: choose("preRunValidationCancelButton"))
        assert window._prompt_for_pre_run_validation() is False
        QTimer.singleShot(0, lambda: choose("preRunValidationOpenButton"))
        assert window._prompt_for_pre_run_validation() is True
        assert observed["title"] == "Pre-run checks required"
    finally:
        window.close()


def test_review_approval_is_required_and_resets_when_findings_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    try:
        _configure_current_project(window, tmp_path, "REVIEW_REQUIRED")
        review = {"01_input_validation": "REVIEW_REQUIRED"}
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
        window._update_sanity_state(review)
        assert not window.approve_review.isHidden()
        assert "1 phase check" in window.approve_review.text()
        assert window._run_gate_ok() is False

        window.approve_review.setChecked(True)
        assert window._run_gate_ok() is True
        window._update_sanity_state({"01_input_validation": "WARNING"})
        assert not window.approve_review.isChecked()
        assert window.approve_review.isHidden()
    finally:
        window.close()


def test_fail_state_blocks_run_monitor_route_and_run_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    try:
        _configure_current_project(window, tmp_path, "FAIL")
        failed = {"01_input_validation": "FAIL"}
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
        window._update_sanity_state(failed)
        assert not window.sanity_go_run.isEnabled()
        assert window.approve_review.isHidden()
        assert window._run_gate_ok() is False
    finally:
        window.close()


@pytest.mark.parametrize("status", ["CORRUPT_STATUS", "", 42, None, {}, []])
def test_unknown_or_non_string_input_check_status_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: object,
) -> None:
    window = _window()
    try:
        _configure_current_project(window, tmp_path, "PASS")
        check_path = tmp_path / "checks" / "01_input_validation.json"
        import json

        payload = json.loads(check_path.read_text(encoding="utf-8"))
        payload["status"] = status
        check_path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)

        assert window._run_gate_ok() is False
        assert window.tabs.currentIndex() == 7
    finally:
        window.close()


def test_edit_after_validation_invalidates_run_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    try:
        _configure_current_project(window, tmp_path, "PASS")
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
        prompt_calls: list[bool] = []
        monkeypatch.setattr(
            window,
            "_prompt_for_pre_run_validation",
            lambda: prompt_calls.append(True) or True,
        )
        assert window._run_gate_ok() is True

        samples = tmp_path / "config" / "samples.tsv"
        samples.write_text(
            "sample_id\tcondition\nS1\tcontrol\nS2\ttreated\nS3\ttreated\n",
            encoding="utf-8",
        )

        assert window._run_gate_ok() is False
        QApplication.processEvents()
        assert prompt_calls == [True]
        assert window.tabs.currentIndex() == 7
        assert window.sanity_run_button.hasFocus()
        assert "out of date" in window.sanity_state_label.text().lower()
    finally:
        window.close()


def test_gui_loads_and_saves_the_configured_sample_sheet(tmp_path: Path) -> None:
    import yaml

    root = tmp_path / "custom-sheet-project"
    (root / "config").mkdir(parents=True)
    (root / "metadata").mkdir()
    config = default_config("custom-sheet", root)
    config.input.samples = "metadata/study.tsv"
    (root / "config" / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8",
    )
    custom = root / "metadata" / "study.tsv"
    custom.write_text(
        "sample_id\tcondition\tlayout\tfastq_1\nCUSTOM\tcontrol\tsingle\treads.fastq.gz\n",
        encoding="utf-8",
    )
    decoy = root / "config" / "samples.tsv"
    decoy.write_text(
        "sample_id\tcondition\tlayout\tfastq_1\nDECOY\tcontrol\tsingle\tdecoy.fastq.gz\n",
        encoding="utf-8",
    )
    window = _window()
    try:
        window._load_project(root)
        assert window.metadata_table.to_dataframe()["sample_id"].tolist() == ["CUSTOM"]
        window.metadata_table.item(0, 0).setText("CUSTOM_EDITED")
        window._save_metadata()
        assert "CUSTOM_EDITED" in custom.read_text(encoding="utf-8")
        assert "DECOY" in decoy.read_text(encoding="utf-8")
    finally:
        window.close()


def test_full_input_fingerprint_runs_without_blocking_the_gui_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    started = threading.Event()
    release = threading.Event()

    def slow_fingerprint(root, payload, *, cancel_requested=None):
        started.set()
        while not release.wait(0.01):
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("cancelled")
        return root / "checks" / "01_input_validation.json"

    try:
        _configure_current_project(window, tmp_path, "PASS")
        monkeypatch.setattr(
            main_window_module,
            "write_input_validation_with_fingerprint",
            slow_fingerprint,
        )
        window._run_sanity_checks()
        assert started.wait(2)
        assert window._sanity_worker.isRunning()

        ui_tick = {"ran": False}
        QTimer.singleShot(0, lambda: ui_tick.__setitem__("ran", True))
        QApplication.processEvents()
        assert ui_tick["ran"] is True
        assert not window.sanity_busy.isHidden()

        release.set()
        assert window._sanity_worker.wait(3000)
        QApplication.processEvents()
        assert window.sanity_busy.isHidden()
    finally:
        release.set()
        worker = getattr(window, "_sanity_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        window.close()


def test_invalid_workflow_settings_restore_validation_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    try:
        _configure_current_project(window, tmp_path, "PASS")
        monkeypatch.setattr(window, "_save_workflow_settings", lambda *, validate: False)

        window._run_sanity_checks()

        assert window.sanity_busy.isHidden()
        assert window.sanity_run_button.isEnabled()
        assert getattr(window, "_sanity_worker", None) is None
    finally:
        window.close()


def test_unreadable_fingerprinted_input_cannot_render_validation_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.preflight as preflight_module

    window = _window()
    real_stream = preflight_module._stream_sha256

    def deny_count_table(path: Path) -> str:
        if path.name == "counts.tsv":
            raise PermissionError("synthetic unreadable scientific input")
        return real_stream(path)

    try:
        _configure_current_project(window, tmp_path, "PASS")
        monkeypatch.setattr(preflight_module, "_stream_sha256", deny_count_table)

        window._run_sanity_checks()
        worker = window._sanity_worker
        assert worker.wait(5000)
        QApplication.processEvents()

        assert "passed" not in window.sanity_state_label.text().lower()
        assert "out of date" in window.sanity_state_label.text().lower()
        assert not window.sanity_go_run.isEnabled()
        assert "unreadable" in window.sanity_text.toPlainText().lower()
    finally:
        worker = getattr(window, "_sanity_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        window.close()


def test_saved_phase_refresh_fingerprints_without_blocking_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window()
    started = threading.Event()
    release = threading.Event()
    outcome = PreflightFingerprintValidation(True, "current", "a", "a")

    def slow_validate(root, *, cancel_requested=None):
        started.set()
        while not release.wait(0.01):
            if cancel_requested is not None and cancel_requested():
                raise RuntimeError("cancelled")
        return outcome

    try:
        _configure_current_project(window, tmp_path, "PASS")
        monkeypatch.setattr(main_window_module, "validate_current_preflight", slow_validate)

        window._refresh_phase_checks()
        assert started.wait(2)
        assert "out of date" in window.sanity_state_label.text().lower()

        ui_tick = {"ran": False}
        QTimer.singleShot(0, lambda: ui_tick.__setitem__("ran", True))
        QApplication.processEvents()
        assert ui_tick["ran"] is True

        release.set()
        worker = window._phase_refresh_worker
        assert worker is not None and worker.wait(3000)
        QApplication.processEvents()
        assert "passed" in window.sanity_state_label.text().lower()
        assert window._phase_refresh_worker is None
    finally:
        release.set()
        worker = window._phase_refresh_worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        window.close()
