from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import MainWindow  # noqa: E402


def _app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def test_main_window_benchmark_smoke() -> None:
    _app()
    window = MainWindow()
    workdir = Path("manual_test_gui") / uuid4().hex
    window.workdir.setText(str(workdir))
    window.project_name.setText("pasilla_gui")

    window._create_benchmark_project()
    assert window.project_root == workdir.resolve() / "pasilla_gui"
    assert window.metadata_table.rowCount() == 4
    assert "Created benchmark project" in window.project_status.toPlainText()

    window._validate_metadata()
    assert "FAIL" not in window.metadata_messages.toPlainText()

    window._run_sanity_checks()
    assert (window.project_root / "checks" / "01_input_validation.json").exists()

    window._estimate_runtime()
    assert "range:" in window.runtime_text.toPlainText()

    window._generate_reports()
    assert (window.project_root / "results" / "reports" / "run_summary.txt").exists()
    assert (window.project_root / "results" / "reports" / "timing_summary.txt").exists()

    window.close()


def test_config_round_trip_through_widgets() -> None:
    _app()
    window = MainWindow()
    workdir = Path("manual_test_gui") / uuid4().hex
    window.workdir.setText(str(workdir))
    window.project_name.setText("rt")
    window._create_benchmark_project()
    root = window.project_root
    assert root is not None

    # Loaded widgets must reflect the on-disk config (the round-trip bug).
    window._load_project(root)
    assert window.aligner.currentText() == "STAR"
    assert window.design.text() == window.config.deseq2.design_formula

    # Change a setting, save, reload from disk, assert it persisted (not overwritten
    # by widget defaults).
    window.design.setText("~ batch + condition")
    window.alpha.setValue(0.1)
    window._save_workflow_settings()
    reloaded = window.manager.load_config(root)
    assert reloaded.deseq2.design_formula == "~ batch + condition"
    assert abs(reloaded.deseq2.alpha - 0.1) < 1e-9
    window.close()
