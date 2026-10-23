from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pandas as pd
import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.core.config_models import default_config
from app.core.de_results import ExternalDEImportDetails
from app.core.metadata import save_metadata
from app.core.project import ProjectManager
from app.ui import main_window
from app.ui.main_window import MainWindow


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    for name in ("_refresh_phase_checks", "_refresh_gallery", "_refresh_resume_banner"):
        monkeypatch.setattr(MainWindow, name, lambda self: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    w = MainWindow()
    yield w
    w.runner = None
    w._run_active = False
    w.project_root = None
    w.close()
    app.processEvents()


def project(tmp_path: Path, name: str, sample: str | None = None) -> Path:
    root = tmp_path / name
    (root / "config").mkdir(parents=True)
    ProjectManager().save_config(root, default_config(name, root))
    if sample is not None:
        save_metadata(pd.DataFrame({"sample_id": [sample], "condition": ["control"]}),
                      root / "config" / "samples.tsv")
    return root


@pytest.mark.parametrize("broken", ["missing", "unreadable"])
def test_project_with_unusable_sheet_cannot_inherit_previous_samples(window, tmp_path, broken):
    first = project(tmp_path, "first", "sample_a")
    second = project(tmp_path, "second")
    sheet = second / "config" / "samples.tsv"
    if broken == "unreadable":
        sheet.write_bytes(b"\xff\xfe\xfa")
    window._load_project(first)
    window._load_project(second)
    assert window.project_root == second
    assert window.metadata_table.rowCount() == 0
    assert "sample" in window.metadata_messages.toPlainText().lower()
    assert "import" in window.metadata_messages.toPlainText().lower()
    window._save_metadata()
    assert "sample_a" not in sheet.read_text(encoding="utf-8")


def test_cancel_switch_keeps_sample_and_workflow_edits(window, tmp_path):
    first = project(tmp_path, "first", "sample_a")
    second = project(tmp_path, "second", "sample_b")
    window._load_project(first)
    window.metadata_table.item(0, 0).setText("edited_sample")
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    window._load_project(second)
    assert window.project_root == first
    assert window.metadata_table.item(0, 0).text() == "edited_sample"
    assert "edited_sample" not in (first / "config" / "samples.tsv").read_text()


def test_save_switch_persists_edits_to_original_project(window, tmp_path, monkeypatch):
    first = project(tmp_path, "first", "sample_a")
    second = project(tmp_path, "second", "sample_b")
    window._load_project(first)
    window.metadata_table.item(0, 0).setText("edited_sample")
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    expected_quality = window.fastp_q.value()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    window._load_project(second)
    assert window.project_root == second
    assert "edited_sample" in (first / "config" / "samples.tsv").read_text()
    assert ProjectManager().load_config(first).fastp.qualified_quality_phred == expected_quality
    assert "edited_sample" not in (second / "config" / "samples.tsv").read_text()


def test_failed_save_cancels_switch_and_retains_edits(window, tmp_path, monkeypatch):
    first = project(tmp_path, "first", "sample_a")
    second = project(tmp_path, "second", "sample_b")
    window._load_project(first)
    window.metadata_table.item(0, 0).setText("edited_sample")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    def fail_save(*args):
        raise OSError("synthetic save failure")
    monkeypatch.setattr(main_window, "save_metadata", fail_save)
    window._load_project(second)
    assert window.project_root == first
    assert window.metadata_table.item(0, 0).text() == "edited_sample"


def test_running_close_defaults_to_keep_running_before_shutdown(window, monkeypatch):
    calls = []
    window.runner = SimpleNamespace(is_running=lambda: True, stop=lambda: calls.append("stop"))
    monkeypatch.setattr(QMessageBox, "exec", lambda dialog: calls.append(dialog.defaultButton().text()))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window._closing
    assert calls == ["Keep running"]


def test_explicit_stop_and_close_stops_runner(window, monkeypatch):
    calls = []
    window.runner = SimpleNamespace(is_running=lambda: True, stop=lambda: calls.append("stop"))
    def choose_stop(dialog):
        next(button for button in dialog.buttons() if button.text() == "Stop run and close").click()
    monkeypatch.setattr(QMessageBox, "exec", choose_stop)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert window._closing
    assert calls == ["stop"]


def test_unsaved_close_cancel_keeps_window_and_run_open(window, tmp_path, monkeypatch):
    window._load_project(project(tmp_path, "first", "sample_a"))
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    calls = []
    window.runner = SimpleNamespace(is_running=lambda: True, stop=lambda: calls.append("stop"))
    def choose_stop(dialog):
        next(button for button in dialog.buttons() if button.text() == "Stop run and close").click()
    monkeypatch.setattr(QMessageBox, "exec", choose_stop)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window._closing
    assert not calls


def test_discard_switch_does_not_save_original_project(window, tmp_path, monkeypatch):
    first = project(tmp_path, "first", "sample_a")
    second = project(tmp_path, "second", "sample_b")
    original = (first / "config" / "config.yaml").read_bytes()
    window._load_project(first)
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    window._load_project(second)
    assert window.project_root == second
    assert (first / "config" / "config.yaml").read_bytes() == original


def test_saved_form_is_clean_but_other_form_edits_remain_dirty(window, tmp_path):
    window._load_project(project(tmp_path, "first", "sample_a"))
    assert not window._unsaved_project_sections()
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    window.cores.setValue(window.cores.value() + 1)
    window._save_resources()
    assert window._unsaved_project_sections() == ["workflow"]
    assert window._save_workflow_settings(validate=False)
    assert not window._unsaved_project_sections()


def test_save_then_reopen_same_project_uses_saved_config(window, tmp_path, monkeypatch):
    root = project(tmp_path, "first", "sample_a")
    window._load_project(root)
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    expected = window.fastp_q.value()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    window._load_project(root)
    assert window.fastp_q.value() == expected
    assert not window._unsaved_project_sections()


def test_configuration_save_failure_keeps_form_dirty(window, tmp_path, monkeypatch):
    first = project(tmp_path, "first", "sample_a")
    window._load_project(first)
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    def fail_save(*args):
        raise OSError("synthetic config failure")
    monkeypatch.setattr(window.manager, "save_config", fail_save)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window._closing
    assert window.project_root == first
    assert window._unsaved_project_sections() == ["workflow"]


@pytest.mark.parametrize("benchmark", [False, True])
def test_cancel_create_does_not_create_the_working_directory(window, tmp_path, benchmark):
    first = project(tmp_path, "first", "sample_a")
    window._load_project(first)
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    workdir = tmp_path / "new_working_directory"
    window.workdir.setText(str(workdir))
    window.use_wsl.setChecked(False)
    window.project_name.setText("cancelled_project")
    if benchmark:
        window._create_benchmark_project("pasilla_paired_subset")
    else:
        window._create_project()
    assert window.project_root == first
    assert not workdir.exists()


@pytest.mark.parametrize("failed_section,other_section", [
    ("workflow", "resources"), ("resources", "figures"), ("figures", "workflow"),
])
def test_failed_settings_save_cannot_leak_into_another_section_save(
        window, tmp_path, monkeypatch, failed_section, other_section):
    root = project(tmp_path, "first", "sample_a")
    window._load_project(root)
    original_config = window.config
    fields = {"workflow": (window.fastp_q, lambda config: config.fastp.qualified_quality_phred),
              "resources": (window.cores, lambda config: config.resources.total_threads),
              "figures": (window.fig_point_size, lambda config: config.figures_style.point_size)}
    savers = {"workflow": lambda: window._save_workflow_settings(validate=False),
              "resources": window._save_resources, "figures": window._apply_figure_style}
    edited, read_edited = fields[failed_section]
    original_value = read_edited(window.manager.load_config(root))
    edited.setValue(edited.value() + 1)
    draft_value = edited.value()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    def fail_save(*args):
        raise OSError("synthetic config failure")
    with monkeypatch.context() as failure:
        failure.setattr(window.manager, "save_config", fail_save)
        assert not window._confirm_unsaved_changes()
    assert window.config is original_config
    assert edited.value() == draft_value
    other, read_other = fields[other_section]
    other.setValue(other.value() + 1)
    other_value = other.value()
    savers[other_section]()
    saved = window.manager.load_config(root)
    assert read_edited(saved) == original_value
    assert read_other(saved) == other_value
    assert window.config is original_config
    assert window._unsaved_project_sections() == [failed_section]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    window._load_project(root)
    assert read_edited(window.config) == original_value
    assert read_other(window.config) == other_value


def test_later_save_failure_keeps_the_successfully_saved_section(window, tmp_path, monkeypatch):
    root = project(tmp_path, "first", "sample_a")
    window._load_project(root)
    original_config = window.config
    original_threads = window.config.resources.total_threads
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    expected_quality = window.fastp_q.value()
    window.cores.setValue(window.cores.value() + 1)
    persist = window.manager.save_config
    def fail_resources(target, config):
        if config.resources.total_threads != original_threads:
            raise OSError("synthetic resource failure")
        persist(target, config)
    monkeypatch.setattr(window.manager, "save_config", fail_resources)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    assert not window._confirm_unsaved_changes()
    saved = window.manager.load_config(root)
    assert saved.fastp.qualified_quality_phred == expected_quality
    assert window.config.fastp.qualified_quality_phred == expected_quality
    assert saved.resources.total_threads == original_threads
    assert window.config.resources.total_threads == original_threads
    assert window.config is original_config
    assert window._unsaved_project_sections() == ["resources"]


@pytest.mark.parametrize("action", [
    "genes_new", "genes_existing", "accessions", "custom_reference", "preset_reference", "ppi",
    "micro_options", "input_mode", "geo", "sra", "fastqs", "counts", "microarray", "de_results",
])
def test_every_remaining_config_writer_rolls_back_failed_saves(window, tmp_path, monkeypatch, action):
    root = project(tmp_path, "first", "sample_a")
    window._load_project(root)
    for name in ("genes_of_interest.txt", "sra_accessions.txt", "samples.auto_generated.tsv",
                 "counts_matrix.txt", "microarray_expression.tsv", "deseq2_results.csv"):
        (root / "config" / name).write_text("original content\n", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references" / "project_reference.lock.yaml").write_text("locked: false\n", encoding="utf-8")
    samples = pd.DataFrame({"sample_id": ["imported_sample"], "condition": ["control"],
                            "layout": ["single"], "read_count": ["10"], "organism": [""]})
    source = tmp_path / "matrix.tsv"
    source.write_text("gene_id\tsample_x\tsample_y\ngene_a\t10\t20\ngene_b\t30\t40\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    if action.startswith("genes_"):
        if action == "genes_existing":
            window.config.gene_sets.custom_gene_list = "config/genes_of_interest.txt"
        window.goi_box.setPlainText("edited_gene")
        invoke = window._persist_goi
    elif action == "accessions":
        window.sra_box.setPlainText("SRR_SYNTHETIC")
        invoke = window._save_sra
    elif action == "custom_reference":
        genome, annotation = tmp_path / "reference.fa", tmp_path / "reference.gtf"
        genome.write_text(">synthetic\nACGT\n", encoding="utf-8")
        annotation.write_text("synthetic annotation\n", encoding="utf-8")
        window.ref_genome.setText(str(genome))
        window.ref_annotation.setText(str(annotation))
        window.ref_organism.setText("Synthetic organism")
        monkeypatch.setattr(main_window, "validate_reference", lambda *args: [])
        invoke = window._use_custom_reference
    elif action == "preset_reference":
        window.reference_list.setCurrentRow(0)
        invoke = window._select_reference
    elif action == "ppi":
        rds = root / "results" / "deseq2" / "deseq2_objects.rds"
        rds.parent.mkdir(parents=True)
        rds.write_bytes(b"synthetic placeholder")
        window.ppi_score.setValue(window.ppi_score.value() + 1)
        monkeypatch.setattr(window, "_start_snakemake", lambda mode: pytest.fail("A failed save started a run"))
        invoke = window._regenerate_ppi
    elif action == "micro_options":
        window.micro_log2.blockSignals(True)
        window.micro_log2.setCurrentIndex(window.micro_log2.findData("yes"))
        window.micro_log2.blockSignals(False)
        invoke = window._on_micro_option_changed
    elif action == "input_mode":
        window.config.enrichment.keytype = "SYMBOL"
        invoke = window._apply_input_mode_ui
    elif action == "geo":
        invoke = lambda: window._on_geo_fetched("GSE_SYNTHETIC", {"is_microarray": True, "samples": samples})
    elif action == "sra":
        monkeypatch.setattr(main_window, "metadata_to_samples", lambda data: samples)
        invoke = lambda: window._on_sra_fetched(["SRR_SYNTHETIC"], object())
    elif action == "fastqs":
        window.config.input.type = "sra"
        window.use_wsl.setChecked(False)
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(tmp_path / "sample_x.fastq")], ""))
        invoke = window._select_fastqs
    elif action == "counts":
        invoke = window._import_count_matrix
    elif action == "microarray":
        invoke = window._import_microarray_matrix
    else:
        source.write_text("gene_id\tlog2FoldChange\tpadj\ngene_a\t1.5\t0.01\n", encoding="utf-8")
        monkeypatch.setattr(window, "_ask_de_results_direction",
                            lambda: ExternalDEImportDetails("case", "control", "limma", "not_applied", "BH"))
        invoke = window._import_deseq2_results
    window.manager.save_config(root, window.config)
    original_config = window.config
    before_model = window.config.model_dump()
    before_files = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    before_table = window.metadata_table.to_dataframe()
    attempts = []
    def fail_save(target, candidate):
        attempts.append((candidate.model_dump(), {name: (root / name).read_bytes() for name in before_files}))
        (target / "config" / "config.yaml").write_text("partial failed write", encoding="utf-8")
        raise OSError("synthetic config failure")
    with monkeypatch.context() as failure:
        failure.setattr(window.manager, "save_config", fail_save)
        try:
            invoke()
        except OSError:
            pass
    assert attempts and (attempts[0][0] != before_model or attempts[0][1] != before_files)
    assert window.config is original_config
    assert window.config.model_dump() == before_model
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before_files
    pd.testing.assert_frame_equal(window.metadata_table.to_dataframe(), before_table)
    window.cores.setValue(window.cores.value() + 1)
    expected_threads = window.cores.value()
    window._save_resources()
    saved = window.manager.load_config(root).model_dump()
    assert saved["resources"]["total_threads"] == expected_threads
    for name in before_model.keys() - {"resources", "rule_threads", "rule_memory_gb"}:
        assert saved[name] == before_model[name]
    assert window.config is original_config


def test_failed_new_gene_list_save_removes_the_uncommitted_file(window, tmp_path, monkeypatch):
    root = project(tmp_path, "first", "sample_a")
    window._load_project(root)
    window.goi_box.setPlainText("new_gene")
    def fail_save(*args):
        raise OSError("synthetic config failure")
    monkeypatch.setattr(window.manager, "save_config", fail_save)
    with pytest.raises(OSError, match="synthetic config failure"):
        window._persist_goi()
    assert not (root / "config" / "genes_of_interest.txt").exists()
    assert window.config.gene_sets.custom_gene_list is None
    assert window.goi_box.toPlainText() == "new_gene"
    assert window._unsaved_project_sections() == ["genes"]


def test_failed_rollback_retains_recovery_copies_and_reports_it(window, tmp_path, monkeypatch):
    root = project(tmp_path, "first", "sample_a")
    window._load_project(root)
    genes = root / "config" / "genes_of_interest.txt"
    genes.write_text("original_gene\n", encoding="utf-8")
    window.goi_box.setPlainText("edited_gene")
    copy_file = main_window.shutil.copy2
    def fail_restore(source, target, *args, **kwargs):
        if Path(source).name.startswith("old-"):
            raise OSError("synthetic restore failure")
        return copy_file(source, target, *args, **kwargs)
    def fail_save(*args):
        raise OSError("synthetic config failure")
    monkeypatch.setattr(main_window.shutil, "copy2", fail_restore)
    monkeypatch.setattr(window.manager, "save_config", fail_save)
    with pytest.raises(OSError, match="Original copies remain"):
        window._persist_goi()
    retained = list(root.glob(".project-save-*/old-*"))
    assert any(path.read_text(encoding="utf-8") == "original_gene\n" for path in retained)
    assert window.config.gene_sets.custom_gene_list is None


def test_failed_microarray_option_save_keeps_the_visible_edit_dirty(window, tmp_path, monkeypatch):
    window._load_project(project(tmp_path, "first", "sample_a"))
    window.micro_log2.blockSignals(True)
    window.micro_log2.setCurrentIndex(window.micro_log2.findData("yes"))
    window.micro_log2.blockSignals(False)
    def fail_save(*args):
        raise OSError("synthetic config failure")
    monkeypatch.setattr(window.manager, "save_config", fail_save)
    with pytest.raises(OSError):
        window._on_micro_option_changed()
    assert window.micro_log2.currentData() == "yes"
    assert window.config.microarray.log2_transform == "auto"
    assert window._unsaved_project_sections() == ["microarray"]


def test_figure_section_navigation_is_not_an_unsaved_setting(window, tmp_path):
    window._load_project(project(tmp_path, "first", "sample_a"))
    window.fig_override_group_pick.setCurrentIndex(1)
    assert not window._unsaved_project_sections()


def test_saved_figure_edits_clear_only_the_figure_section(window, tmp_path):
    window._load_project(project(tmp_path, "first", "sample_a"))
    window.fig_point_size.setValue(window.fig_point_size.value() + 1)
    window.fastp_q.setValue(window.fastp_q.value() + 1)
    assert set(window._unsaved_project_sections()) == {"figures", "workflow"}
    assert window._apply_figure_style()
    assert window._unsaved_project_sections() == ["workflow"]


@pytest.mark.parametrize("route", ["geo", "sra"])
@pytest.mark.parametrize("outcome", ["done", "failed"])
@pytest.mark.parametrize("transition", ["switch", "reopen", "stay"])
def test_import_callback_is_bound_to_its_original_project_session(
        window, tmp_path, monkeypatch, route, outcome, transition):
    callbacks = {}
    class DeferredWorker:
        def __init__(self, work):
            self.done = SimpleNamespace(connect=lambda callback: callbacks.update(done=callback))
            self.failed = SimpleNamespace(connect=lambda callback: callbacks.update(failed=callback))
        def start(self):
            pass
        def isRunning(self):
            return False
    monkeypatch.setattr(main_window, "BackgroundWorker", DeferredWorker)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    first = project(tmp_path, "first", "sample_a")
    second = project(tmp_path, "second", "sample_b")
    window._load_project(first)
    samples = pd.DataFrame({"sample_id": ["late_sample"], "condition": ["control"],
                            "layout": ["single"], "read_count": ["10"], "organism": [""]})
    if route == "geo":
        window.gse_box.setText("GSE_SYNTHETIC")
        window._fetch_geo_series()
        result = {"is_microarray": True, "samples": samples}
    else:
        window.sra_box.setPlainText("SRR_SYNTHETIC")
        window._fetch_sra_metadata()
        monkeypatch.setattr(main_window, "metadata_to_samples", lambda value: samples)
        result = object()
    target = second if transition == "switch" else first
    if transition != "stay":
        window._load_project(target)
    before = {path.name: path.read_bytes() for path in (target / "config").iterdir()}
    table = window.metadata_table.to_dataframe()
    preview = window.input_preview.toPlainText()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    callbacks[outcome](result if outcome == "done" else OSError("synthetic import failure"))
    if transition == "stay" and outcome == "done":
        assert "late_sample" in (target / "config" / "samples.tsv").read_text()
        assert window.metadata_table.item(0, 0).text() == "late_sample"
        assert window.config.input.type == ("microarray" if route == "geo" else "sra")
        if route == "sra":
            assert "accessions" not in window._unsaved_project_sections()
    else:
        assert {path.name: path.read_bytes() for path in (target / "config").iterdir()} == before
        pd.testing.assert_frame_equal(window.metadata_table.to_dataframe(), table)
    if transition != "stay":
        assert window.input_preview.toPlainText() == preview
    assert bool(warnings) == (transition == "stay" and outcome == "failed")
