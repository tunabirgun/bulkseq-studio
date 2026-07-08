from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import MainWindow  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_workflow_controls_greyed_by_input_mode() -> None:
    # The workflow settings the Snakemake DAG ignores in a given input mode must be greyed
    # in the GUI, and re-enabled (with the trim/rRNA/aligner cascades restored) on return.
    _app()
    w = MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("gating")
    w._create_benchmark_project("pasilla_paired_subset")  # fastq mode

    # fastq: the alignment/read-processing controls are live.
    w.config.input.type = "fastq"
    w._apply_input_mode_ui()
    assert w.align_group.isEnabled()
    assert w.de_engine.isEnabled()
    assert w.organellar.isEnabled()
    assert w.rseqc.isEnabled()
    assert w.gsva.isEnabled()

    # microarray: no alignment, limma-trend forced -> align group + de_engine + organellar +
    # rseqc greyed; enrichment/figures/gsva and the contrast builder stay live.
    w.config.input.type = "microarray"
    w._apply_input_mode_ui()
    assert not w.align_group.isEnabled()
    assert not w.aligner.isEnabled()  # child of the disabled group
    assert not w.de_engine.isEnabled()
    assert not w.organellar.isEnabled()
    assert not w.rseqc.isEnabled()
    assert w.gsva.isEnabled()
    assert w.enrichment.isEnabled()
    assert w.figures.isEnabled()
    assert w.numerator.isEnabled()
    assert w.design.isEnabled()

    # count-matrix: no alignment, but the DE engine still runs on counts.
    w.config.input.type = "count_matrix"
    w._apply_input_mode_ui()
    assert not w.align_group.isEnabled()
    assert w.de_engine.isEnabled()
    assert w.gsva.isEnabled()

    # deseq2-results: DE is bypassed and there is no per-sample matrix -> de_engine + gsva greyed.
    w.config.input.type = "deseq2_results"
    w._apply_input_mode_ui()
    assert not w.align_group.isEnabled()
    assert not w.de_engine.isEnabled()
    assert not w.gsva.isEnabled()
    assert w.enrichment.isEnabled()

    # back to fastq: the alignment group re-enables and the trim->trimmer cascade is restored.
    w.config.input.type = "fastq"
    w._apply_input_mode_ui()
    assert w.align_group.isEnabled()
    assert w.aligner.isEnabled()
    w.trim.setChecked(True)
    assert w.trimmer.isEnabled()
    w.trim.setChecked(False)
    assert not w.trimmer.isEnabled()
    w.close()
