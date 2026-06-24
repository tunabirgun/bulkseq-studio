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

    window._create_benchmark_project("pasilla_paired_subset")
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
    # Report generation now runs off the UI thread (it probes WSL tool versions);
    # wait for the worker so the files are on disk before asserting.
    worker = getattr(window, "_reports_worker", None)
    if worker is not None:
        worker.wait(60000)
    QApplication.processEvents()
    assert (window.project_root / "results" / "reports" / "run_summary.txt").exists()
    assert (window.project_root / "results" / "reports" / "timing_summary.txt").exists()

    window.close()


def test_config_round_trip_through_widgets() -> None:
    _app()
    window = MainWindow()
    workdir = Path("manual_test_gui") / uuid4().hex
    window.workdir.setText(str(workdir))
    window.project_name.setText("rt")
    window._create_benchmark_project("pasilla_paired_subset")
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
    window.organellar.setCurrentIndex(window.organellar.findData("separate"))
    window._save_workflow_settings()
    reloaded = window.manager.load_config(root)
    assert reloaded.deseq2.design_formula == "~ batch + condition"
    assert abs(reloaded.deseq2.alpha - 0.1) < 1e-9
    assert reloaded.workflow.organellar_genes == "separate"
    # Reload into the widgets: the organellar combo must reflect the saved value.
    window._load_project(root)
    assert window.organellar.currentData() == "separate"

    # The Run-Monitor provenance exports stay disabled until a run writes the files.
    assert not window.export_toolsref_button.isEnabled()
    assert not window.export_design_button.isEnabled()
    reports = root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "tools_references.txt").write_text("x", encoding="utf-8")
    (reports / "study_design.txt").write_text("x", encoding="utf-8")
    window._refresh_export_buttons()
    assert window.export_toolsref_button.isEnabled()
    assert window.export_design_button.isEnabled()
    window.close()


def test_approve_review_resets_on_project_switch() -> None:
    # The run-approval gate is per project; a stale tick must not bleed across
    # opens or an unreviewed run could start.
    _app()
    window = MainWindow()
    base = Path("manual_test_gui") / uuid4().hex
    window.workdir.setText(str(base / "a"))
    window.project_name.setText("proj_a")
    window._create_benchmark_project("pasilla_paired_subset")
    window.approve_review.setChecked(True)
    window.workdir.setText(str(base / "b"))
    window.project_name.setText("proj_b")
    window._create_benchmark_project("pasilla_paired_subset")
    assert window.approve_review.isChecked() is False
    window.close()


def test_enrichment_without_organism_flags_review() -> None:
    # Enrichment enabled with no organism id (the count-matrix trap) must surface
    # as REVIEW_REQUIRED so the run gate forces the user to acknowledge it.
    import json

    _app()
    window = MainWindow()
    window.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    window.project_name.setText("trap")
    window._create_benchmark_project("pasilla_paired_subset")
    window.config.workflow.enrichment = True
    window.config.enrichment.kegg_organism = None
    window.config.enrichment.orgdb = None
    window.config.enrichment.gprofiler_organism = None
    window._run_sanity_checks()
    payload = json.loads(
        (window.project_root / "checks" / "01_input_validation.json").read_text(encoding="utf-8"))
    assert payload["status"] == "REVIEW_REQUIRED"
    window.close()
