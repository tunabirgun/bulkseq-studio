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
