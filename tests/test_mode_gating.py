from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy  # noqa: E402

from app.core.config_models import Deseq2ResultsDirectionProvenance  # noqa: E402
from app.core.paths import app_root  # noqa: E402
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
    assert w.meta_analysis.isEnabled()  # count-based route -> meta available
    assert w.adv_group.isEnabled()
    assert all(c.isEnabled() for c in w.adv_alignment_widgets)
    assert all(c.isEnabled() for c in w.adv_de_widgets)

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
    assert not w.meta_analysis.isEnabled()  # microarray has no per-study count fan-out
    # Nothing in Advanced parameters applies: no reads to trim or align, and limma-trend
    # reads neither the DESeq2 count filter nor its shrinkage estimator.
    assert not w.adv_group.isEnabled()
    assert not any(c.isEnabled() for c in w.adv_alignment_widgets)
    assert not any(c.isEnabled() for c in w.adv_de_widgets)

    # count-matrix: no alignment, but the DE engine still runs on counts.
    w.config.input.type = "count_matrix"
    w._apply_input_mode_ui()
    assert not w.align_group.isEnabled()
    assert w.de_engine.isEnabled()
    assert w.gsva.isEnabled()
    assert w.meta_analysis.isEnabled()  # count matrix can carry a multi-study dataset column
    # The read-preparation / aligner parameters are dead here, but DESeq2 still reads its
    # count filter and shrinkage estimator, so the panel must not be greyed wholesale.
    assert w.adv_group.isEnabled()
    assert not any(c.isEnabled() for c in w.adv_alignment_widgets)
    assert all(c.isEnabled() for c in w.adv_de_widgets)
    assert not w.fastp_u.isEnabled()
    assert not w.star_twopass.isEnabled()
    assert w.de_min_count.isEnabled()

    # deseq2-results: DE is bypassed and there is no per-sample matrix. Local-model controls
    # disappear; the immutable imported direction replaces them while thresholds stay live.
    w.config.input.type = "deseq2_results"
    w.config.input.deseq2_results_direction = Deseq2ResultsDirectionProvenance(
        numerator="stimulated",
        denominator="baseline",
        confirmed=True,
        confirmed_at="2026-08-10T12:00:00Z",
    )
    w._apply_input_mode_ui()
    assert not w.align_group.isEnabled()
    assert not w.de_engine.isEnabled()
    assert w.de_engine.isHidden()
    assert not w.gsva.isEnabled()
    assert w.enrichment.isEnabled()
    assert not w.meta_analysis.isEnabled()  # a results table has no per-study counts
    assert w.workflow_comparison_factor_row.isHidden()
    assert w.numerator.isHidden()
    assert w.denominator.isHidden()
    assert w.workflow_direction_hint.isHidden()
    assert w.workflow_design_toggle.isHidden()
    assert w.workflow_design_options.isHidden()
    assert not w.design.isEnabled()
    assert not w.reference_level.isEnabled()
    assert not w.refresh_conditions_button.isEnabled()
    assert not w.design_helper_button.isEnabled()
    assert not w.de_min_count.isEnabled()
    assert not w.de_shrink.isEnabled()
    assert not w.adv_group.isEnabled()  # DE is bypassed and there are no reads
    assert not any(c.isEnabled() for c in w.adv_alignment_widgets)
    assert w.alpha.isEnabled()
    assert w.lfc_threshold.isEnabled()
    assert not w.external_de_direction_banner.isHidden()
    assert "higher expression in stimulated than in baseline" in (
        w.external_de_direction_banner.text()
    )
    assert "Project and data > Add data" in w.external_de_direction_banner.text()
    threshold_label = w.alpha_threshold_info.findChild(QLabel, "infoLabelText")
    assert threshold_label is not None
    assert threshold_label.text() == "Adjusted p-value"
    assert "not reported" in w.alpha.toolTip()
    w.config.input.deseq2_results_provenance.p_adjustment_method = "Benjamini-Hochberg"
    w._apply_input_mode_ui()
    assert threshold_label.text() == "BH FDR"
    w.config.input.deseq2_results_provenance.p_adjustment_method = "unknown"
    w._apply_input_mode_ui()
    assert threshold_label.text() == "Adjusted p-value"
    assert w.de_group.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
    external_height = w.workflow_section_tabs.maximumHeight()
    assert external_height <= 230
    assert w._active_contrast() == ("stimulated", "baseline")

    old_design = w.config.deseq2.design_formula
    old_reference = dict(w.config.deseq2.reference_level)
    old_contrast = w.config.deseq2.contrasts[0].model_copy(deep=True)
    old_direction = w.config.input.deseq2_results_direction.model_copy(deep=True)
    w.design.setText("~ ignored_batch + ignored_group")
    w.contrast_factor.setText("ignored_group")
    w.numerator.setCurrentText("wrong_numerator")
    w.denominator.setCurrentText("wrong_denominator")
    w.reference_level.setCurrentText("wrong_reference")
    w.alpha.setValue(0.01)
    w.lfc_threshold.setValue(1.5)
    assert w._save_workflow_settings()
    assert w.config.deseq2.design_formula == old_design
    assert w.config.deseq2.reference_level == old_reference
    assert w.config.deseq2.contrasts[0] == old_contrast
    assert w.config.input.deseq2_results_direction == old_direction
    assert w.config.deseq2.alpha == 0.01
    assert w.config.deseq2.lfc_threshold == 1.5

    # back to fastq: the alignment group re-enables and the trim->trimmer cascade is restored.
    w.config.input.type = "fastq"
    w._apply_input_mode_ui()
    assert w.align_group.isEnabled()
    assert w.aligner.isEnabled()
    assert not w.de_engine.isHidden()
    assert not w.workflow_comparison_factor_row.isHidden()
    assert not w.numerator.isHidden()
    assert not w.denominator.isHidden()
    assert not w.workflow_design_toggle.isHidden()
    assert w.design.isEnabled()
    assert w.reference_level.isEnabled()
    assert w.refresh_conditions_button.isEnabled()
    assert w.design_helper_button.isEnabled()
    assert w.de_min_count.isEnabled()
    assert w.de_shrink.isEnabled()
    assert w.adv_group.isEnabled()
    assert all(c.isEnabled() for c in w.adv_alignment_widgets)
    assert w.external_de_direction_banner.isHidden()
    assert threshold_label.text() == "BH FDR"
    assert w.contrast_info.isHidden()
    # Returning to a full analysis route must remove the compact external-results
    # cap.  The exact height is derived from the visible controls and available
    # viewport, so it must not be frozen to the former 520 px constant.
    assert w.workflow_section_tabs.maximumHeight() > external_height
    w.trim.setChecked(True)
    assert w.trimmer.isEnabled()
    w.trim.setChecked(False)
    assert not w.trimmer.isEnabled()
    w.close()


def test_friendly_phase_maps_every_rule_the_workflow_defines() -> None:
    # The run-phase banner must not call the reference download "Downloading sequencing data":
    # the specific download_genome/download_gtf entries have to win over the bare "download"
    # that catches download_fastq, and no entry may match a rule that does not exist.
    _app()
    w = MainWindow()
    assert w._friendly_phase("download_genome") == "Preparing the reference genome"
    assert w._friendly_phase("download_gtf") == "Preparing the reference genome"
    assert w._friendly_phase("download_fastq") == "Downloading sequencing data"
    assert w._friendly_phase("star_align") == "Aligning reads to the genome"
    assert w._friendly_phase("not_a_rule_at_all") is None
    # Every key must be reachable from a real rule name; the removed fasterq/prefetch entries
    # were dead because the workflow has no such rule.
    rules = set()
    sources = list((app_root() / "workflow" / "rules").glob("*.smk"))
    sources.append(app_root() / "workflow" / "Snakefile")
    for smk in sources:
        for line in smk.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("rule ") and stripped.endswith(":"):
                rules.add(stripped[len("rule "):-1].strip())
    assert "download_genome" in rules and "download_fastq" in rules
    unmatched = [key for key, _ in w._PHASE_BY_RULE
                 if not any(key in rule for rule in rules)]
    assert unmatched == []
    w.close()


def test_meta_analysis_checkbox_roundtrip() -> None:
    # The meta-analysis toggle must persist to config and hydrate back (no stale/unconnected control),
    # and 'comparative_meta' must be a selectable figure-override group.
    _app()
    w = MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("meta")
    w._create_benchmark_project("pasilla_paired_subset")

    assert ("comparative_meta", "Multi-study meta-analysis figures") in w.PALETTE_GROUPS

    w.meta_analysis.setChecked(True)
    # The GO-ontology combo is a meta-analysis dependant: live only while meta-analysis is on.
    assert w.meta_go_ontology.isEnabled()
    w.meta_go_ontology.setCurrentIndex(w.meta_go_ontology.findData("MF"))
    assert w._save_workflow_settings() is not False
    assert w.config.workflow.meta_analysis is True
    assert w.config.enrichment.go_ontology == "MF"
    assert w.manager.load_config(w.project_root).enrichment.go_ontology == "MF"

    w.config.workflow.meta_analysis = False
    w._populate_widgets_from_config()
    assert w.meta_analysis.isChecked() is False
    assert not w.meta_go_ontology.isEnabled()
    assert w.meta_go_ontology.currentData() == "MF"  # hydrated from config, not reset

    w.config.workflow.meta_analysis = True
    w.config.enrichment.go_ontology = "CC"
    w._populate_widgets_from_config()
    assert w.meta_analysis.isChecked() is True
    assert w.meta_go_ontology.isEnabled()
    assert w.meta_go_ontology.currentData() == "CC"
    w.close()
