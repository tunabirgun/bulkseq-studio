from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QWidget,
)

from app.ui.main_window import MainWindow
from app.ui.task_navigator import TaskNavigator
from app.ui.theme import apply_theme
from app.core.config_models import default_config
from app.core.resources import SystemResources, recommend_profile, recommend_rule_threads


PAGE_LABELS = (
    "Project",
    "Input Data",
    "Metadata",
    "Reference Manager",
    "Workflow Settings",
    "Resources",
    "Runtime",
    "Sanity Checks",
    "Run Monitor",
    "Reports",
    "Outputs",
    "PPI Network",
)
ORDINARY_PAGE_LABELS = tuple(label for label in PAGE_LABELS if label not in {
    "Workflow Settings", "Outputs", "PPI Network",
})


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(width: int, height: int) -> MainWindow:
    app = _app()
    QSettings().setValue("theme_mode", "light")
    apply_theme(app, "light")
    window = MainWindow()
    window.resize(width, height)
    window.show()
    QApplication.processEvents()
    return window


def _select_page(window: MainWindow, label: str) -> QWidget:
    index = PAGE_LABELS.index(label)
    window.tabs.setCurrentIndex(index)
    QApplication.processEvents()
    page = window.tabs.currentWidget()
    assert page is not None
    return page


def _visible_scroll_areas(page: QWidget) -> list[QScrollArea]:
    areas = [page] if isinstance(page, QScrollArea) else []
    areas.extend(area for area in page.findChildren(QScrollArea) if area.isVisible())
    return areas


def _assert_bounded_vertical_scroll(area: QScrollArea, *, context: str) -> None:
    """Allow a short overflow, but reject a layout that opens far down a form."""
    viewport_height = max(area.viewport().height(), 1)
    overflow = area.verticalScrollBar().maximum()
    limit = viewport_height * 0.75
    assert overflow <= limit, (
        f"{context} requires {overflow}px of vertical scrolling with a "
        f"{viewport_height}px viewport; compact-page limit is {limit:.1f}px"
    )


def test_save_resources_persists_derived_rule_threads(tmp_path: Path) -> None:
    window = _window(1366, 768)
    try:
        project_root = tmp_path / "resource-project"
        (project_root / "config").mkdir(parents=True)
        window.project_root = project_root
        window.config = default_config("resource-project", project_root)
        window.profile.setCurrentText("custom")
        window.cores.setValue(20)
        window.ram.setValue(60)

        window._save_resources()

        expected = recommend_rule_threads(20)
        assert expected["star_align"] == 10
        assert window.config.rule_threads.model_dump() == expected
        persisted = window.manager.load_config(project_root)
        assert persisted.resources.profile == "custom"
        assert persisted.resources.total_threads == 20
        assert persisted.resources.total_memory_gb == 60
        assert persisted.rule_threads.model_dump() == expected
    finally:
        window.close()


def test_task_navigator_preserves_every_page_and_reaches_each_one() -> None:
    window = _window(1366, 768)
    try:
        assert window.tabs.count() == len(PAGE_LABELS)
        assert tuple(window.tabs.tabText(index) for index in range(window.tabs.count())) == PAGE_LABELS

        for index, label in enumerate(PAGE_LABELS):
            window.tabs.setCurrentIndex(index)
            QApplication.processEvents()
            assert window.tabs.currentIndex() == index
            assert window.tabs.currentWidget() is window.tabs.widget(index), label
            control = window.tabs.findChild(QToolButton, f"taskNavigatorItem{index}")
            assert control is not None and control.accessibleName() == f"Go to {label}"
    finally:
        window.close()


def test_navigator_switches_at_the_compact_threshold_and_keeps_theme_control() -> None:
    window = _window(TaskNavigator.COMPACT_BREAKPOINT, 768)
    try:
        assert not window.tabs.isCompact()
        assert window.tabs.findChild(QWidget, "taskNavigatorRail").isVisible()
        assert window.theme_toggle.isVisible()
        assert window.theme_toggle.text()
        assert window.theme_toggle.focusPolicy().name == "StrongFocus"

        window.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 768)
        QApplication.processEvents()
        selector = window.tabs.findChild(QComboBox, "taskNavigatorCompactSelector")
        stage_selector = window.tabs.findChild(QComboBox, "taskNavigatorCompactStageSelector")
        assert window.tabs.isCompact(), "The compact selector must activate below the declared breakpoint."
        assert selector is not None and selector.isVisible()
        assert stage_selector is not None and stage_selector.isVisible()
        assert stage_selector.count() == 4
        assert [selector.itemText(index) for index in range(selector.count())] == [
            "Project", "Add data", "Samples", "Reference",
        ]
        stage_selector.setCurrentIndex(3)
        QApplication.processEvents()
        assert [selector.itemText(index) for index in range(selector.count())] == [
            "Figures and tables", "Reports", "Protein network",
        ]
        assert window.theme_toggle.isVisible(), "The header theme control must remain available in compact mode."
    finally:
        window.close()


def test_progressive_controls_start_in_a_purposeful_safe_state() -> None:
    window = _window(1366, 768)
    try:
        _select_page(window, "Resources")
        assert not window.resource_manual_group.isVisible()
        window.profile.setCurrentText("custom")
        QApplication.processEvents()
        assert window.resource_manual_group.isVisible()
        assert window.resource_manual_toggle.isChecked()

        _select_page(window, "Outputs")
        assert window.output_no_project_panel.isVisible()
        assert not window.output_controls_widget.isVisible()
        assert not window._outputs_main_splitter.isVisible()
        assert all(not control.isEnabled() for control in window.output_project_controls)
        assert not window.results_inspector.isVisible()

        _select_page(window, "Reports")
        assert window.report_no_project_panel.isVisible()
        assert not window.report_operational_panel.isVisible()
        assert all(not button.isEnabled() for button in window.report_project_buttons)

        _select_page(window, "PPI Network")
        assert window.ppi_no_project_panel.isVisible()
        assert not window.ppi_command_widget.isVisible()
        assert not window.ppi_workspace.isVisible()
        assert not window.ppi_inspector.isVisible()
    finally:
        window.close()


def test_resource_detection_copy_has_no_empty_wslconfig_assignments() -> None:
    window = _window(1366, 768)
    try:
        system = SystemResources(
            "Windows",
            "Test CPU",
            20,
            28,
            64,
            48,
            500,
            "C:/tmp",
            True,
            False,
            False,
            False,
            wsl_ram_gb=63,
            wsl_cpus=24,
            wsl_physical_cores=12,
        )
        window._on_detect_done((system, {"total_threads": 10, "total_memory_gb": 47}))
        copy = window.system_info_label.text()
        assert "24 logical CPUs / 12 physical cores / 63 GB" in copy
        assert "Edit WSL2 memory / CPU limits" in copy
        assert "memory=," not in copy
        assert "processors=)" not in copy
    finally:
        window.close()


def test_resource_detection_copy_reports_wsl_cpu_with_host_ram_fallback() -> None:
    window = _window(1366, 768)
    try:
        system = SystemResources(
            "Windows", "Hybrid CPU", 20, 28, 64, 48, 500, "C:/tmp",
            True, False, False, False,
            wsl_ram_gb=0, wsl_cpus=24, wsl_physical_cores=12,
        )
        recommendation = recommend_profile(system, "balanced")
        assert recommendation["total_threads"] == 18
        assert recommendation["total_memory_gb"] == 48

        window._on_detect_done((system, recommendation))
        copy = window.system_info_label.text()
        assert "24 logical CPUs / 12 physical cores" in copy
        assert "CPU recommendations use the WSL logical CPU allocation" in copy
        assert "RAM recommendations use host RAM (64 GB)" in copy
        assert "18 CPU workers and 48 GB RAM" in window.recommendation_label.text()
        status = window.statusBar().currentMessage()
        assert "CPU basis: WSL2 12 physical / 24 logical CPUs" in status
        assert "RAM basis: host 64 GB RAM (WSL RAM limit unavailable)" in status
    finally:
        window.close()


def test_resource_detection_copy_reports_host_cpu_with_wsl_ram_fallback() -> None:
    window = _window(1366, 768)
    try:
        system = SystemResources(
            "Windows", "Hybrid CPU", 20, 28, 64, 48, 500, "C:/tmp",
            True, False, False, False,
            wsl_ram_gb=63, wsl_cpus=0, wsl_physical_cores=0,
        )
        recommendation = recommend_profile(system, "balanced")
        assert recommendation["total_threads"] == 21
        assert recommendation["total_memory_gb"] == 47

        window._on_detect_done((system, recommendation))
        copy = window.system_info_label.text()
        assert "WSL2 sees 63 GB RAM" in copy
        assert "CPU recommendations use host logical CPUs (28)" in copy
        assert "RAM recommendations use the WSL RAM limit" in copy
        assert "21 CPU workers and 47 GB RAM" in window.recommendation_label.text()
        status = window.statusBar().currentMessage()
        assert "CPU basis: host 20 physical / 28 logical CPUs" in status
        assert "(WSL CPU allocation unavailable)" in status
        assert "RAM basis: WSL2 63 GB RAM" in status
    finally:
        window.close()


def test_optional_custom_gene_sets_stay_collapsed_until_requested() -> None:
    window = _window(1093, 640)
    try:
        _select_page(window, "Workflow Settings")
        window.workflow_section_tabs.setCurrentIndex(2)
        QApplication.processEvents()
        assert window.custom_gene_sets_toggle.text() == "Custom gene sets (enrichment, optional)"
        assert not window.custom_gene_sets_toggle.isChecked()
        assert not window.custom_gene_sets_panel.isVisible()
        assert not window.custom_gmt.isVisible()

        window.custom_gene_sets_toggle.setChecked(True)
        QApplication.processEvents()
        assert window.custom_gene_sets_panel.isVisible()
        assert all(control.isVisible() for control in (
            window.custom_gmt, window.custom_annot, window.custom_background,
        ))
    finally:
        window.close()


def test_outputs_inspector_keeps_headers_and_long_term_actions_visible_at_minimum_width() -> None:
    window = _window(1366, 768)
    try:
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        _select_page(window, "Outputs")
        window._outputs_results_splitter.setSizes([1000, 320])
        QApplication.processEvents()
        inspector = window.results_inspector
        assert 320 <= inspector.width() <= 420
        assert isinstance(inspector, QTabWidget)
        assert tuple(inspector.tabText(index) for index in range(inspector.count())) == (
            "Style", "Genes", "Terms",
        )
        tab_bar = inspector.tabBar()
        for index in range(inspector.count()):
            assert tab_bar.rect().contains(tab_bar.tabRect(index)), (
                inspector.tabText(index), tab_bar.tabRect(index), tab_bar.rect())
        assert inspector.itemToolTip(2).startswith("Enrichment terms")

        inspector.setCurrentIndex(2)
        QApplication.processEvents()
        term_scroll = inspector.currentWidget()
        assert isinstance(term_scroll, QScrollArea)
        for button in (window.term_table_btn, window.term_heatmap_btn):
            term_scroll.ensureWidgetVisible(button, 8, 8)
            QApplication.processEvents()
            rect = button.rect().translated(button.mapTo(term_scroll.viewport(), QPoint(0, 0)))
            assert term_scroll.viewport().rect().contains(rect), (button.text(), rect)
            longest_line = max(button.text().splitlines(), key=len)
            assert button.width() >= button.fontMetrics().horizontalAdvance(longest_line) + 24
            assert button.accessibleName()
            assert button.toolTip()
    finally:
        window.close()


def test_metadata_more_tools_reflow_without_cropping_action_names() -> None:
    window = _window(1093, 640)
    try:
        page = _select_page(window, "Metadata")
        window.metadata_more_toggle.setChecked(True)
        QApplication.processEvents()
        expected = {
            "Duplicate selected", "Autofill replicates", "Add", "Rename", "Remove",
            "Import table…", "Export TSV…", "Restore generated",
        }
        buttons = {
            button.text(): button
            for button in page.findChildren(QPushButton)
            if button.text() in expected
        }
        assert set(buttons) == expected
        for text, button in buttons.items():
            visible = button.visibleRegion().boundingRect()
            required = button.fontMetrics().horizontalAdvance(text) + 20
            assert visible.contains(button.rect()), (text, visible, button.rect())
            assert button.width() >= required, (text, button.width(), required)
        # The disclosure is one left-aligned command list, not buttons floating
        # in three unrelated card columns.
        left_edges = [
            button.mapTo(window.metadata_more_group, QPoint(0, 0)).x()
            for button in window.metadata_advanced_buttons
            if button.text() in {"Duplicate selected", "Add", "Import table…"}
        ]
        assert max(left_edges) - min(left_edges) <= 2, left_edges
    finally:
        window.close()


def test_genes_inspector_keeps_both_actions_visible_below_the_editor() -> None:
    window = _window(1093, 640)
    try:
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        _select_page(window, "Outputs")
        window.results_inspector.setCurrentIndex(1)
        QApplication.processEvents()
        assert not isinstance(window.results_inspector.currentWidget(), QScrollArea)
        for button in (window.goi_save_button, window.goi_generate_button):
            assert button.isVisible()
            visible = button.visibleRegion().boundingRect()
            assert visible.contains(button.rect()), (button.text(), visible, button.rect())
            assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 20
        assert window.goi_save_button.y() < window.goi_generate_button.y()
    finally:
        window.close()


def test_outputs_workspace_and_status_text_keep_consistent_edge_insets() -> None:
    window = _window(1093, 640)
    try:
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        _select_page(window, "Outputs")
        QApplication.processEvents()
        assert window.output_table_heading.mapTo(
            window._outputs_table_panel, QPoint(0, 0)).x() >= 8
        figure_panel = window.output_figure_canvas.parentWidget()
        assert window.output_figure_heading.mapTo(figure_panel, QPoint(0, 0)).x() >= 8
        assert window.results_inspector.mapTo(
            window._outputs_inspector_host, QPoint(0, 0)).x() >= 8

        window.statusBar().showMessage("Project open — review the inputs and settings.")
        QApplication.processEvents()
        margins = window.statusBar().contentsMargins()
        assert margins.left() >= 8 and margins.right() >= 8
        assert margins.top() >= 2 and margins.bottom() >= 2
        assert window.statusBar().currentMessage().startswith("Project open")
        window.statusBar().showMessage("Temporary status", 10)
        QTest.qWait(25)
        QApplication.processEvents()
        assert window.statusBar().currentMessage() == ""
    finally:
        window.close()


def test_output_option_names_do_not_wrap_or_clip_at_compact_desktop_width() -> None:
    window = _window(1093, 640)
    try:
        _select_page(window, "Workflow Settings")
        window.workflow_section_tabs.setCurrentIndex(2)
        QApplication.processEvents()
        controls = (
            window.enrichment,
            window.figures,
            window.gsva,
            window.rseqc,
            window.meta_analysis,
            window.per_study_enrichment,
        )
        assert tuple(control.text() for control in controls) == (
            "Enrichment",
            "Publication figures",
            "GSVA pathway activity",
            "Extended QC (RSeQC)",
            "Multi-study meta-analysis",
            "Per-study enrichment",
        )
        for control in controls:
            assert "\n" not in control.text()
            assert control.width() >= control.sizeHint().width(), (
                control.text(), control.width(), control.sizeHint().width())
            assert control.visibleRegion().boundingRect().contains(control.rect()), (
                control.text(), control.visibleRegion().boundingRect(), control.rect())
            assert control.accessibleDescription()
            assert control.toolTip()
        # The paired grid uses the width that the old QFormLayout left empty.
        assert window.figures.mapTo(window.out_group, QPoint(0, 0)).x() > 400
        assert window.per_study_enrichment.mapTo(window.out_group, QPoint(0, 0)).x() > 400
    finally:
        window.close()


def test_ppi_progressive_sections_are_navigation_tabs_not_field_like_rows() -> None:
    window = _window(1093, 640)
    try:
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        _select_page(window, "PPI Network")
        QApplication.processEvents()
        inspector = window.ppi_inspector
        assert isinstance(inspector, QTabWidget)
        assert inspector.property("uiRole") == "inspectorTabs"
        assert tuple(inspector.tabText(index) for index in range(inspector.count())) == (
            "View", "Rebuild", "Export",
        )
        inspector.setCurrentIndex(0)
        QApplication.processEvents()
        tab_bar = inspector.tabBar()
        for index in range(inspector.count()):
            assert tab_bar.rect().contains(tab_bar.tabRect(index))
            assert inspector.itemToolTip(index)
        assert window.ppi_focus_cb.visibleRegion().boundingRect().contains(
            window.ppi_focus_cb.rect())
        assert window.ppi_focus_cb.width() >= window.ppi_focus_cb.sizeHint().width()
    finally:
        window.close()


def test_ppi_compact_combo_labels_keep_full_meaning_in_metadata() -> None:
    window = _window(1093, 640)
    try:
        assert window.ppi_layout_pick.itemText(0) == "Force-directed"
        assert window.ppi_layout_pick.itemData(0) == "fcose"
        assert "fCoSE" in window.ppi_layout_pick.toolTip()
        assert window.ppi_color_pick.itemText(0) == "Fold change"
        assert window.ppi_color_pick.itemData(0) == "log2FoldChange"
        assert "log₂ fold change" in window.ppi_color_pick.toolTip()
        assert window.ppi_size_pick.itemText(2) == "Significance"
        assert window.ppi_size_pick.itemData(2) == "neglog10padj"
        assert "−log₁₀ adjusted p-value" in window.ppi_size_pick.toolTip()
    finally:
        window.close()


def test_input_summary_is_a_compact_status_banner_not_an_editor() -> None:
    window = _window(1093, 640)
    try:
        _select_page(window, "Input Data")
        QApplication.processEvents()
        assert isinstance(window.input_preview, QLabel)
        assert not isinstance(window.input_preview, QTextEdit)
        assert window.input_preview_frame.property("uiRole") == "statusBanner"
        assert window.input_preview_frame.height() <= 72
        assert window.input_preview.wordWrap()
        assert window.input_preview.accessibleName() == "Input route summary and next step"
    finally:
        window.close()


def test_runtime_and_sanity_statuses_use_compact_selectable_banners() -> None:
    window = _window(1093, 640)
    try:
        window.project_root = Path("synthetic_ui_project")
        window.runtime_no_project_panel.setVisible(False)
        window.runtime_operational_panel.setVisible(True)
        _select_page(window, "Runtime")
        QApplication.processEvents()
        assert isinstance(window.runtime_text, QLabel)
        assert not isinstance(window.runtime_text, QTextEdit)
        assert window.runtime_text.property("uiRole") == "statusBanner"
        assert window.runtime_text.height() <= 72

        window._update_sanity_state({"01_input_validation": "PASS"})
        _select_page(window, "Sanity Checks")
        QApplication.processEvents()
        assert isinstance(window.sanity_text, QLabel)
        assert not isinstance(window.sanity_text, QTextEdit)
        assert window.sanity_text.property("uiRole") == "statusBanner"
        assert window.sanity_text.height() <= 120
        assert "Overall: PASS" in window.sanity_text.toPlainText()
    finally:
        window.close()


def test_pre_run_validation_button_reserves_a_font_derived_glyph_guard() -> None:
    window = _window(1093, 640)
    try:
        _select_page(window, "Sanity Checks")
        button = window.sanity_run_button
        metrics = button.fontMetrics()
        glyph_guard = max(2, (metrics.horizontalAdvance(" ") + 1) // 2)
        assert button.minimumWidth() >= button.sizeHint().width() + glyph_guard
        assert button.visibleRegion().boundingRect().contains(button.rect())
    finally:
        window.close()


def test_project_status_is_compact_copy_and_empty_recent_list_has_a_clear_message() -> None:
    settings = QSettings()
    saved_recent = settings.value("recent_projects", None)
    settings.remove("recent_projects")
    window = _window(1093, 640)
    try:
        _select_page(window, "Project")
        assert window.project_status.toPlainText().startswith("Create a new project")
        assert window.project_status.hasSelectedText() is False
        assert window.project_status.height() < 70
        assert window.recent_empty_label.isVisible()
        assert not window.recent_pick.isVisible()
        assert not window.recent_open.isVisible()
    finally:
        window.close()
        if saved_recent is None:
            settings.remove("recent_projects")
        else:
            settings.setValue("recent_projects", saved_recent)


def test_figure_detail_defaults_to_common_controls_and_reveals_every_advanced_control() -> None:
    window = _window(1093, 640)
    try:
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        _select_page(window, "Outputs")
        window._outputs_results_splitter.setSizes([700, 420])
        appearance_scroll = window.figure_style_sections.currentWidget()
        assert isinstance(appearance_scroll, QScrollArea)
        assert appearance_scroll.verticalScrollBar().maximum() == 0
        assert not window.figure_appearance_advanced_toggle.isChecked()
        assert not window.figure_appearance_advanced_panel.isVisible()
        window.figure_appearance_advanced_toggle.setChecked(True)
        QApplication.processEvents()
        assert window.figure_appearance_advanced_panel.isVisible()
        for control in (
            window.fig_font_family,
            window.fig_label_bold,
            window.fig_title_bold,
            window.fig_gene_italic,
        ):
            assert control.isVisible()
        window.figure_style_sections.setCurrentIndex(1)
        QApplication.processEvents()

        detail_scroll = window.figure_style_sections.currentWidget()
        assert isinstance(detail_scroll, QScrollArea)
        assert not window.figure_detail_advanced_toggle.isChecked()
        assert not window.figure_detail_advanced_panel.isVisible()
        assert detail_scroll.verticalScrollBar().maximum() == 0
        assert detail_scroll.horizontalScrollBar().maximum() == 0
        assert all(control.isVisible() for control in window.figure_detail_common_controls)

        expected_controls = {
            window.fig_volcano_top,
            window.fig_heatmap_top,
            window.fig_pca_ntop,
            window.fig_volcano_yscale,
            window.fig_volcano_ycap,
            window.fig_volcano_alpha,
            window.fig_pca_fixed_aspect,
            window.fig_sample_labels,
            window.fig_heatmap_zlim,
            window.fig_enrich_show,
            window.fig_ppi_layout,
        }
        assert set(window.figure_detail_common_controls + window.figure_detail_advanced_controls) == expected_controls

        window.figure_detail_advanced_toggle.setChecked(True)
        QApplication.processEvents()
        assert window.figure_detail_advanced_panel.isVisible()
        for control in window.figure_detail_advanced_controls:
            assert control.isVisible()
            detail_scroll.ensureWidgetVisible(control, 12, 12)
            QApplication.processEvents()
            control_rect = control.rect().translated(
                control.mapTo(detail_scroll.viewport(), QPoint(0, 0)))
            if not detail_scroll.viewport().rect().contains(control_rect):
                # QScrollArea's descendant helper can stop at a wrapped QFormLayout
                # label, leaving the associated field a few pixels below the viewport.
                # Address the field in content coordinates so the gate still proves
                # that the complete control can be brought into view.
                content = detail_scroll.widget()
                content_pos = control.mapTo(content, QPoint(0, 0))
                detail_scroll.ensureVisible(
                    content_pos.x() + control.width() // 2,
                    content_pos.y() + control.height() // 2,
                    control.width() // 2 + 12,
                    control.height() // 2 + 12,
                )
                QApplication.processEvents()
                control_rect = control.rect().translated(
                    control.mapTo(detail_scroll.viewport(), QPoint(0, 0)))
            assert detail_scroll.viewport().rect().contains(control_rect), (
                type(control).__name__, control_rect, detail_scroll.viewport().rect())

        assert window.apply_figure_style_button.text() == "Apply && regenerate"
        assert window.apply_figure_style_button.accessibleName() == "Apply and regenerate figures"
    finally:
        window.close()


def test_output_preview_sizes_common_de_headers_without_unbounded_columns(tmp_path) -> None:
    result_path = tmp_path / "differential_expression.tsv"
    extra_headers = [f"supporting_metric_{index}" for index in range(8)]
    headers = ["gene", "log2FoldChange", "padj", "description", *extra_headers]
    values = ["GENE1", "1.25", "0.001", "long content " * 80, *(["x" * 120] * 8)]
    result_path.write_text(
        "\t".join(headers) + "\n" + "\t".join(values) + "\n",
        encoding="utf-8",
    )
    window = _window(1093, 640)
    try:
        _select_page(window, "Outputs")
        window.project_root = tmp_path
        window.output_table_pick.clear()
        window.output_table_pick.addItem("Differential expression", result_path.name)
        window._load_output_table()
        QApplication.processEvents()

        header = window.output_table.horizontalHeader()
        fold_change_column = 1
        assert window.output_table.horizontalHeaderItem(fold_change_column).text() == "log2FoldChange"
        assert window.output_table.columnWidth(fold_change_column) >= (
            header.fontMetrics().horizontalAdvance("log2FoldChange") + 24)
        assert max(
            window.output_table.columnWidth(column)
            for column in range(window.output_table.columnCount())
        ) <= 320
        assert window.output_table.horizontalScrollBar().maximum() > 0
    finally:
        window.close()


def test_workflow_disclosures_use_available_height_before_scrolling() -> None:
    window = _window(1366, 768)
    try:
        _select_page(window, "Workflow Settings")
        tabs = window.workflow_section_tabs

        tabs.setCurrentIndex(0)
        QApplication.processEvents()
        collapsed_height = tabs.height()
        window.workflow_design_toggle.setChecked(True)
        QApplication.processEvents()
        QApplication.processEvents()
        comparison = tabs.currentWidget()
        assert tabs.height() > collapsed_height
        # Native Qt font metrics vary slightly across platforms. Any residual
        # scroll must stay below one text line rather than exposing a hidden row.
        assert comparison.verticalScrollBar().maximum() < comparison.fontMetrics().height()
        assert window.workflow_save_bar.visibleRegion().boundingRect().contains(
            window.workflow_save_bar.rect())
        window.workflow_design_toggle.setChecked(False)
        QApplication.processEvents()
        QApplication.processEvents()
        assert tabs.height() == collapsed_height

        tabs.setCurrentIndex(3)
        QApplication.processEvents()
        advanced_collapsed_height = tabs.height()
        window.adv_toggle.setChecked(True)
        QApplication.processEvents()
        QApplication.processEvents()
        advanced = tabs.currentWidget()
        assert tabs.height() > advanced_collapsed_height
        assert advanced.verticalScrollBar().maximum() < advanced.viewport().height() * 0.5
        assert window.workflow_save_bar.visibleRegion().boundingRect().contains(
            window.workflow_save_bar.rect())
        window.adv_toggle.setChecked(False)
        QApplication.processEvents()
        QApplication.processEvents()
        assert tabs.height() == advanced_collapsed_height
    finally:
        window.close()


def test_workflow_advanced_content_fits_without_scroll_at_full_hd() -> None:
    window = _window(1920, 1080)
    try:
        _select_page(window, "Workflow Settings")
        window.workflow_section_tabs.setCurrentIndex(3)
        window.adv_toggle.setChecked(True)
        QApplication.processEvents()
        QApplication.processEvents()
        advanced = window.workflow_section_tabs.currentWidget()
        assert advanced.verticalScrollBar().maximum() == 0
        assert window.workflow_save_bar.isVisible()
        expanded_height = window.workflow_section_tabs.height()
        window.adv_toggle.setChecked(False)
        QApplication.processEvents()
        QApplication.processEvents()
        assert window.workflow_section_tabs.height() < expanded_height * 0.4
    finally:
        window.close()


def test_expanded_workflow_recalculates_after_compact_to_wide_resize() -> None:
    window = _window(1093, 640)
    try:
        _select_page(window, "Workflow Settings")
        window.workflow_section_tabs.setCurrentIndex(3)
        window.adv_toggle.setChecked(True)
        QApplication.processEvents()
        window.resize(1366, 768)
        QApplication.processEvents()
        QApplication.processEvents()
        QApplication.processEvents()
        layout = window.workflow_page_layout
        margins = layout.contentsMargins()
        available = (
            window.workflow_page.height()
            - margins.top()
            - margins.bottom()
            - window.workflow_intro.height()
            - window.workflow_save_bar.height()
            - layout.spacing() * 2
        )
        assert window.workflow_section_tabs.height() <= available
        assert window.workflow_save_bar.visibleRegion().boundingRect().contains(
            window.workflow_save_bar.rect())
    finally:
        window.close()


@pytest.mark.parametrize(
    "width,height",
    [(1093, 614), (1280, 720), (1366, 768), (1920, 1080)],
)
def test_primary_layouts_remain_usable_at_desktop_sizes(width: int, height: int) -> None:
    window = _window(width, height)
    try:
        for label in ORDINARY_PAGE_LABELS:
            page = _select_page(window, label)
            assert all(area.horizontalScrollBar().maximum() == 0 for area in _visible_scroll_areas(page)), label

        workflow_page = _select_page(window, "Workflow Settings")
        assert window.workflow_section_tabs.isVisible()
        assert tuple(
            window.workflow_section_tabs.tabText(index)
            for index in range(window.workflow_section_tabs.count())
        ) == ("Comparison", "Read processing", "Output options", "Advanced")
        for index in range(window.workflow_section_tabs.count()):
            window.workflow_section_tabs.setCurrentIndex(index)
            QApplication.processEvents()
            section = window.workflow_section_tabs.currentWidget()
            assert isinstance(section, QScrollArea)
            assert section.horizontalScrollBar().maximum() == 0
            holder_layout = section.widget().layout()
            section_content = holder_layout.itemAt(0).widget()
            assert section_content is not None
            margins = section_content.layout().contentsMargins()
            assert margins.left() >= 12 and margins.right() >= 12
            assert margins.top() >= 8 and margins.bottom() >= 8
            _assert_bounded_vertical_scroll(
                section,
                context=f"Workflow Settings / {window.workflow_section_tabs.tabText(index)}",
            )
        assert all(area.horizontalScrollBar().maximum() == 0 for area in _visible_scroll_areas(workflow_page))
        window.workflow_section_tabs.setCurrentIndex(0)
        QApplication.processEvents()
        comparison = window.workflow_section_tabs.currentWidget()
        group_origin = window.de_group.mapTo(comparison.viewport(), QPoint(0, 0))
        assert group_origin.x() >= 12 and group_origin.y() >= 8
        assert not window.workflow_design_options.isVisible()
        for control in (
            window.de_engine, window.contrast_factor, window.numerator,
            window.denominator, window.alpha, window.lfc_threshold,
            window.workflow_design_toggle,
        ):
            assert control.isVisible()
            assert control.visibleRegion().boundingRect().contains(control.rect()), (
                width, height, control.objectName() or type(control).__name__)

        # Exercise the editing layout explicitly; without a project the real UI
        # intentionally defers the complete workspace behind its empty state.
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        _select_page(window, "Outputs")
        window._outputs_results_splitter.setSizes([700, 420])
        QApplication.processEvents()
        assert window.figure_viewer.isVisible()
        assert window.figure_viewer.width() >= 300
        assert window.figure_viewer.height() >= 280
        assert window._outputs_results_splitter.sizes()[1] >= 300
        style_sections = window.figure_style_sections
        assert tuple(style_sections.tabText(index) for index in range(style_sections.count())) == (
            "Appearance", "Detail", "Size", "Overrides",
        )
        for index in range(style_sections.count()):
            style_sections.setCurrentIndex(index)
            QApplication.processEvents()
            section = style_sections.currentWidget()
            assert isinstance(section, QScrollArea) and section.isVisible()
            _assert_bounded_vertical_scroll(
                section,
                context=f"Outputs / Figure style / {style_sections.tabText(index)}",
            )

        _select_page(window, "PPI Network")
        assert window.ppi_viewer.isVisible()
        assert window.ppi_viewer.width() >= 300
        assert window.ppi_viewer.height() >= 300
        assert window.ppi_inspector.isVisible()
        assert window.ppi_workspace.sizes()[1] >= 300
        for area_index, area in enumerate(_visible_scroll_areas(window.tabs.currentWidget())):
            assert area.horizontalScrollBar().maximum() == 0
            _assert_bounded_vertical_scroll(
                area,
                context=f"PPI Network / inspector scroll area {area_index + 1}",
            )
    finally:
        window.close()
