from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QLabel

from app.core.config_models import default_config
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


PAGE_LABELS = (
    "Project", "Input Data", "Metadata", "Reference Manager", "Workflow Settings",
    "Resources", "Runtime", "Sanity Checks", "Run Monitor", "Reports", "Outputs",
    "PPI Network",
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window() -> MainWindow:
    app = _app()
    apply_theme(app, "light")
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    app.processEvents()
    return window


def _select_page(window: MainWindow, label: str) -> None:
    window.tabs.setCurrentIndex(PAGE_LABELS.index(label))
    QApplication.processEvents()


def _accessible_text(widget, text: QAccessible.Text) -> str:
    interface = QAccessible.queryAccessibleInterface(widget)
    assert interface is not None
    return interface.text(text)


def _label_relation_names(widget) -> set[str]:
    interface = QAccessible.queryAccessibleInterface(widget)
    assert interface is not None
    return {
        related.text(QAccessible.Text.Name)
        for related, relation in interface.relations(QAccessible.RelationFlag.Label)
        if relation & QAccessible.RelationFlag.Label
    }


def test_critical_controls_expose_specific_qt_accessible_names_and_label_relations(tmp_path: Path) -> None:
    window = _window()
    try:
        _select_page(window, "Input Data")
        window.input_route_tabs.setCurrentIndex(0)
        QApplication.processEvents()
        expected = {
            window.sra_box: "Public sequencing accessions",
            window.gse_box: "GEO Series accession",
            window.de_engine: "DE engine",
            window.contrast_factor: "Comparison factor",
            window.numerator: "Numerator group",
            window.denominator: "Denominator group",
            window.alpha: "BH FDR",
            window.lfc_threshold: "|log2FC|",
            window.design: "Design formula",
            window.reference_level: "Reference level",
            window.organellar: "Mitochondrial / chloroplast genes",
            window.output_table_pick: "Table",
            window.figure_pick: "Figure",
            window.figure_viewer: "Figure preview",
            window.ppi_conf: "Edge filter",
        }
        for widget, name in expected.items():
            exposed_name = _accessible_text(widget, QAccessible.Text.Name)
            if exposed_name != name:
                # The Linux Qt platform interface exposes a native combo or spinbox's
                # selected value as Text.Name, even when accessibleName is set. Its real
                # Label relation is therefore the accessible visible caption; neither a
                # generic parent group nor a setter string can satisfy this branch.
                assert isinstance(widget, (QComboBox, QDoubleSpinBox))
                native_value = (
                    widget.currentText()
                    if isinstance(widget, QComboBox)
                    else widget.text()
                )
                assert exposed_name == native_value
                assert name in {
                    relation_name.rstrip(":")
                    for relation_name in _label_relation_names(widget)
                }

        _select_page(window, "Workflow Settings")
        window.workflow_design_toggle.setChecked(True)
        QApplication.processEvents()
        for widget, label_text in (
            (window.de_engine, "DE engine"),
            (window.contrast_factor, "Comparison factor"),
            (window.numerator, "Numerator group"),
            (window.denominator, "Denominator group"),
            (window.alpha, "BH FDR"),
            (window.lfc_threshold, "|log2FC|"),
            (window.design, "Design formula"),
            (window.reference_level, "Reference level"),
            (window.organellar, "Mitochondrial / chloroplast genes"),
        ):
            assert label_text in _label_relation_names(widget)

        figure_dir = tmp_path / "results" / "figures"
        figure_dir.mkdir(parents=True)
        image = QImage(12, 8, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        assert image.save(str(figure_dir / "volcano.png"))
        window.project_root = tmp_path
        window._refresh_gallery()
        _select_page(window, "Outputs")
        QApplication.processEvents()
        description = _accessible_text(window.figure_viewer, QAccessible.Text.Description)
        assert "volcano.png" in description
        assert str(tmp_path) not in description

        _select_page(window, "PPI Network")
        assert "0 to 100" in _accessible_text(window.ppi_conf, QAccessible.Text.Description)
        assert "0.00 to 1.00" in _accessible_text(window.ppi_conf, QAccessible.Text.Description)
        assert "current view" in _accessible_text(window.ppi_conf, QAccessible.Text.Description).lower()
    finally:
        window.close()


def test_accessibility_targets_remain_keyboard_focusable() -> None:
    window = _window()
    try:
        _select_page(window, "Input Data")
        window.input_route_tabs.setCurrentIndex(0)
        window.sra_box.setFocus()
        QApplication.processEvents()
        assert window.sra_box.hasFocus()
        QTest.keyClick(window.sra_box, Qt.Key.Key_Tab)
        QApplication.processEvents()
        assert QApplication.focusWidget() is not window.sra_box

        _select_page(window, "Workflow Settings")
        window.de_engine.setFocus()
        QApplication.processEvents()
        assert window.de_engine.hasFocus()
        QTest.keyClick(window.de_engine, Qt.Key.Key_Tab)
        QApplication.processEvents()
        assert QApplication.focusWidget() is not window.de_engine
    finally:
        window.close()


def test_adjusted_p_value_accessibility_tracks_imported_results_provenance() -> None:
    window = _window()
    try:
        window.config = default_config("accessibility", Path.cwd())
        window.config.input.type = "deseq2_results"
        window.config.input.deseq2_results_provenance.p_adjustment_method = "Bonferroni"
        window._apply_input_mode_ui()
        assert _accessible_text(window.alpha, QAccessible.Text.Name) == "Adjusted p-value"
        assert "Bonferroni" in _accessible_text(window.alpha, QAccessible.Text.Description)
        assert "Benjamini-Hochberg" not in _accessible_text(window.alpha, QAccessible.Text.Description)

        window.config.input.type = "count_matrix"
        window._apply_input_mode_ui()
        assert _accessible_text(window.alpha, QAccessible.Text.Name) == "BH FDR"
        assert "Benjamini-Hochberg" in _accessible_text(window.alpha, QAccessible.Text.Description)
    finally:
        window.close()
