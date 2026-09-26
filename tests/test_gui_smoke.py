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
    # Estimation now runs off the UI thread (it detects the local WSL cores/RAM so
    # the estimate reflects this machine); wait for the worker before asserting.
    est_worker = getattr(window, "_estimate_worker", None)
    if est_worker is not None:
        est_worker.wait(60000)
    QApplication.processEvents()
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


def test_multi_contrast_message_states_only_the_first_is_analysed() -> None:
    # S4: workflow/rules/deseq2.smk reads only contrasts[0]; the message shown for a
    # hand-edited multi-contrast config (config/contrasts.yaml) must say the others are not
    # analysed by the run, not just that they are "preserved on save".
    from app.core.config_models import Contrast

    _app()
    window = MainWindow()
    workdir = Path("manual_test_gui") / uuid4().hex
    window.workdir.setText(str(workdir))
    window.project_name.setText("multi_contrast")
    window._create_benchmark_project("pasilla_paired_subset")

    window.config.deseq2.contrasts = [
        Contrast(name="a_vs_b", factor="condition", numerator="a", denominator="b"),
        Contrast(name="c_vs_d", factor="condition", numerator="c", denominator="d"),
    ]
    window._populate_widgets_from_config()

    assert not window.contrast_info.isHidden()
    text = window.contrast_info.text().lower()
    assert "only this first contrast is analysed by the run" in text
    assert "c vs d" in text
    window.close()


def test_ppi_status_reconciles_after_the_viewer_prunes_to_its_budget() -> None:
    # I2: viewer.js prunes the interactive display to its node-display budget (currently 300)
    # by degree; the status line -- built synchronously from the Python-side (full) counts --
    # must be corrected once the async render callback reports how many were actually drawn.
    _app()
    window = MainWindow()

    window._ppi_full_counts = (812, 2400)
    window._ppi_drawn_counts = (812, 2400)
    window._on_ppi_rendered('{"nodes": 300, "edges": 900}', 812, 2400)

    assert window._ppi_drawn_counts == (300, 900)
    assert window.ppi_status.text() == (
        "Showing the 300 most connected of 812 proteins (900 of 2400 interactions). "
        "Hover a protein for details; click to highlight its neighbours; drag and scroll "
        "to explore.")
    assert window._ppi_pruned_caption() == (
        " — showing the 300 most connected of 812 proteins (900 of 2400 interactions)")

    # No pruning (drawn == full): the status line is left alone and the export caption
    # (reused by _save_ppi_export) is empty.
    window.ppi_status.setText("unchanged")
    window._on_ppi_rendered('{"nodes": 812, "edges": 2400}', 812, 2400)
    assert window.ppi_status.text() == "unchanged"
    assert window._ppi_pruned_caption() == ""
    window.close()


def test_a_stored_config_carrying_the_removed_hub_label_count_still_opens() -> None:
    # ppi.hub_label_count was dropped once the static network figure stopped drawing labels.
    # A project written by an older version still carries it; loading must ignore the extra key
    # rather than refuse the project.
    import yaml

    from app.core.config_models import AppConfig, PpiConfig

    _app()
    window = MainWindow()
    workdir = Path("manual_test_gui") / uuid4().hex
    window.workdir.setText(str(workdir))
    window.project_name.setText("legacy_ppi")
    window._create_benchmark_project("pasilla_paired_subset")
    root = window.project_root
    assert root is not None
    assert not hasattr(PpiConfig(), "hub_label_count")

    config_path = root / "config" / "config.yaml"
    stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    stored["ppi"]["hub_label_count"] = 15
    config_path.write_text(yaml.safe_dump(stored, sort_keys=False), encoding="utf-8")
    assert AppConfig.model_validate(stored).ppi.score_threshold == stored["ppi"]["score_threshold"]

    window._load_project(root)
    assert window.config is not None
    assert not hasattr(window.config.ppi, "hub_label_count")
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


def test_reopen_project_and_recent_projects(monkeypatch) -> None:
    # Reopening an existing project must restore full GUI state (config, samples table, widgets),
    # remember it in Recent projects, and set the "Open project" start folder to where it lives
    # (not the app's install/AppData dir). Covers the bug where Open project defaulted to AppData.
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox
    # Modal dialogs block forever under the offscreen/headless platform; make them no-ops so the
    # "reject a non-project folder" path (which pops a warning) can be exercised without hanging.
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    app = _app()
    # Isolate QSettings to a throwaway scope so the test doesn't touch the real recent list.
    prev = (app.organizationName(), app.applicationName())
    app.setOrganizationName("BulkSeqTest")
    app.setApplicationName("reopen_" + uuid4().hex)
    try:
        QSettings().clear()
        window = MainWindow()
        workdir = Path("manual_test_gui") / uuid4().hex
        window.workdir.setText(str(workdir))
        window.project_name.setText("reopen_me")
        window._create_benchmark_project("pasilla_paired_subset")
        root = window.project_root
        assert root is not None

        # Simulate a fresh open of the existing project (as "Open project" -> _load_project does).
        window.project_root = None
        window._load_project(root)
        assert window.project_root == root
        assert window.config is not None
        assert window.metadata_table.rowCount() == 4                       # samples reloaded
        assert window.design.text() == window.config.deseq2.design_formula  # widgets reflect config

        # The reopen wired the picker start folder (the bug fix) and the recent list.
        assert str(QSettings().value("last_project_dir", "")) == str(root.parent)
        window._refresh_recent_projects()
        recent_texts = [window.recent_pick.itemText(i) for i in range(window.recent_pick.count())]
        assert any(Path(t) == root for t in recent_texts), f"{root} not in recent {recent_texts}"

        # The Recent-projects "Open recent" path reloads it with full state.
        window.recent_pick.setCurrentText(str(root))
        window.project_root = None
        window._open_recent_project()
        assert window.project_root == root
        assert window.metadata_table.rowCount() == 4

        # Opening a non-project folder is rejected without corrupting the current project.
        notproj = workdir.resolve() / "not_a_project"
        notproj.mkdir(parents=True, exist_ok=True)
        window._load_project(notproj)
        assert window.project_root == root  # unchanged
        window.close()
    finally:
        QSettings().clear()
        app.setOrganizationName(prev[0])
        app.setApplicationName(prev[1])


def test_env_broken_run_offers_rebuild() -> None:
    # An R-load failure in the run output flags the environment broken and, at run end, offers a
    # one-click rebuild; a generic setup/design error must NOT (it is not an environment problem,
    # so offering "rebuild the environment" would mislead).
    _app()
    window = MainWindow()
    window._env_broken_detected = False

    # A bad-contrast / generic setup error is not an environment problem.
    window._on_run_line("PROJECT SETUP ERROR: The design uses 'control' for 'condition', but the "
                        "sample sheet has no such value.")
    assert window._env_broken_detected is False

    # The R load-test failure message (and a raw R load error) do flag it.
    window._on_run_line("PROJECT SETUP ERROR: These required R/Bioconductor packages will not load "
                        "in the bulkseq env: clusterProfiler,GO.db,DOSE,enrichplot.")
    assert window._env_broken_detected is True
    window._env_broken_detected = False
    window._on_run_line("Error: package or namespace load failed: there is no package called 'GO.db'")
    assert window._env_broken_detected is True

    # At run end, a failure carrying the env-broken flag routes to the rebuild offer.
    called = {"n": 0}
    window._offer_env_rebuild = lambda: called.__setitem__("n", called["n"] + 1)  # type: ignore[method-assign]
    window._stop_in_progress = False
    window._run_mode = None
    window._on_run_finished(1)
    QApplication.processEvents()
    assert called["n"] == 1

    # A failure WITHOUT the env-broken flag must not offer a rebuild.
    window._env_broken_detected = False
    window._stop_in_progress = False
    window._run_mode = None
    window._on_run_finished(1)
    QApplication.processEvents()
    assert called["n"] == 1
    window.close()


def test_run_failure_expands_execution_details_and_scrolls_to_the_error_line() -> None:
    # I1: the "Error in rule" hint lives inside a log panel that starts collapsed (the "Show
    # command and log" disclosure). On failure the panel must expand itself and the cursor must
    # already sit on the error line, so the cause is visible without the user scrolling by hand.
    _app()
    window = MainWindow()
    assert window.execution_details_toggle.isChecked() is False

    for i in range(30):
        window._on_run_line(f"padding line {i}")
    window._on_run_line("Error in rule deseq2:")
    window._on_run_line("    jobid: 3")
    for i in range(30):
        window._on_run_line(f"trailer line {i}")
    assert window._run_error_detected is True

    window._stop_in_progress = False
    window._run_mode = None
    window._on_run_finished(1)
    QApplication.processEvents()

    assert window.execution_details_toggle.isChecked() is True
    assert "Error in rule" in window.log_text.textCursor().block().text()
    window.close()


def test_run_failure_without_error_in_rule_text_still_scrolls_to_the_true_cause() -> None:
    # A run can fail with only a different marker present (no literal "Error in rule" line
    # anywhere in the real log). The hint text appended afterwards contains the phrase "Error in
    # rule" itself, so the scroll target must be computed before that hint is appended, or the
    # cursor would land on our own hint instead of the actual cause.
    _app()
    window = MainWindow()

    for i in range(10):
        window._on_run_line(f"padding line {i}")
    window._on_run_line("Exiting because a job execution failed.")
    for i in range(10):
        window._on_run_line(f"trailer line {i}")
    assert window._run_error_detected is True

    window._stop_in_progress = False
    window._run_mode = None
    window._on_run_finished(1)
    QApplication.processEvents()

    assert window.execution_details_toggle.isChecked() is True
    assert "Exiting because a job execution failed" in window.log_text.textCursor().block().text()
    window.close()


def test_run_failure_scroll_does_not_find_previous_run_error() -> None:
    # I1: after two consecutive failed runs, the cursor should land on the current run's
    # error, not the previous run's error. _scroll_log_to_first_error must start searching
    # from _run_log_start (the character position when the run began), not from the document start.
    _app()
    window = MainWindow()

    # First failed run
    for i in range(10):
        window._on_run_line(f"padding line {i}")
    window._on_run_line("Error in rule first_rule:")
    window._on_run_line("    jobid: 1")
    for i in range(10):
        window._on_run_line(f"trailer line {i}")
    assert window._run_error_detected is True

    window._stop_in_progress = False
    window._run_mode = None
    window._on_run_finished(1)
    QApplication.processEvents()

    assert "first_rule" in window.log_text.textCursor().block().text()

    # Simulate the start of a second run: set the log start marker as _start_snakemake_impl does
    window._run_log_start = window.log_text.document().characterCount() - 1
    window._run_error_detected = False

    # Second failed run
    window._on_run_line("Error in rule second_rule:")
    window._on_run_line("    jobid: 2")
    for i in range(10):
        window._on_run_line(f"trailer line {i}")
    assert window._run_error_detected is True

    window._stop_in_progress = False
    window._run_mode = None
    window._on_run_finished(1)
    QApplication.processEvents()

    # The cursor should be on the second error, not the first
    assert "second_rule" in window.log_text.textCursor().block().text()
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


def test_microarray_symbol_keytype_cleared_on_switch_to_count_route() -> None:
    # A microarray-only enrichment keytype='SYMBOL' must not leak into a count-based route, where it
    # would override the organism's ENSEMBL default and mis-map ids. Central guard in _apply_input_mode_ui.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path
    from uuid import uuid4
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("kt")
    w._create_benchmark_project("pasilla_paired_subset")
    w.config.enrichment.keytype = "SYMBOL"
    w.config.input.type = "microarray"
    w._apply_input_mode_ui()
    assert w.config.enrichment.keytype == "SYMBOL"       # microarray keeps it
    w.config.input.type = "fastq"
    w._apply_input_mode_ui()
    assert w.config.enrichment.keytype is None            # cleared for the count route
    w.close()


def test_goi_blocked_in_deseq2_results_mode(monkeypatch, tmp_path) -> None:
    # A DESeq2-results upload has no per-sample counts (synthetic RDS: dds=vsd=NULL), so "Generate
    # genes-of-interest" must be gated in the GUI, not launch make_goi.R and crash on colData(NULL).
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path
    from uuid import uuid4
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui import main_window as mw
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    w = mw.MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("goigate")
    w._create_benchmark_project("pasilla_paired_subset")
    launched = []
    monkeypatch.setattr(w, "_start_snakemake", lambda mode: launched.append(mode))
    # a gene + an existing rds so the earlier guards pass; the mode gate is what must stop it
    w.goi_box.setPlainText("FBgn0025111")
    rds = w.project_root / "results" / "deseq2" / "deseq2_objects.rds"
    rds.parent.mkdir(parents=True, exist_ok=True)
    rds.write_text("x")
    w.config.input.type = "deseq2_results"
    w._generate_goi()
    assert launched == [], "GOI must NOT launch in deseq2_results mode"
    # a count-based mode with the same setup DOES launch
    w.config.input.type = "count_matrix"
    w._generate_goi()
    assert launched == ["goi"], f"GOI should launch for count_matrix, got {launched}"
    w.close()


def test_resume_banner_and_new_figure_controls(monkeypatch, tmp_path) -> None:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path
    from uuid import uuid4
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("resume")
    w._create_benchmark_project("pasilla_paired_subset")
    # new volcano-scale + sample-label controls round-trip through the config
    w.fig_volcano_yscale.setCurrentIndex(w.fig_volcano_yscale.findData("full"))
    w.fig_sample_labels.setChecked(False)
    # meta-analysis figure detail: exposed spinboxes must reach figures_style and hydrate back
    w.fig_meta_label_top.setValue(4)
    w.fig_meta_heatmap_top.setValue(25)
    w.fig_meta_enrich_show.setValue(9)
    w._apply_figure_style()
    assert w.config.figures_style.volcano_y_scale == "full"
    assert w.config.figures_style.sample_labels is False
    assert (w.config.figures_style.meta_label_top,
            w.config.figures_style.meta_heatmap_top,
            w.config.figures_style.meta_enrich_show_category) == (4, 25, 9)
    reloaded = w.manager.load_config(w.project_root)
    assert (reloaded.figures_style.meta_label_top,
            reloaded.figures_style.meta_heatmap_top,
            reloaded.figures_style.meta_enrich_show_category) == (4, 25, 9)
    w.fig_meta_label_top.setValue(1)
    w._populate_widgets_from_config()
    assert w.fig_meta_label_top.value() == 4
    assert w.fig_meta_heatmap_top.value() == 25
    assert w.fig_meta_enrich_show.value() == 9
    assert not w.fig_volcano_ycap.isEnabled()      # y-cap greyed out in non-cap mode
    # resume banner: seed an incomplete-run marker, refresh, assert it surfaces
    (w.project_root / ".snakemake" / "incomplete").mkdir(parents=True, exist_ok=True)
    (w.project_root / ".snakemake" / "incomplete" / "x").write_text("1")
    w._refresh_resume_banner()
    # isHidden() reflects the explicit setVisible() state without needing the window shown.
    assert not w.resume_banner.isHidden() and not w.resume_button.isHidden()
    # clear it -> banner hides
    (w.project_root / ".snakemake" / "incomplete" / "x").unlink()
    w._refresh_resume_banner()
    assert w.resume_banner.isHidden()
    w.close()


def test_term_picker_reads_every_gene_list_csv_and_splits_joined_symbols(tmp_path) -> None:
    # The per-ontology GO tables and the custom gene-set results carry the same enrichResult /
    # gseaResult columns as the combined ones, so they must be offered too. And run_enrichment.R
    # joins multi-symbol entrez rows with ";", which must not block the id_map bridge.
    _app()
    w = MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("terms")
    w._create_benchmark_project("pasilla_paired_subset")
    enr = w.project_root / "results" / "enrichment"
    enr.mkdir(parents=True, exist_ok=True)
    (enr / "go_ora_MF.csv").write_text(
        "ID,Description,p.adjust,Count,geneID\nGO:1,binding,0.001,2,GENA/GENB\n", encoding="utf-8")
    (enr / "go_ora_CC.csv").write_text(
        "ID,Description,p.adjust,Count,geneID\nGO:2,membrane,0.002,1,GENA\n", encoding="utf-8")
    (enr / "custom_ora.csv").write_text(
        "ID,Description,p.adjust,Count,geneID\nSET1,my set,0.003,1,GENB\n", encoding="utf-8")
    (enr / "custom_gsea.csv").write_text(
        "ID,Description,p.adjust,setSize,core_enrichment\nSET2,my other set,0.004,2,GENA/GENB\n",
        encoding="utf-8")
    w._populate_term_picker()
    offered = {w.term_pick.itemData(i)["csv"] for i in range(w.term_pick.count())
               if isinstance(w.term_pick.itemData(i), dict)}
    assert offered == {
        "results/enrichment/go_ora_MF.csv",
        "results/enrichment/go_ora_CC.csv",
        "results/enrichment/custom_ora.csv",
        "results/enrichment/custom_gsea.csv",
    }

    # id_map bridge: entrez 100 maps to the joined symbol "GENA;GENB"; either token must resolve.
    des = w.project_root / "results" / "deseq2"
    des.mkdir(parents=True, exist_ok=True)
    (des / "deseq2_results.csv").write_text(
        "gene_id,symbol,log2FoldChange,padj\n"
        "FBgn1,GENB,1.0,0.01\n"
        "FBgn2,GENC,-2.0,\n",
        encoding="utf-8")
    (enr / "id_map.csv").write_text(
        "gene_id,base_id,symbol,entrez\n"
        "FBgn1,FBgn1,GENA;GENB,100\n"
        "FBgn2,FBgn2,GENC,200\n",
        encoding="utf-8")
    sub, unmatched = w._resolve_term_genes(["100", "200"])
    assert unmatched == 0 and list(sub["gene_id"]) == ["FBgn1", "FBgn2"]
    w.close()


def test_count_matrix_import_never_leaves_an_unbalanced_override_cursor(monkeypatch, tmp_path) -> None:
    # Every exit path pushes and pops exactly one override cursor, and no dialog is raised while
    # the wait cursor is up. An extra restoreOverrideCursor() pops a cursor this method never
    # pushed, which unsets a wait cursor some other operation owns.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    _app()
    w = MainWindow()
    w.workdir.setText(str(Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("counts")
    w._create_benchmark_project("pasilla_paired_subset")

    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a[2])))
    chosen: dict[str, str] = {}
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (chosen["path"], "")))

    def _import(path: Path) -> None:
        # A sentinel cursor owned by an imaginary outer operation: an extra
        # restoreOverrideCursor() pops THAT one (restoring an empty stack is a silent no-op,
        # so only a pre-existing cursor exposes the imbalance).
        QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
        chosen["path"] = str(path)
        w._import_count_matrix()
        current = QApplication.overrideCursor()
        assert current is not None and current.shape() == Qt.CursorShape.CrossCursor, (
            f"the caller's override cursor was popped during {path.name}")
        QApplication.restoreOverrideCursor()
        assert QApplication.overrideCursor() is None, f"cursor left set after {path.name}"

    _import(tmp_path / "missing.csv")
    assert warnings and warnings[-1].startswith("Could not read the matrix")

    one_col = tmp_path / "one_column.csv"
    one_col.write_text("gene_id\nG1\n", encoding="utf-8")
    _import(one_col)
    assert "gene-id column plus at least one sample" in warnings[-1]

    invalid = tmp_path / "invalid.csv"
    invalid.write_text("gene_id,s1,s2\nG1,10,20\nG2,30,oops\n", encoding="utf-8")
    _import(invalid)
    assert "numeric" in warnings[-1].lower()
    assert w.config.input.type != "count_matrix"
    assert not (w.project_root / "config" / "counts_matrix.txt").exists()

    negative = tmp_path / "negative.csv"
    negative.write_text("gene_id,s1,s2\nG1,10,20\nG2,-0.1,40\n", encoding="utf-8")
    _import(negative)
    assert "negative" in warnings[-1].lower()
    assert w.config.input.type != "count_matrix"
    assert not (w.project_root / "config" / "counts_matrix.txt").exists()

    prior = w.project_root / "config" / "counts_matrix.txt"
    prior.write_bytes(b"prior count matrix bytes\n")
    w.config.input.type = "count_matrix"
    w.config.input.count_matrix = "config/counts_matrix.txt"
    w.config.input.estimated_counts = True

    frac = tmp_path / "single_fractional.csv"
    frac.write_text(
        "gene_id,s1,s2\nG1,1.5,2\nG2,3,4\nG3,5,6\nG4,7,8\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    _import(frac)
    assert w.config.input.type == "count_matrix"      # prior import preserved on cancellation
    assert w.config.input.estimated_counts is True
    assert prior.read_bytes() == b"prior count matrix bytes\n"

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    _import(frac)
    assert w.config.input.type == "count_matrix"
    assert w.config.input.estimated_counts is True    # RSEM/tximport estimated counts

    integer = tmp_path / "million_total_counts.csv"
    integer.write_text(
        "gene_id,s1,s2\nG1,600000,250000\nG2,400000,750000\n",
        encoding="utf-8",
    )
    _import(integer)
    assert w.config.input.estimated_counts is False
    assert "near 1,000,000" in warnings[-1]
    assert (w.project_root / "config" / "counts_matrix.txt").exists()
    w.close()


def test_a_network_share_path_is_refused_with_a_warning_at_every_wsl_conversion(monkeypatch, tmp_path) -> None:
    # app.core.paths.windows_to_wsl_path raises UnsupportedUncPathError for a non-WSL UNC path
    # (a \\server\share\... path). Every call site must surface that as a warning and leave the
    # stored value untouched, never as an excepthook traceback.
    from pathlib import Path as _Path

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from app.ui import main_window as mw

    _app()
    w = MainWindow()
    w.workdir.setText(str(_Path("manual_test_gui") / uuid4().hex))
    w.project_name.setText("unc")
    w._create_benchmark_project("pasilla_paired_subset")
    assert w.project_root is not None

    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _p, _t, text, *a, **k: warnings.append(text)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    share = "\\\\fileserver\\genomics\\"

    # 1. Reference Manager: a UNC genome/annotation must not reach the config at all.
    before = (w.config.reference.mode, w.config.reference.genome_fasta,
              w.config.reference.annotation_file)
    monkeypatch.setattr(_Path, "is_file", lambda self: True)
    monkeypatch.setattr(mw, "validate_reference", lambda *a, **k: [])
    w.ref_genome.setText(share + "genome.fa")
    w.ref_annotation.setText(share + "genes.gtf")
    w._use_custom_reference()
    assert "network share" in warnings[-1]
    assert (w.config.reference.mode, w.config.reference.genome_fasta,
            w.config.reference.annotation_file) == before
    # undo() drops every patch on this fixture, so both dialog stubs must be re-applied:
    # an unstubbed modal would hang the offscreen suite rather than fail it.
    monkeypatch.undo()
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _p, _t, text, *a, **k: warnings.append(text)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    # 2. FASTQ picker under WSL: the sheet must not be written from half-translated paths.
    w.use_wsl.setChecked(True)
    sheet = w._configured_samples_path()
    stamp = sheet.read_text(encoding="utf-8") if sheet.exists() else None
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([share + "s1_R1.fastq.gz",
                                                       share + "s1_R2.fastq.gz"], "")))
    w._select_fastqs()
    assert "network share" in warnings[-1]
    assert (sheet.read_text(encoding="utf-8") if sheet.exists() else None) == stamp

    # 3. Custom gene-set fields: the save is refused and the stored value is unchanged.
    stored = w.config.gene_sets.custom_gene_sets
    w.custom_gmt.setText(share + "sets.gmt")
    assert w._save_workflow_settings() is False
    assert "network share" in warnings[-1]
    assert w.config.gene_sets.custom_gene_sets == stored
    w.close()


def test_reports_page_shows_reports_already_on_disk(tmp_path) -> None:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.core.project import ProjectManager
    from app.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    root = ProjectManager().create_project("reports_on_disk", tmp_path)
    w = MainWindow()
    w._load_project(root)
    assert "No reports yet" in w.report_text.toPlainText()
    reports = root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_summary.txt").write_text("Run finished: earlier" + chr(10), encoding="utf-8")
    w._load_project(root)
    assert "run_summary.txt" in w.report_text.toPlainText()
    w.report_text.setPlainText("No reports yet.")
    w._refresh_report_status()
    assert "Run finished: earlier" in w.report_text.toPlainText()


def test_close_deletes_web_views_before_the_deferred_exit(monkeypatch) -> None:
    import shiboken6

    import app.ui.main_window as mw
    from app.ui.ppi_viewer import PpiViewer

    app = _app()
    # A window the test built does not own the event loop: closing it must leave the
    # shared application's quit policy alone.
    window = MainWindow()
    window.close()
    assert app.quitOnLastWindowClosed() is True

    owner = MainWindow()
    owner._exit_on_close = True
    owner._quit_code = 3
    viewers = owner.findChildren(PpiViewer)
    assert viewers
    scheduled, exits = [], []
    # Patch only this module's QTimer; the shared class also drives conftest teardown.
    monkeypatch.setattr(mw, "QTimer", type("Timer", (), {"singleShot": staticmethod(
        lambda ms, fn: scheduled.append((ms, fn)))}))
    monkeypatch.setattr(app, "exit", lambda code: exits.append(code))
    try:
        owner.close()
        assert app.quitOnLastWindowClosed() is False
        assert [ms for ms, _ in scheduled] == [250]
        QApplication.sendPostedEvents(None, 52)  # QEvent.Type.DeferredDelete
        assert not any(shiboken6.isValid(v) for v in viewers)
        scheduled[0][1]()
        assert exits == [3]
    finally:
        app.setQuitOnLastWindowClosed(True)
