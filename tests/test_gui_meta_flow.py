from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QImage, QPainter, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QTableWidgetItem

from app.core.config_models import default_config
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def _window() -> MainWindow:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1093, 640)
    window.show()
    app.processEvents()
    return window


def test_unsaved_study_edits_drive_preview_and_unsupported_route_remains_reversible(tmp_path: Path) -> None:
    window = _window()
    try:
        window.project_root = tmp_path
        window.config = default_config("sample", tmp_path)
        window.config.input.type = "count_matrix"
        window._apply_input_mode_ui()
        window.metadata_table.load_dataframe(pd.DataFrame({
            "sample_id": [f"s{i}" for i in range(8)],
            "condition": ["C", "C", "T", "T"] * 2,
        }))
        window._add_study_column()
        assert "dataset" in window.metadata_table.column_names()
        dataset_col = window.metadata_table.column_names().index("dataset")
        for row in range(8):
            window.metadata_table.setItem(row, dataset_col, QTableWidgetItem("A" if row < 4 else "B"))
        window.numerator.setCurrentText("T")
        window.denominator.setCurrentText("C")
        window.meta_analysis.setChecked(True)
        window._refresh_meta_preview()
        assert "Metadata eligible: 2 eligible studies; 0 excluded" in window.meta_preview.text()
        assert "Current-editor preview" in window.meta_preview.text()
        assert "saved inputs" in window.meta_preview.text()
        accessible = QAccessible.queryAccessibleInterface(window.meta_preview)
        assert accessible is not None
        assert "2 eligible studies" in accessible.text(QAccessible.Text.Name)
        assert "not study independence" in window.meta_preview.text()

        condition_col = window.metadata_table.column_names().index("condition")
        window.metadata_table.item(4, condition_col).setText("T")
        window._refresh_meta_preview()
        assert "1 eligible study; 1 excluded" in window.meta_preview.text()
        assert "1 excluded" in accessible.text(QAccessible.Text.Name)
        window.metadata_table.item(4, condition_col).setText("C")

        window.design.setText("~ batch + condition")
        assert "Metadata needs changes" in window.meta_preview.text()
        assert "batch" in window.meta_preview_detail_text.text()
        assert "Metadata needs changes" in accessible.text(QAccessible.Text.Name)

        window.config.input.type = "microarray"
        window._apply_input_mode_ui()
        assert window.meta_analysis.isEnabled() and window.meta_analysis.isChecked()
        assert "unavailable" in window.meta_preview.text()
        assert "unavailable" in accessible.text(QAccessible.Text.Name)
        window.meta_analysis.setChecked(False)
        assert "is off" in window.meta_preview.text()
        window.config.input.type = "count_matrix"
        window._apply_input_mode_ui()
        assert not window.meta_analysis.isChecked()
    finally:
        window.close()


def test_cross_study_report_actions_follow_saved_file_even_when_meta_is_off(tmp_path: Path) -> None:
    window = _window()
    try:
        window.project_root = tmp_path
        window._refresh_export_buttons()
        assert not window.open_meta_report_button.isEnabled()
        reports = tmp_path / "results" / "reports"
        reports.mkdir(parents=True)
        (reports / "meta_analysis_report.html").write_text("<html></html>", encoding="utf-8")
        window.meta_analysis.setChecked(False)
        window._refresh_export_buttons()
        assert window.open_meta_report_button.isEnabled()
        assert next(button for button in window.report_project_buttons
                    if button.text() == "Open cross-study report").isEnabled()
        assert not window.report_open_results_button.isEnabled()
        assert window.report_open_meta_button.property("primary") is True
        assert window.report_generate_button.property("primary") is False
        (reports / "results_report.html").write_text("<html></html>", encoding="utf-8")
        window._refresh_export_buttons()
        assert window.report_open_results_button.text() == "Open main results report"
        assert window.report_open_results_button.property("primary") is True
        assert window.report_open_meta_button.property("primary") is False
    finally:
        window.close()


def test_viewer_incremental_zoom_keeps_arrow_pan_and_inspector_choice(tmp_path: Path) -> None:
    window = _window()
    try:
        window.project_root = tmp_path
        window._refresh_export_buttons()
        window.tabs.setCurrentIndex(10)
        QApplication.processEvents()
        image = QImage(1600, 1200, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        path = tmp_path / "large.png"
        assert image.save(str(path))
        viewer = window.figure_viewer
        viewer.set_image(path)
        viewer.setFocus()
        QApplication.processEvents()
        before = viewer.transform().m11()
        QTest.keyClick(viewer, Qt.Key.Key_Plus)
        assert viewer.transform().m11() > before
        QTest.keyClick(viewer, Qt.Key.Key_Minus)
        assert abs(viewer.transform().m11() - before) < 1e-6
        viewer.actual_size()
        QApplication.processEvents()
        before_pan = viewer.horizontalScrollBar().value()
        QTest.keyClick(viewer, Qt.Key.Key_Right)
        assert viewer.horizontalScrollBar().value() > before_pan

        window.customize_figure_button.setChecked(True)
        window.customize_figure_button.setChecked(False)
        assert not window._outputs_inspector_host.isVisible()
        window.resize(1366, 768)
        QApplication.processEvents()
        assert not window._outputs_inspector_host.isVisible()
        window.customize_figure_button.setChecked(True)
        assert window._outputs_inspector_host.isVisible()

        figures = tmp_path / "results" / "figures"
        figures.mkdir(parents=True)
        assert image.save(str(figures / "volcano.png"))
        table = tmp_path / "results" / "deseq2" / "deseq2_results.csv"
        table.parent.mkdir(parents=True)
        table.write_text("gene_id,log2FoldChange,padj\nG1,1,0.01\n", encoding="utf-8")
        window._refresh_gallery()
        assert "Volcano from the main differential-expression results" in window.figure_description.text()
        assert "deseq2_results.csv" in window.figure_description.text()
        assert window.figure_data_button.isEnabled()
    finally:
        window.close()


def test_style_and_ontology_controls_have_names_help_and_visible_label_relations() -> None:
    window = _window()
    try:
        def assert_labeled(widget, caption: str) -> None:
            interface = QAccessible.queryAccessibleInterface(widget)
            assert interface is not None
            assert widget.accessibleName() == caption
            name = interface.text(QAccessible.Text.Name)
            assert name == caption or (isinstance(widget, QComboBox)
                                       and name == widget.currentText())
            assert interface.text(QAccessible.Text.Description)
            assert any(caption == label.text(QAccessible.Text.Name)
                       for label, relation in interface.relations(QAccessible.RelationFlag.Label)
                       if relation & QAccessible.RelationFlag.Label)
            assert any(label.text() == caption for label in window.findChildren(QLabel)
                       if label.buddy() is widget)

        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        window.tabs.setCurrentIndex(10)
        window.customize_figure_button.setChecked(True)
        inventory = {
            "fig_palette": "Palette", "fig_point_size": "Point size",
            "fig_base_font": "Base font size", "fig_font_family": "Font family",
            "fig_volcano_top": "Volcano top-N labels", "fig_heatmap_top": "Heatmap top-N genes",
            "fig_pca_ntop": "PCA n-top genes", "fig_volcano_yscale": "Volcano y-axis",
            "fig_volcano_ycap": "Volcano y cap", "fig_volcano_alpha": "Volcano point alpha",
            "fig_heatmap_zlim": "Heatmap z limit",
            "fig_enrich_show": "Enrichment categories shown",
            "fig_meta_label_top": "Meta-analysis: genes labelled on the volcano",
            "fig_meta_heatmap_top": "Meta-analysis: genes in the effect-size heatmap",
            "fig_meta_enrich_show": "Meta-analysis: enrichment terms shown",
            "fig_width": "Width", "fig_height": "Height", "fig_dpi": "DPI (PNG)",
            "fig_dim_unit": "Size units",
        }
        window.figure_appearance_advanced_toggle.setChecked(True)
        window.figure_detail_advanced_toggle.setChecked(True)
        appearance = {"fig_palette", "fig_point_size", "fig_base_font", "fig_font_family"}
        size = {"fig_width", "fig_height", "fig_dpi", "fig_dim_unit"}
        for field, caption in inventory.items():
            window.figure_style_sections.setCurrentIndex(
                0 if field in appearance else 2 if field in size else 1)
            QApplication.processEvents()
            widget = getattr(window, field)
            assert widget.isVisible(), field
            assert_labeled(widget, caption)

        window.tabs.setCurrentIndex(4)
        window.workflow_section_tabs.setCurrentIndex(2)
        QApplication.processEvents()
        ontology = window.meta_go_ontology
        for enabled in (False, True):
            ontology.setEnabled(enabled)
            ontology.setCurrentIndex(1)
            assert_labeled(ontology, "Meta-analysis GO ontology")
            interface = QAccessible.queryAccessibleInterface(ontology)
            assert "Gene Ontology" in interface.text(QAccessible.Text.Description)
            assert ontology.currentText() == "Molecular function (MF)"

        palette = window.fig_palette
        palette_label = next(label for label in window.findChildren(QLabel)
                             if label.buddy() is palette)
        palette_label.setBuddy(None)
        palette.setAccessibleName("")
        with pytest.raises(AssertionError):
            assert_labeled(palette, "Palette")
        palette.setAccessibleName("Palette")
        palette_label.setBuddy(palette)
        palette_label.setText("Incorrect label")
        with pytest.raises(AssertionError):
            assert_labeled(palette, "Palette")
    finally:
        window.close()


def test_compact_fit_is_inside_actual_visible_region_with_table_and_inspector(tmp_path: Path) -> None:
    window = _window()
    try:
        window.project_root = tmp_path
        window._refresh_export_buttons()
        window.tabs.setCurrentIndex(10)
        QApplication.processEvents()
        assert window.tabs.isCompact()
        assert not window._outputs_inspector_host.isVisible()
        figures = tmp_path / "results" / "figures"
        figures.mkdir(parents=True)
        image = QImage(1600, 1200, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.setPen(QPen(Qt.GlobalColor.black, 12))
        painter.drawRect(12, 12, 1576, 1176)
        painter.end()
        assert image.save(str(figures / "volcano.png"))
        table = tmp_path / "results" / "deseq2" / "deseq2_results.csv"
        table.parent.mkdir(parents=True)
        table.write_text("gene_id,log2FoldChange,padj\nG1,1,0.01\n", encoding="utf-8")
        window._refresh_gallery()
        window.output_table_pick.setCurrentText("results/deseq2/deseq2_results.csv")
        viewer = window.figure_viewer
        for theme in ("light", "dark"):
            apply_theme(QApplication.instance(), theme)
            for inspector in (False, True):
                window.customize_figure_button.setChecked(inspector)
                for table_open in (False, True):
                    window._outputs_main_splitter.setSizes([0, 680])
                    if table_open:
                        window._load_output_table()
                    QApplication.processEvents()
                    viewer.fit()
                    QApplication.processEvents()
                    visible = viewer.viewport().visibleRegion().boundingRect()
                    image_region = viewer.mapFromScene(viewer._item.sceneBoundingRect()).boundingRect()
                    assert visible.contains(image_region), (theme, inspector, table_open,
                                                            visible, image_region)
        window.resize(1800, 1000)
        QApplication.processEvents()
        window._outputs_main_splitter.setSizes([380, 550])
        saved_splitter = window._outputs_main_splitter.saveState()
        window.resize(1093, 640)
        for theme in ("light", "dark"):
            apply_theme(QApplication.instance(), theme)
            for inspector in (False, True):
                window.customize_figure_button.setChecked(inspector)
                window._outputs_main_splitter.restoreState(saved_splitter)
                QApplication.processEvents()
                viewer.fit()
                QApplication.processEvents()
                assert viewer.viewport().visibleRegion().boundingRect().contains(
                    viewer.mapFromScene(viewer._item.sceneBoundingRect()).boundingRect()), (
                        "restored", theme, inspector)
    finally:
        window.close()


def test_automatic_inspector_tracks_compact_mode_without_overriding_user_choice() -> None:
    window = _window()
    try:
        window.project_root = Path.cwd()
        window._refresh_export_buttons()
        window.tabs.setCurrentIndex(10)
        QApplication.processEvents()
        assert window.tabs.isCompact()
        assert not window._outputs_inspector_host.isVisible()
        window.resize(1800, 1000)
        QApplication.processEvents()
        assert not window.tabs.isCompact()
        assert window._outputs_inspector_host.isVisible()
        window.customize_figure_button.setChecked(False)
        window.resize(1093, 640)
        QApplication.processEvents()
        assert not window._outputs_inspector_host.isVisible()
        window.customize_figure_button.setChecked(True)
        assert window._outputs_inspector_host.isVisible()
    finally:
        window.close()


def test_gallery_source_action_uses_exact_plotted_tables_and_disables_matrix_mismatches(
    tmp_path: Path, monkeypatch,
) -> None:
    window = _window()
    try:
        window.project_root = tmp_path
        window._refresh_export_buttons()
        window.tabs.setCurrentIndex(10)
        figures = tmp_path / "results" / "figures"
        study_figures = tmp_path / "results" / "meta" / "per_study" / "A" / "figures"
        figures.mkdir(parents=True)
        study_figures.mkdir(parents=True)
        (study_figures.parent.parent / "manifest.json").write_text("{}", encoding="utf-8")
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        for stem in ("meta_enrichment_dotplot", "meta_volcano", "meta_heterogeneity",
                     "pca", "top_deg_heatmap"):
            assert image.save(str(figures / f"{stem}.png"))
        for stem in ("pca", "heatmap_topdeg", "volcano"):
            assert image.save(str(study_figures / f"{stem}.png"))
        meta = tmp_path / "results" / "meta"
        (meta / "meta_enrichment_plotted.csv").write_text(
            "Cluster,Description,GeneRatio,p.adjust,Count\nA,term,1/2,0.01,1\n", encoding="utf-8")
        (meta / "per_study_A.csv").write_text(
            "gene_id,baseMean,log2FoldChange,lfcSE,pvalue,padj\nG1,10,1,0.2,0.01,0.02\n",
            encoding="utf-8")
        normalized = tmp_path / "results" / "deseq2" / "normalized_counts.csv"
        normalized.parent.mkdir(parents=True)
        normalized.write_text("gene_id,sample_1\nG1,10\n", encoding="utf-8")
        opened = []
        monkeypatch.setattr(window, "_register_output_table", opened.append)
        window._refresh_gallery()

        def choose(label: str) -> None:
            index = window.figure_pick.findText(label)
            assert index >= 0
            window.figure_pick.setCurrentIndex(index)
            QApplication.processEvents()

        choose("meta_enrichment_dotplot.png")
        assert window.figure_data_button.isEnabled()
        assert "plotted term-by-set" in window.figure_description.text()
        window.figure_data_button.click()
        assert opened[-1] == "results/meta/meta_enrichment_plotted.csv"

        for label in ("A / pca", "A / heatmap_topdeg"):
            choose(label)
            assert Path(window.figure_pick.currentData()) == study_figures / f"{label.split(' / ')[1]}.png"
            assert not window.figure_data_button.isEnabled()
            assert "No directly matching tabular source" in window.figure_description.text()
            count = len(opened)
            window.figure_data_button.click()
            assert len(opened) == count

        for label in ("pca.png", "top_deg_heatmap.png"):
            choose(label)
            assert not window.figure_data_button.isEnabled()
            assert "transformed count matrix" in window.figure_description.text()

        choose("A / volcano")
        assert window.figure_data_button.isEnabled()
        window.figure_data_button.click()
        assert opened[-1] == "results/meta/per_study_A.csv"

        choose("meta_heterogeneity.png")
        assert not window.figure_data_button.isEnabled()
        (meta / "meta_analysis_results.csv").write_text(
            "gene_id,rem_log2FC,combined_padj,I2,combined_pvalue\nG1,1,0.01,0.1,0.001\n",
            encoding="utf-8")
        choose("meta_volcano.png")
        assert window.figure_data_button.isEnabled()
        window.figure_data_button.click()
        assert opened[-1] == "results/meta/meta_analysis_results.csv"
    finally:
        window.close()
