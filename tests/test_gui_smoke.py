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
    w._apply_figure_style()
    assert w.config.figures_style.volcano_y_scale == "full"
    assert w.config.figures_style.sample_labels is False
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
