from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pandas as pd
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QLineEdit,
    QMessageBox,
)

from app.core.config_models import default_config
from app.core.de_results import ExternalDEImportDetails
from app.ui.main_window import MainWindow


def _window(root: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project_root = root
    window.config = default_config("external-results", root)
    (root / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(window.manager, "save_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window, "_require_project", lambda: True)
    window.show()
    app.processEvents()
    return window


def test_external_results_confirmation_dialog_collects_all_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)
    try:
        def complete_dialog() -> None:
            dialog = QApplication.activeModalWidget()
            assert dialog is not None
            dialog.findChild(QLineEdit, "externalDENumerator").setText("case")
            dialog.findChild(QLineEdit, "externalDEDenominator").setText("control")
            dialog.findChild(QLineEdit, "externalDEUpstreamMethod").setText("limma")
            dialog.findChild(QComboBox, "externalDELfcShrinkage").setCurrentIndex(2)
            dialog.findChild(QLineEdit, "externalDEPAdjustmentMethod").setText("Benjamini-Hochberg")
            dialog.findChild(QCheckBox, "externalDEConfirmation").setChecked(True)
            buttons = dialog.findChild(QDialogButtonBox)
            ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
            assert ok.isEnabled()
            ok.click()

        QTimer.singleShot(0, complete_dialog)
        details = window._ask_de_results_direction()
        assert details == ExternalDEImportDetails(
            numerator="case",
            denominator="control",
            upstream_method="limma",
            lfc_shrinkage="not_applied",
            p_adjustment_method="Benjamini-Hochberg",
        )
    finally:
        window.close()


@pytest.mark.parametrize("gene_header", ["ensembl", "Unnamed: 0"])
def test_external_results_import_records_direction_without_fabricating_a_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gene_header: str,
) -> None:
    source = tmp_path / "external.csv"
    pd.DataFrame(
        {
            gene_header: ["ENSG000001"],
            "log2FoldChange": [1.25],
            "padj": [0.01],
        }
    ).to_csv(source, index=False)
    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(source), "DESeq2 results (*.csv)"),
        )
        monkeypatch.setattr(
            window,
            "_ask_de_results_direction",
            lambda: ExternalDEImportDetails(
                "stimulated",
                "baseline",
                upstream_method="edgeR",
                lfc_shrinkage="not_applied",
                p_adjustment_method="Benjamini-Hochberg",
            ),
        )

        window._import_deseq2_results()

        provenance = window.config.input.deseq2_results_direction
        assert window.config.input.type == "deseq2_results"
        assert window.config.input.deseq2_results == "config/deseq2_results.csv"
        assert provenance.confirmed is True
        assert provenance.numerator == "stimulated"
        assert provenance.denominator == "baseline"
        assert provenance.confirmed_at
        file_provenance = window.config.input.deseq2_results_provenance
        assert file_provenance.original_basename == "external.csv"
        assert file_provenance.project_copy == "config/deseq2_results.csv"
        assert len(file_provenance.sha256 or "") == 64
        assert file_provenance.byte_size == source.stat().st_size
        assert file_provenance.row_count == 1
        assert file_provenance.column_names == [gene_header, "log2FoldChange", "padj"]
        assert file_provenance.upstream_method == "edgeR"
        assert file_provenance.lfc_shrinkage == "not_applied"
        assert file_provenance.p_adjustment_method == "Benjamini-Hochberg"
        assert (tmp_path / "config" / "deseq2_results.csv").read_bytes() == source.read_bytes()
        samples = pd.read_csv(tmp_path / "config" / "samples.tsv", sep="\t")
        assert list(samples.columns) == ["sample_id", "condition", "layout", "fastq_1"]
        assert samples.empty
        assert "positive log2FoldChange means higher in stimulated than baseline" in (
            window.input_preview.toPlainText()
        )
        window._update_workflow_summary()
        plan = window.workflow_summary.text()
        assert "upstream method edgeR" in plan
        assert "adjusted-p method Benjamini-Hochberg" in plan
        assert "BH FDR" not in plan
    finally:
        window.close()


def test_external_results_import_validates_rows_beyond_a_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "late-invalid.csv"
    pd.DataFrame({
        "gene_id": [f"g{i}" for i in range(7)],
        "log2FoldChange": ["1.0"] * 6 + ["2.0junk"],
        "padj": ["0.05"] * 7,
    }).to_csv(source, index=False)
    warnings: list[str] = []
    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(source), "Differential-expression results (*.csv)"),
        )
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda _parent, _title, message, *_args, **_kwargs: warnings.append(message),
        )
        monkeypatch.setattr(
            window, "_ask_de_results_direction",
            lambda: pytest.fail("Direction dialog must not open for an invalid full table"),
        )

        window._import_deseq2_results()

        assert any("complete numeric token" in warning for warning in warnings)
        assert window.config.input.type == "fastq"
        assert not (tmp_path / "config" / "deseq2_results.csv").exists()
    finally:
        window.close()


def test_external_results_preflight_detects_valid_content_and_checksum_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "external.csv"
    pd.DataFrame({
        "gene_id": ["g1", "g2"],
        "log2FoldChange": [1.0, -1.0],
        "padj": [0.01, 0.02],
    }).to_csv(source, index=False)
    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(source), "Differential-expression results (*.csv)"),
        )
        monkeypatch.setattr(
            window, "_ask_de_results_direction",
            lambda: ExternalDEImportDetails("case", "control"),
        )
        window._import_deseq2_results()

        initial = window._deseq2_results_preflight_messages()
        assert not [message for message in initial if message["status"] == "FAIL"]
        assert any("Validated the complete" in message["message"] for message in initial)

        # Keep the replacement table scientifically valid so this specifically tests
        # the independent import-integrity gate, not only schema/value validation.
        project_copy = tmp_path / "config" / "deseq2_results.csv"
        pd.DataFrame({
            "gene_id": ["g1", "g2"],
            "log2FoldChange": [2.0, -1.0],
            "padj": [0.01, 0.02],
        }).to_csv(project_copy, index=False)
        drifted = window._deseq2_results_preflight_messages()
        assert any(
            message["status"] == "FAIL" and "SHA-256 mismatch" in message["message"]
            for message in drifted
        )
    finally:
        window.close()


def test_tsv_source_remains_valid_after_copy_to_stable_project_csv_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "external.tsv"
    pd.DataFrame({
        "gene_id": ["g1", "g2"],
        "log2FoldChange": ["1.0", "NA"],
        "padj": ["0.01", ""],
    }).to_csv(source, sep="\t", index=False)
    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(source), "Differential-expression results (*.tsv)"),
        )
        monkeypatch.setattr(
            window, "_ask_de_results_direction",
            lambda: ExternalDEImportDetails("case", "control"),
        )
        window._import_deseq2_results()

        project_copy = tmp_path / "config" / "deseq2_results.csv"
        assert project_copy.read_bytes() == source.read_bytes()
        assert not [
            message for message in window._deseq2_results_preflight_messages()
            if message["status"] == "FAIL"
        ]
    finally:
        window.close()


def test_external_results_import_cancelled_without_direction_leaves_project_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "external.csv"
    source.write_text(
        "gene_id,log2FoldChange,padj\nGENE1,1.0,0.01\n", encoding="utf-8")
    window = _window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: (str(source), "DESeq2 results (*.csv)"),
        )
        monkeypatch.setattr(window, "_ask_de_results_direction", lambda: None)

        window._import_deseq2_results()

        assert window.config.input.type == "fastq"
        assert window.config.input.deseq2_results is None
        assert window.config.input.deseq2_results_direction.confirmed is False
        assert not (tmp_path / "config" / "deseq2_results.csv").exists()
        assert not (tmp_path / "config" / "samples.tsv").exists()
    finally:
        window.close()
