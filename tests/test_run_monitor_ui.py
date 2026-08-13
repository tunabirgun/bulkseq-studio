from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFrame, QGroupBox, QPushButton

from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    # Keep this focused UI suite independent of a machine's WSL installation.
    monkeypatch.setattr("app.ui.main_window.shutil.which", lambda _name: None)
    app = _app()
    QSettings().setValue("theme_mode", "light")
    apply_theme(app, "light")
    result = MainWindow()
    result.resize(1093, 640)
    result.show()
    result.tabs.setCurrentIndex(8)
    app.processEvents()
    yield result
    result.close()
    app.processEvents()


def _attach_project(window: MainWindow, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    window.project_root = root
    window._refresh_export_buttons()
    QApplication.processEvents()


def test_no_project_empty_state_excludes_all_operational_ui(window: MainWindow) -> None:
    assert window.run_empty_panel.isVisible()
    assert window.run_empty_body.visibleRegion().boundingRect().contains(
        window.run_empty_body.rect())
    assert not window.run_operational_panel.isVisible()
    assert not window.run_action_buttons["run"].isVisible()
    assert not window.progress.isVisible()
    assert not window.command_text.isVisible()
    assert not window.log_text.isVisible()


def test_ready_project_has_compact_sections_and_one_primary_action(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    _attach_project(window, tmp_path)

    assert not window.run_empty_panel.isVisible()
    assert window.run_operational_panel.isVisible()
    assert window.run_action_buttons["run"].isVisible()
    assert window.run_action_buttons["dry-run"].isVisible()
    assert not window.stop_button.isVisible()
    assert not window.execution_details_panel.isVisible()
    assert not window.run_options_panel.isVisible()
    assert window.open_project_folder_button.isEnabled()
    assert not window.open_results_report_button.isEnabled()
    assert not window.open_multiqc_button.isEnabled()
    results_report = tmp_path / "results" / "reports" / "results_report.html"
    multiqc_report = tmp_path / "results" / "qc" / "multiqc" / "multiqc_report.html"
    results_report.parent.mkdir(parents=True)
    multiqc_report.parent.mkdir(parents=True)
    results_report.write_text("<html>synthetic results</html>", encoding="utf-8")
    multiqc_report.write_text("<html>synthetic QC</html>", encoding="utf-8")
    window._refresh_export_buttons()
    assert window.open_results_report_button.isEnabled()
    assert window.open_multiqc_button.isEnabled()

    visible_primary = [
        button.text()
        for button in window.run_operational_panel.findChildren(QPushButton)
        if button.isVisible() and button.property("primary") is True
    ]
    assert visible_primary == ["Start Run"]
    assert window.status_label.text() == "Ready — configure the project, then start the workflow."
    assert not window.phase_label.text()

    # The old implementation used two tall, notched fieldsets. The replacement
    # has content-sized semantic sections and no QGroupBox on this page.
    assert window.run_monitor_page.findChildren(QGroupBox) == []
    sections = [
        child for child in window.run_operational_panel.findChildren(QFrame)
        if child.property("uiRole") == "section"
    ]
    assert len(sections) == 4
    assert window.findChild(QFrame, "runWorkflowSection").height() < 190
    assert window.findChild(QFrame, "runAfterSection").height() < 210
    assert window.run_monitor_page.horizontalScrollBar().maximum() == 0
    intro = window.run_monitor_page.widget().findChild(QFrame, "pageIntro")
    assert intro is not None
    assert intro.visibleRegion().boundingRect().contains(intro.rect())
    assert intro.width() <= window.run_monitor_page.viewport().width()
    assert (
        window.run_monitor_page.verticalScrollBar().maximum()
        <= window.run_monitor_page.viewport().height() * 0.15
    )


def test_disclosures_reveal_only_contextual_controls_and_details(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    _attach_project(window, tmp_path)

    window.run_options_toggle.click()
    QApplication.processEvents()
    assert window.run_options_panel.isVisible()
    assert window.use_wsl.isVisible() == os.name.startswith("nt")
    assert not window.run_action_buttons["resume"].isVisible()
    assert not window.run_action_buttons["unlock"].isVisible()

    incomplete = tmp_path / ".snakemake" / "incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "partial").write_text("1", encoding="utf-8")
    window._refresh_resume_banner()
    QApplication.processEvents()
    assert window.run_action_buttons["resume"].isVisible()
    assert not window.run_action_buttons["unlock"].isVisible()

    locks = tmp_path / ".snakemake" / "locks"
    locks.mkdir(parents=True)
    (locks / "0.input.lock").write_text("1", encoding="utf-8")
    window._refresh_resume_banner()
    QApplication.processEvents()
    assert window.run_action_buttons["resume"].isVisible()
    assert window.run_action_buttons["unlock"].isVisible()

    assert not window.execution_details_panel.isVisible()
    window.command_text.setText("snakemake --snakefile workflow/Snakefile")
    QApplication.processEvents()
    # Technical output arriving must not steal compact monitor space. It is
    # available on demand, then remains under the user's control while streaming.
    assert not window.execution_details_toggle.isChecked()
    assert not window.execution_details_panel.isVisible()
    window.execution_details_toggle.click()
    QApplication.processEvents()
    assert window.execution_details_toggle.isChecked()
    assert window.execution_details_panel.isVisible()
    assert window.command_text.property("uiRole") == "codeOutput"
    assert window.log_text.property("uiRole") == "codeOutput"

    # A user collapse remains respected while more log lines stream in.
    window.execution_details_toggle.click()
    window.log_text.append("Building DAG of jobs...")
    QApplication.processEvents()
    assert not window.execution_details_panel.isVisible()
    window.command_text.clear()
    window.log_text.clear()
    QApplication.processEvents()
    assert not window.execution_details_toggle.isChecked()


def test_action_hierarchy_order_and_running_state(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    _attach_project(window, tmp_path)

    start = window.run_action_buttons["run"]
    dry_run = window.run_action_buttons["dry-run"]
    assert start.geometry().left() < dry_run.geometry().left()
    post_run_actions = (
        window.open_results_report_button,
        window.open_multiqc_button,
        window.export_design_button,
        window.export_toolsref_button,
        window.open_project_folder_button,
    )
    assert len({button.geometry().top() for button in post_run_actions}) == 1
    assert [button.geometry().left() for button in post_run_actions] == sorted(
        button.geometry().left() for button in post_run_actions)
    assert [button.accessibleName() for button in post_run_actions] == [
        "Open results report", "Open MultiQC report", "Export study design",
        "Export tools and references", "Open project folder",
    ]
    assert window.open_project_folder_button.property("buttonRole") is None
    assert window.stop_button.property("buttonRole") == "danger"

    window._set_running_ui(True)
    QApplication.processEvents()
    assert window.stop_button.isVisible() and window.stop_button.isEnabled()
    assert not start.isEnabled() and not dry_run.isEnabled()
    assert not window.use_wsl.isEnabled()

    window._set_running_ui(False)
    QApplication.processEvents()
    assert not window.stop_button.isVisible()
    assert start.isEnabled() and dry_run.isEnabled()
    assert window.use_wsl.isEnabled()


def test_project_state_change_restores_run_monitor_heading(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    _attach_project(window, tmp_path)
    window.execution_details_toggle.setChecked(True)
    window.log_text.setPlainText("synthetic line\n" * 40)
    QApplication.processEvents()
    scrollbar = window.run_monitor_page.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())

    window.project_root = None
    window._refresh_export_buttons()
    QApplication.processEvents()
    assert scrollbar.value() == 0

    _attach_project(window, tmp_path)
    assert scrollbar.value() == 0
    intro = window.run_monitor_page.widget().findChild(QFrame, "pageIntro")
    assert intro is not None and intro.visibleRegion().boundingRect().contains(intro.rect())


def test_dry_run_completion_cannot_be_mistaken_for_full_analysis(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    _attach_project(window, tmp_path)
    window._run_mode = "dry-run"
    window._stop_in_progress = False
    window._run_error_detected = False
    window._on_run_finished(0)
    QApplication.processEvents()

    assert window.status_label.text() == "Dry run completed"
    assert window.phase_label.text() == "Plan checked — no analysis steps were executed."
    assert window.progress.value() == 100
    assert window.progress_value_label.text() == "100%"
    assert window.progress_value_label.isVisible()
    assert not window.progress.isTextVisible()


def test_full_run_completion_retains_unambiguous_completed_state(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    _attach_project(window, tmp_path)
    window._run_mode = "run"
    window._stop_in_progress = False
    window._run_error_detected = False
    window._active_estimate = None
    window._on_run_finished(0)
    QApplication.processEvents()

    assert window.status_label.text() == "Completed"
    assert window.phase_label.text() == ""
    assert window.progress.value() == 100
    assert window.progress_value_label.text() == "100%"
    assert window.progress_value_label.isVisible()
    assert not window.progress.isTextVisible()
    completion_route = window.statusBar().currentMessage()
    assert "Explore results > Figures and tables" in completion_route
    assert "Explore results > Protein network" in completion_route
    assert "Outputs tab" not in completion_route
