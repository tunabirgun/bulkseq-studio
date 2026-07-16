from __future__ import annotations

import shutil
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml
from PySide6.QtCore import QByteArray, QSettings, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QDesktopServices, QFontDatabase, QKeySequence, QPixmap, QShortcut
from PySide6.QtCore import Qt, QUrl

from app.constants import APP_NAME, APP_VERSION, MIN_UNIQUE_MAPPED_WARN_PCT
from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.config_models import AppConfig
from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import dataframe_from_rows, load_metadata, save_metadata, validate_metadata
from app.core.project import ProjectManager, decimal_comma_warnings, validate_working_directory
from app.core.provenance import write_run_summary
from app.core.reference_manager import catalog_entry_for_organism, load_reference_catalog, md5sum, validate_reference
from app.core.resources import detect_system, recommend_profile
from app.core.sra_metadata import fetch_ena_metadata, metadata_to_samples
from app.core.geo_metadata import fetch_geo_series
from app.core.runtime_calibration import calibration_factor, record_run
from app.core.runtime_estimator import estimate_runtime
from app.core.sanity_checks import write_check
from app.core.snakemake_runner import (
    SnakemakeRunner,
    _new_run_tag,
    build_snakemake_command,
    snakemake_run_state,
)
from app.core.timing import write_timing_summary
from app.core.paths import data_path, windows_to_wsl_path, wsl_recommended_workdir
from app.ui.image_viewer import SVG_AVAILABLE, ImageViewer
from app.ui.metadata_editor import MetadataTable
from app.ui.readiness_dialog import ReadinessDialog
from app.ui.theme import IMAGEVIEWER_BG, PALETTES, apply_theme


class RunnerThread(QThread):
    line = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, runner: SnakemakeRunner) -> None:
        super().__init__()
        self.runner = runner

    def run(self) -> None:
        try:
            process = self.runner.start()
        except OSError as exc:
            self.line.emit(f"Failed to launch run: {exc}")
            self.finished_with_code.emit(1)
            return
        assert process.stdout is not None
        for line in process.stdout:
            self.line.emit(line.rstrip())
        self.finished_with_code.emit(process.wait())


class BackgroundWorker(QThread):
    """Runs a callable off the UI thread so a busy bar can animate while a blocking
    operation (e.g. detect_system probing WSL) runs, instead of freezing."""

    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # surfaced via failed signal on the UI thread
            self.failed.emit(exc)
            return
        self.done.emit(result)


class _SortableItem(QTableWidgetItem):
    """Table cell that sorts numerically when both cells are numbers, else as text.

    The Outputs preview loads every cell as a string (dtype=str), so a plain
    QTableWidgetItem would sort a numeric column lexicographically ("10" < "2",
    "-3" < "-30"). This overrides `<` to compare as floats when possible, so
    log2FoldChange / padj / baseMean columns sort in true numeric order.
    """

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return float(self.text()) < float(other.text())
        except (ValueError, TypeError):
            return self.text().casefold() < other.text().casefold()


class MainWindow(QMainWindow):
    FONT_DEFAULT_LABEL = "(ggplot default)"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.manager = ProjectManager()
        self.project_root: Path | None = None
        self.config: AppConfig | None = None
        self.runner_thread: RunnerThread | None = None
        self.runner: SnakemakeRunner | None = None
        self.readiness_dialog: ReadinessDialog | None = None
        self._run_active = False
        self._run_mode: str | None = None
        self._stop_in_progress = False
        self._recovery_offered = False
        self._pending_recover = False  # set on the locked-resume / auto-recovery path; consumed by _on_run_finished
        self._mapping_checked: set[str] = set()
        self._mapping_halt_decided = False
        self._closing = False
        self.run_action_buttons: dict[str, QPushButton] = {}
        self.stop_button: QPushButton | None = None

        self.tabs = QTabWidget()
        # At narrow window widths the 12 tabs would overflow to scroll arrows that hide the last
        # tab (PPI Network); elide the labels instead so every tab stays visible and reachable.
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.setCentralWidget(self.tabs)
        # The window owns its minimum so the size contract holds even under direct
        # construction (tests), and the restore-geometry size guard has a real bound.
        self.setMinimumSize(900, 640)
        # Light/dark mode toggle: a labelled button in the top-right corner. It is
        # wrapped in a container with margins so it isn't flush against (and visually
        # clipped by) the window edge; the container is the corner widget, so the
        # button still sits in the corner rather than drifting into the page.
        self.theme_toggle = QPushButton()
        self.theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_toggle.setMinimumWidth(110)  # stable width across Dark/Light label swap
        self.theme_toggle.setMinimumHeight(26)  # readable height, not squeezed by the tab bar
        self.theme_toggle.setFlat(False)
        self.theme_toggle.clicked.connect(self._toggle_theme)
        self._sync_theme_toggle(str(QSettings().value("theme_mode", "light")))
        theme_corner = QWidget()
        theme_corner_layout = QHBoxLayout(theme_corner)
        theme_corner_layout.setContentsMargins(6, 2, 8, 2)
        theme_corner_layout.addWidget(self.theme_toggle)
        self.tabs.setCornerWidget(theme_corner, Qt.Corner.TopRightCorner)
        self._build_project_tab()
        self._build_input_tab()
        self._build_metadata_tab()
        self._build_reference_tab()
        self._build_workflow_tab()
        self._build_resources_tab()
        self._build_runtime_tab()
        self._build_sanity_tab()
        self._build_run_tab()
        self._build_reports_tab()
        self._build_outputs_tab()
        self._build_ppi_tab()
        # A small status bar at the bottom for transient feedback (e.g. resource
        # detection), so blocking actions show progress instead of looking frozen.
        # The environment check is on-demand (the 'Check Environment' button) so the
        # window opens instantly instead of blocking on WSL/conda probes at startup.
        if not sys.platform.startswith("win"):
            self.statusBar().showMessage(
                "Ready — the pipeline runs natively in the local environment. Click 'Check "
                "Environment' on the Project tab to verify the bioinformatics tools are installed."
            )
        elif shutil.which("wsl") is None:
            self.statusBar().showMessage(
                "WSL2 was not detected. Click 'Check Environment' on the Project tab to "
                "enable WSL2 and install the bioinformatics environment before running."
            )
        else:
            self.statusBar().showMessage(
                "Ready — on the Project tab, click 'Check Environment' to verify your WSL setup."
            )
        self._install_shortcuts()
        # Prefer the WSL-native filesystem by default (resolved in the background so
        # startup stays instant); the user can still pick a Windows folder.
        self._autodetect_wsl_workdir()
        # On the very first launch, open the environment check up front so a missing tool
        # is caught before a run (deferred so the window paints first).
        QTimer.singleShot(600, self._maybe_prompt_first_run_readiness)

    def _maybe_prompt_first_run_readiness(self) -> None:
        # First launch after install: auto-open the environment check so a missing tool
        # (e.g. the R/DESeq2 stack) surfaces up front rather than as an exit-127 surprise
        # mid-run. Shown once; the 'Check Environment' button reopens it anytime.
        if os.environ.get("BULKSEQ_SKIP_READINESS_DIALOG") == "1" or os.environ.get("BULKSEQ_SELFTEST") == "1":
            return
        settings = QSettings()
        # Version-scoped, not a permanent boolean: re-open the environment check after an app
        # update so a carried-over broken env (e.g. one whose R stack stopped loading between
        # versions) is re-surfaced instead of silently persisting and failing the next run.
        # The environment check clears this stamp when it finds the R stack broken, so a broken
        # env keeps being re-nudged until it is repaired.
        if settings.value("env_check_prompted_version", "", type=str) == APP_VERSION:
            return
        settings.setValue("env_check_prompted_version", APP_VERSION)
        self.statusBar().showMessage(
            "Opening the environment check so any missing or broken tool is caught before a run.", 9000)
        self.show_readiness_dialog()

    def _install_shortcuts(self) -> None:
        # Keyboard shortcuts for the highest-frequency actions (no menu bar).
        for seq, slot in (
            (QKeySequence("Ctrl+O"), self._open_project),
            (QKeySequence("F5"), lambda: self._start_snakemake("dry-run")),
            (QKeySequence("F9"), lambda: self._start_snakemake("run")),
        ):
            QShortcut(seq, self, activated=slot)

    # ---- Theme toggle ------------------------------------------------------
    def _current_theme_mode(self) -> str:
        mode = str(QSettings().value("theme_mode", "light"))
        return mode if mode in ("light", "dark") else "light"

    def _toggle_theme(self) -> None:
        new_mode = "dark" if self._current_theme_mode() == "light" else "light"
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode=new_mode)
        QSettings().setValue("theme_mode", new_mode)
        self._sync_theme_toggle(new_mode)
        # A QGraphicsScene ignores widget QSS, so repaint the viewer background.
        if hasattr(self, "figure_viewer"):
            self.figure_viewer.update_theme(IMAGEVIEWER_BG.get(new_mode, IMAGEVIEWER_BG["light"]))
        # A QWebEngineView ignores app QSS too; push the palette into the page.
        if hasattr(self, "ppi_viewer"):
            self.ppi_viewer.update_theme(self._ppi_theme_palette())

    def _sync_theme_toggle(self, mode: str) -> None:
        # The button is labelled with the mode it switches TO.
        if mode == "light":
            self.theme_toggle.setText("Dark Mode")
            self.theme_toggle.setToolTip("Switch to the dark theme")
        else:
            self.theme_toggle.setText("Light Mode")
            self.theme_toggle.setToolTip("Switch to the light theme")

    # ---- Window geometry persistence --------------------------------------
    def _save_geometry_state(self) -> None:
        s = QSettings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        for key in ("_outputs_main_splitter", "_outputs_results_splitter"):
            sp = getattr(self, key, None)
            if sp is not None:
                s.setValue(f"outputs/{key}", sp.saveState())

    def _restore_geometry_state(self) -> None:
        s = QSettings()
        geo = s.value("geometry", QByteArray())
        if isinstance(geo, QByteArray) and not geo.isEmpty():
            self.restoreGeometry(geo)
            if self.width() < self.minimumWidth() or self.height() < self.minimumHeight():
                self.resize(1280, 820)  # reject a saved size smaller than the minimum
        st = s.value("windowState", QByteArray())
        if isinstance(st, QByteArray) and not st.isEmpty():
            self.restoreState(st)

    def closeEvent(self, event) -> None:
        # Flag closing so any queued worker callbacks return early instead of
        # touching widgets that are being torn down.
        self._closing = True
        self._save_geometry_state()
        # Stop an active pipeline run before teardown: a still-running QThread
        # destroyed by Qt crashes, and the WSL process tree would be orphaned.
        if self.runner is not None and self.runner.is_running():
            self._stop_run(announce=False)
        # Let short-lived background probes finish so QThread isn't destroyed while
        # running (which would crash). These are bounded WSL/resource queries.
        for attr in ("_wsl_autodetect_worker", "_wsl_workdir_worker", "_detect_worker",
                     "_geo_worker", "_sra_worker", "_reports_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.wait(3000)
        if self.runner_thread is not None and self.runner_thread.isRunning():
            self.runner_thread.wait(5000)
        super().closeEvent(event)

    def _scrollable(self, page: QWidget) -> QScrollArea:
        # Wrap a tall form page so the window can shrink below the page's natural
        # height; the page scrolls instead of forcing a large minimum window size.
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _build_project_tab(self) -> None:
        page = QWidget()
        layout = QFormLayout(page)
        self.project_name = QLineEdit("example_project")
        self._default_workdir = str(Path.home() / "BulkSeqProjects")
        self.workdir = QLineEdit(self._default_workdir)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_workdir)
        wsl_fs = QPushButton("Use WSL filesystem")
        wsl_fs.setToolTip("Place the project on the WSL2 (Linux) filesystem for the fastest "
                          "genomics I/O. A Windows-drive folder works too but is slower over the "
                          "/mnt 9P boundary.")
        wsl_fs.clicked.connect(self._use_wsl_workdir)
        wsl_fs.setVisible(sys.platform.startswith("win"))  # WSL filesystem is a Windows-only concept
        workdir_row = QHBoxLayout()
        workdir_row.addWidget(self.workdir)
        workdir_row.addWidget(browse)
        workdir_row.addWidget(wsl_fs)
        if sys.platform.startswith("win"):
            workdir_hint = QLabel(
                "Recommended for WSL2: keep the project on the Linux filesystem "
                "(\\\\wsl.localhost\\...). A Windows folder (C:\\...) also works but is slower for "
                "large genomics files."
            )
        else:
            workdir_hint = QLabel(
                "The project folder and the pipeline run in the local filesystem and environment."
            )
        workdir_hint.setWordWrap(True)
        create = QPushButton("New Project")
        create.setProperty("primary", True)
        create.setToolTip("Scaffold a new project folder: a default config.yaml and an empty samples.tsv.")
        create.clicked.connect(self._create_project)
        benchmark = QPushButton("Create Benchmark Project")
        benchmark.setToolTip("Set up a project pre-loaded with a bundled validation dataset, for testing or a worked example.")
        benchmark.clicked.connect(lambda: self._create_benchmark_project())
        open_existing = QPushButton("Open Existing Project")
        open_existing.clicked.connect(self._open_project)
        readiness = QPushButton("Check Environment")
        readiness.clicked.connect(self.show_readiness_dialog)
        self.recent_pick = QComboBox()
        self.recent_pick.setToolTip("Projects you have opened before.")
        recent_open = QPushButton("Open recent")
        recent_open.clicked.connect(self._open_recent_project)
        recent_row = QHBoxLayout()
        recent_row.addWidget(self.recent_pick, 1)
        recent_row.addWidget(recent_open)
        self.project_status = QTextEdit()
        self.project_status.setReadOnly(True)
        self.project_status.setPlaceholderText(
            "Create a new project or open an existing one. Status and next steps appear here.")
        layout.addRow("Project name", self.project_name)
        layout.addRow("Working directory", workdir_row)
        layout.addRow("", workdir_hint)
        layout.addRow(create, open_existing)
        layout.addRow(benchmark, readiness)
        layout.addRow("Recent projects", recent_row)
        layout.addRow("Status", self.project_status)
        self._refresh_recent_projects()
        self.tabs.addTab(self._scrollable(page), "Project")

    def _refresh_recent_projects(self) -> None:
        if not hasattr(self, "recent_pick"):
            return
        s = QSettings()
        recent = s.value("recent_projects", []) or []
        if isinstance(recent, str):
            recent = [recent]
        self.recent_pick.blockSignals(True)
        self.recent_pick.clear()
        self.recent_pick.addItems([str(p) for p in recent])
        self.recent_pick.blockSignals(False)
        self.recent_pick.setEnabled(bool(recent))

    def _open_recent_project(self) -> None:
        path = self.recent_pick.currentText().strip() if hasattr(self, "recent_pick") else ""
        if path:
            self._load_project(Path(path))

    def _build_input_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.sra_box = QTextEdit()
        # Paste as plain text: strip any source formatting (fonts/colours/links) so pasted
        # accessions come in clean.
        self.sra_box.setAcceptRichText(False)
        self.sra_box.setPlaceholderText("Paste SRR/ERR/DRR runs, or an SRP/PRJNA/GSE study accession, one per line")
        buttons = QHBoxLayout()
        fetch_meta = QPushButton("Fetch metadata && build samples")
        fetch_meta.clicked.connect(self._fetch_sra_metadata)
        save_sra = QPushButton("Save accessions only")
        save_sra.clicked.connect(self._save_sra)
        pick_fastq = QPushButton("Select FASTQ Files")
        pick_fastq.clicked.connect(self._select_fastqs)
        for b in (fetch_meta, save_sra, pick_fastq):
            buttons.addWidget(b)
        self.input_preview = QTextEdit()
        self.input_preview.setReadOnly(True)
        self.input_preview.setPlaceholderText(
            "Pick an input mode above. A summary of the imported samples and the next steps appears here.")
        layout.addWidget(QLabel("SRA / ENA accessions"))
        layout.addWidget(self.sra_box)
        layout.addLayout(buttons)
        _ena_hint = QLabel("Fetched from the ENA Portal API: layout, FASTQ URLs, read counts. A condition grouping is suggested from the sample titles (or GEO characteristics) where possible; always review and correct it in the Metadata tab before running — it sets the differential-expression contrast.")
        _ena_hint.setWordWrap(True)  # long hint must wrap, not force width / clip on narrow windows
        layout.addWidget(_ena_hint)
        layout.addWidget(self.input_preview)
        cm_row = QHBoxLayout()
        cm_btn = QPushButton("Use a Count Matrix (skip alignment)")
        cm_btn.setToolTip("Start from a gene x sample counts table (TSV/CSV or featureCounts output). "
                          "The pipeline skips download/QC/alignment and runs DESeq2 -> figures -> enrichment.")
        cm_btn.clicked.connect(self._import_count_matrix)
        cm_row.addWidget(QLabel("Already have counts?"))
        cm_row.addWidget(cm_btn)
        cm_row.addStretch(1)
        layout.addLayout(cm_row)
        dr_row = QHBoxLayout()
        dr_btn = QPushButton("Upload DESeq2 Results (skip to enrichment/PPI)")
        dr_btn.setToolTip("Start from a ready DESeq2 results table (CSV/TSV with at least gene_id, "
                          "log2FoldChange and padj). The pipeline skips alignment/counts/DESeq2 and runs "
                          "enrichment, the volcano/MA/p-value figures and the STRING PPI network. PCA, "
                          "sample heatmaps, sample correlation and genes-of-interest need counts and are skipped.")
        dr_btn.clicked.connect(self._import_deseq2_results)
        dr_row.addWidget(QLabel("Already have DE results?"))
        dr_row.addWidget(dr_btn)
        dr_row.addStretch(1)
        layout.addLayout(dr_row)
        geo_row = QHBoxLayout()
        self.gse_box = QLineEdit()
        self.gse_box.setPlaceholderText("GSE accession, e.g. GSE5583")
        self.gse_box.setMaximumWidth(220)
        self.gse_box.setToolTip(
            "GEO Series (GSE) microarray accessions only. For RNA-seq, enter SRA/ENA run "
            "accessions in the other box instead.")
        geo_btn = QPushButton("Fetch a GEO microarray series (GSE)")
        geo_btn.setToolTip("Load a GEO/GSE microarray dataset. The pipeline ingests the normalized "
                           "intensities (GEOquery/affy), runs limma differential expression, then the "
                           "same figures and enrichment. RNA-seq GSEs are redirected to the SRA box.")
        geo_btn.clicked.connect(self._fetch_geo_series)
        micro_upload_btn = QPushButton("Upload a local microarray matrix")
        micro_upload_btn.setToolTip(
            "Load your own microarray data without a GEO accession: a gene x sample expression "
            "matrix (first column gene ids or symbols, one column per sample; already-normalized "
            "log2 intensities). Runs limma differential expression, figures, and enrichment just "
            "like a fetched GEO series — no download.")
        micro_upload_btn.clicked.connect(self._import_microarray_matrix)
        geo_row.addWidget(QLabel("Microarray?"))
        geo_row.addWidget(self.gse_box)
        geo_row.addWidget(geo_btn)
        geo_row.addWidget(micro_upload_btn)
        geo_row.addStretch(1)
        layout.addLayout(geo_row)
        # Microarray processing options (consumed by ingest_geo.R for a loaded GEO series).
        # Shown only in microarray mode (toggled in _apply_input_mode_ui).
        self.micro_source = QComboBox()
        self.micro_source.addItem("GEO series matrix — submitter-normalized (recommended)", "geo_series_matrix")
        self.micro_source.addItem("Affymetrix raw CEL → RMA (re-normalize)", "affy_cel")
        self.micro_source.setToolTip(
            "How the microarray intensities are obtained. 'GEO series matrix' (recommended) uses the "
            "submitter's normalized table, correct for the large majority of GEO datasets. 'Affymetrix "
            "raw CEL → RMA' downloads the raw CEL archive and re-normalizes with affy::rma — Affymetrix "
            "arrays only, a larger download, and it needs the full R environment.")
        self.micro_log2 = QComboBox()
        self.micro_log2.addItem("Auto-detect log2 (recommended)", "auto")
        self.micro_log2.addItem("Force log2 transform", "yes")
        self.micro_log2.addItem("No log2 (already log-scaled)", "no")
        self.micro_log2.setToolTip(
            "Whether to log2-transform the intensities. Auto-detect uses the GEO2R quantile heuristic "
            "(correct for most series); RMA output is always already log2.")
        self.micro_source.currentIndexChanged.connect(self._on_micro_option_changed)
        self.micro_log2.currentIndexChanged.connect(self._on_micro_option_changed)
        self.micro_group = QGroupBox("Microarray processing (applies to the loaded GEO series)")
        micro_form = QFormLayout(self.micro_group)
        micro_form.addRow("Source", self.micro_source)
        micro_form.addRow("log2 transform", self.micro_log2)
        self.micro_group.setVisible(False)
        layout.addWidget(self.micro_group)
        self.tabs.addTab(self._scrollable(page), "Input Data")

    def _fetch_geo_series(self) -> None:
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        gse = self.gse_box.text().strip()
        if not gse:
            QMessageBox.information(self, APP_NAME, "Enter a GSE accession (e.g. GSE5583) first.")
            return
        if getattr(self, "_geo_worker", None) is not None and self._geo_worker.isRunning():
            return
        self.statusBar().showMessage(f"Fetching {gse} from GEO...")
        worker = BackgroundWorker(lambda: fetch_geo_series(gse))
        worker.done.connect(lambda result: self._on_geo_fetched(gse, result))
        worker.failed.connect(self._on_geo_failed)
        self._geo_worker = worker
        worker.start()

    def _on_geo_failed(self, exc: object) -> None:
        if getattr(self, "_closing", False):
            return
        self.statusBar().clearMessage()
        QMessageBox.warning(self, APP_NAME, f"Could not load the GEO series:\n{exc}")

    def _on_geo_fetched(self, gse: str, result: object) -> None:
        if getattr(self, "_closing", False) or self.config is None or self.project_root is None:
            return
        self.statusBar().clearMessage()
        info = result if isinstance(result, dict) else {}
        if not info.get("is_microarray", False):
            QMessageBox.warning(
                self, APP_NAME,
                f"{gse} looks like a sequencing series (type: {info.get('series_type', 'unknown')}), "
                "not a microarray. Use the SRA/ENA accessions box above for RNA-seq studies.")
            return
        samples = info["samples"]
        save_metadata(samples, self.project_root / "config" / "samples.tsv")
        self.metadata_table.load_dataframe(samples)
        organism = str(info.get("organism", "")).strip()
        platform = str(info.get("platform", "")).strip()
        self.config.input.type = "microarray"
        self.config.input.count_matrix = None
        self.config.input.deseq2_results = None
        self.config.microarray.gse_accession = gse
        self.config.microarray.platform = platform or None
        self.config.microarray.source = self.micro_source.currentData()
        self.config.microarray.log2_transform = self.micro_log2.currentData()
        if organism:
            self.config.reference.organism_name = organism
            # Pull the organism's enrichment/PPI ids from the catalog when the GEO
            # organism string matches a preset (e.g. Fusarium-GEO -> KEGG fgr, taxon
            # 229533). keytype stays SYMBOL below; no match leaves the ids None.
            entry = catalog_entry_for_organism(organism)
            if entry is not None:
                self.config.enrichment.orgdb = entry.get("orgdb") or None
                self.config.enrichment.kegg_organism = entry.get("kegg_organism") or None
                self.config.enrichment.gprofiler_organism = entry.get("gprofiler_organism") or None
                self.config.ppi.taxon = entry.get("string_taxon")
        # GPL annotation maps probes to gene symbols, so enrichment uses SYMBOL.
        self.config.enrichment.keytype = "SYMBOL"
        self.manager.save_config(self.project_root, self.config)
        self._apply_input_mode_ui()
        organism_note = (organism or "organism not reported")
        enrichment_warn = "" if organism else (
            "\n\nNote: no organism was found in the series matrix, so functional enrichment "
            "may be skipped. Set the organism on the Reference Manager tab if you want enrichment.")
        self.input_preview.setPlainText(
            f"Microarray mode: {gse} ({platform}), {len(samples)} samples — {organism_note}.\n\n"
            "Next: assign each sample a condition on the Metadata tab, set the contrast on "
            "Workflow Settings, then Start Run. Alignment and a reference genome are not needed."
            + enrichment_warn)
        self.statusBar().showMessage(f"Loaded {gse}: {len(samples)} microarray samples.", 8000)

    def _apply_input_mode_ui(self) -> None:
        # Reflect the current input mode: microarray/count-matrix need no genome
        # reference, so surface that on the Reference tab.
        if self.config is None:
            return
        mode = self.config.input.type
        # A microarray-only SYMBOL enrichment keytype must not carry into a count-based route: it would
        # override the organism's correct ENSEMBL/LOC default and mis-map gene ids for enrichment. The
        # microarray ingest re-sets it; clear it for every other mode here (central guard — the per-import
        # clears in the count-matrix / deseq2-results handlers are now redundant but harmless). Persist
        # only on the rare transition that actually clears it.
        if (mode != "microarray" and self.project_root is not None
                and self.config.enrichment.keytype == "SYMBOL"):
            self.config.enrichment.keytype = None
            self.manager.save_config(self.project_root, self.config)
        if getattr(self, "reference_mode_banner", None) is not None:
            if mode == "microarray":
                self.reference_mode_banner.setText(
                    "Microarray mode: alignment is skipped (limma works on intensities). "
                    "You still need to select your organism below — it enables GO/KEGG "
                    "enrichment and the STRING PPI network. Without a selection, enrichment "
                    "and PPI are skipped.")
                self.reference_mode_banner.setVisible(True)
            elif mode == "count_matrix":
                self.reference_mode_banner.setText(
                    "Count-matrix mode: alignment is skipped. You still need to select your "
                    "organism below — it enables GO/KEGG enrichment and the STRING PPI network. "
                    "Without a selection, enrichment and PPI are skipped.")
                self.reference_mode_banner.setVisible(True)
            elif mode == "deseq2_results":
                self.reference_mode_banner.setText(
                    "DESeq2-results mode: alignment and a genome reference are skipped. Select your "
                    "organism below — it enables GO/KEGG enrichment and the STRING PPI network. PCA, "
                    "sample heatmaps, sample correlation and genes-of-interest need per-sample counts "
                    "and are not produced in this mode.")
                self.reference_mode_banner.setVisible(True)
            else:
                self.reference_mode_banner.setVisible(False)
        self._refresh_output_table_pick()
        self._update_enrichment_warning()
        self._update_organism_label()
        if getattr(self, "micro_group", None) is not None:
            self.micro_group.setVisible(mode == "microarray")
        if getattr(self, "micro_source", None) is not None:
            # A local-matrix upload has no combo item (source="local_matrix"); leaving the combo
            # enabled at its default index would visibly contradict the persisted config.
            is_local = mode == "microarray" and self.config.microarray.source == "local_matrix"
            self.micro_source.setEnabled(not is_local)
            self.micro_source.setToolTip(
                "Not applicable: this project uses a locally uploaded expression matrix, not a "
                "GEO/CEL download."
                if is_local else
                "How the microarray intensities are obtained. 'GEO series matrix' (recommended) uses the "
                "submitter's normalized table, correct for the large majority of GEO datasets. 'Affymetrix "
                "raw CEL → RMA' downloads the raw CEL archive and re-normalizes with affy::rma — Affymetrix "
                "arrays only, a larger download, and it needs the full R environment.")
        self._apply_workflow_mode_gating(mode)

    def _on_micro_option_changed(self) -> None:
        # Persist the microarray source / log2 choice as the user picks it (Input Data tab).
        if self.config is None or self.project_root is None:
            return
        # The Source combo only offers geo_series_matrix / affy_cel; a local-matrix upload sets
        # source='local_matrix' (no combo item), so writing the combo's value here would clobber it
        # to geo_series_matrix and the run would abort trying to download an empty accession. Only the
        # GEO/CEL sources come from this combo; a local matrix keeps its source. The log2 override is a
        # legitimate live control on the local path, so it is always persisted.
        if self.config.microarray.source != "local_matrix":
            self.config.microarray.source = self.micro_source.currentData()
        self.config.microarray.log2_transform = self.micro_log2.currentData()
        self.manager.save_config(self.project_root, self.config)

    def _apply_workflow_mode_gating(self, mode: str) -> None:
        # Grey out the Workflow Settings controls the engine ignores in this input mode, so the
        # UI matches what actually runs. Purely cosmetic: every gated field is already dropped
        # from the Snakemake DAG for the mode (aligner/trim/rRNA/contam/quantifier/rseqc/
        # organellar in microarray/count-matrix/deseq2-results; de_engine in microarray and
        # deseq2-results; gsva needs a per-sample matrix, absent in deseq2-results).
        alignment_active = mode in ("fastq", "sra", "mixed")
        if getattr(self, "align_group", None) is not None:
            self.align_group.setEnabled(alignment_active)
            if alignment_active:
                # A blanket re-enable would clobber the parent/child cascades; restore them.
                self.trimmer.setEnabled(self.trim.isChecked())
                self.rrna_tool.setEnabled(self.rrna.isChecked())
                self._on_aligner_changed(self.aligner.currentText())
        if getattr(self, "de_engine", None) is not None:
            # microarray forces limma-trend; deseq2-results bypasses the DE step entirely.
            self.de_engine.setEnabled(mode not in ("microarray", "deseq2_results"))
        if getattr(self, "organellar", None) is not None:
            self.organellar.setEnabled(alignment_active)  # needs a genome + GTF
        if getattr(self, "rseqc", None) is not None:
            self.rseqc.setEnabled(alignment_active)  # needs a genome BAM
        if getattr(self, "gsva", None) is not None:
            self.gsva.setEnabled(mode != "deseq2_results")  # needs the normalized matrix
        if getattr(self, "meta_analysis", None) is not None:
            # Count-based routes only (needs a per-study count matrix); microarray / results-upload
            # cannot run the per-study DESeq2 fan-out. The workflow additionally requires a 'dataset'
            # column with >1 study (MULTI_DATASET) — the Snakefile is the source of truth there.
            self.meta_analysis.setEnabled(mode in ("fastq", "sra", "mixed", "count_matrix"))
        if getattr(self, "per_study_enrichment", None) is not None:
            # Only meaningful when meta-analysis is both available (mode) and enabled.
            self.per_study_enrichment.setEnabled(
                self.meta_analysis.isEnabled() and self.meta_analysis.isChecked())

    def _import_deseq2_results(self) -> None:
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a DESeq2 results table", "", "DESeq2 results (*.csv *.tsv *.txt)")
        if not path:
            return
        src = Path(path)
        sep = "," if src.suffix.lower() == ".csv" else "\t"
        self.statusBar().showMessage("Importing DESeq2 results table...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                df = pd.read_csv(src, sep=sep, comment="#", dtype=str, nrows=5)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not read the table: {exc}")
                return
            cols = {str(c).strip().lower() for c in df.columns}
            def _has(*names: str) -> bool:
                return any(n.lower() in cols for n in names)
            missing = []
            if not _has("gene_id", "gene", "geneid", "id"):
                missing.append("gene_id")
            if not _has("log2FoldChange", "log2fc", "logfc"):
                missing.append("log2FoldChange")
            if not _has("padj", "adj.P.Val", "FDR", "qvalue"):
                missing.append("padj")
            if missing:
                QMessageBox.warning(
                    self, APP_NAME,
                    "The results table is missing required column(s): " + ", ".join(missing) +
                    ".\n\nRequired: gene_id, log2FoldChange, padj (common synonyms accepted; see the README).")
                return
            # Copy verbatim; the ingest step detects CSV vs TSV and normalizes headers.
            dest = self.project_root / "config" / "deseq2_results.csv"
            shutil.copyfile(src, dest)
            self.config.input.type = "deseq2_results"
            self.config.input.deseq2_results = "config/deseq2_results.csv"
            self.config.input.count_matrix = None
            self.config.microarray.gse_accession = None
            # A microarray-only SYMBOL keytype must not carry over; fall back to the
            # organism mapping (or the LOC/ENSEMBL handling in the enrichment step).
            if self.config.enrichment.keytype == "SYMBOL":
                self.config.enrichment.keytype = None
            samples = dataframe_from_rows([
                {"sample_id": "uploaded", "condition": "unknown", "layout": "n/a", "fastq_1": ""}
            ])
            save_metadata(samples, self.project_root / "config" / "samples.tsv")
            self.metadata_table.load_dataframe(samples)
            self.manager.save_config(self.project_root, self.config)
        finally:
            QApplication.restoreOverrideCursor()
        if hasattr(self, "gse_box"):
            self.gse_box.clear()
        self._apply_input_mode_ui()
        organism = self.config.reference.organism_name
        has_org = bool(self.config.enrichment.kegg_organism or self.config.enrichment.orgdb
                       or self.config.enrichment.gprofiler_organism)
        org_note = (
            f"\n\nEnrichment/PPI organism: {organism}." if has_org else
            "\n\nNo organism selected yet — pick your organism on the Reference Manager tab so GO/KEGG "
            "enrichment and the STRING PPI network can run.")
        self.input_preview.setPlainText(
            f"DESeq2-results mode: imported {src.name}.\n\n"
            "The pipeline skips alignment, counts and DESeq2, and runs enrichment (GO/KEGG/GSEA), the "
            "volcano / MA / p-value figures, and the STRING PPI network directly from your table. PCA, "
            "sample-distance and expression heatmaps, sample correlation, the Wilcoxon diagnostic and "
            "genes-of-interest need per-sample counts and are skipped." + org_note +
            "\n\nNext: select your organism (Reference Manager), optionally set the contrast factor/levels "
            "on Workflow Settings to name the comparison, then Start Run.")
        self.statusBar().showMessage(f"Imported DESeq2 results: {src.name}", 8000)

    def _import_count_matrix(self) -> None:
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a counts matrix", "", "Counts (*.tsv *.txt *.csv)")
        if not path:
            return
        src = Path(path)
        sep = "," if src.suffix.lower() == ".csv" else "\t"
        # Reading/copying the matrix is blocking I/O (and a UNC/9P source can be
        # slow), so show a wait cursor and status instead of a frozen-looking window.
        self.statusBar().showMessage("Importing count matrix...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                df = pd.read_csv(src, sep=sep, comment="#", dtype=str)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not read the matrix: {exc}")
                return
            if df.shape[1] < 2:
                QMessageBox.warning(self, APP_NAME, "The matrix needs a gene-id column plus at least one sample column.")
                return
            # Sample columns = all but the gene-id column, minus featureCounts metadata.
            meta_cols = {"Chr", "Start", "End", "Strand", "Length"}
            sample_cols = [c for c in df.columns[1:] if c not in meta_cols]
            # featureCounts BAM-path columns -> sample_ids.
            def clean(c: str) -> str:
                return re.sub(r"_Aligned\.sortedByCoord\.out\.bam$", "", Path(str(c)).name)
            sample_ids = [clean(c) for c in sample_cols]
            # Detect normalized / estimated input up front (mirrors the ingest_counts guard) so the
            # user gets an immediate, clear choice instead of a downstream ingest failure. RSEM/
            # tximport estimated counts are fractional but valid (rounded); TPM/FPKM/log/RMA are not.
            self.config.input.estimated_counts = False
            _num = df[sample_cols].apply(pd.to_numeric, errors="coerce")
            _vals = _num.to_numpy(dtype="float64").ravel()
            _vals = _vals[~pd.isna(_vals)]
            _nz = _vals[_vals != 0]
            if _nz.size and float((_nz % 1 != 0).mean()) > 0.5:
                _colsum = _num.sum(axis=0, skipna=True).to_numpy(dtype="float64")
                _tpm = _colsum.size and float(((abs(_colsum - 1e6) / 1e6) < 0.01).mean()) >= 0.5
                if _tpm:
                    QApplication.restoreOverrideCursor()
                    QMessageBox.warning(self, APP_NAME,
                        "The matrix columns each sum to ~1,000,000, so this is TPM, not raw counts. "
                        "DESeq2 and the meta-analysis need raw integer counts — re-export "
                        "un-normalized counts and import again.")
                    return
                QApplication.restoreOverrideCursor()
                resp = QMessageBox.question(self, APP_NAME,
                    "The matrix values are mostly non-integer.\n\n"
                    "• If these are RSEM / tximport ESTIMATED counts, they will be rounded to "
                    "integers and the run can proceed.\n"
                    "• If they are NORMALIZED data (FPKM/RPKM, log-CPM, RMA or microarray "
                    "intensities), DESeq2 cannot use them — cancel and re-export raw counts.\n\n"
                    "Are these estimated counts?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if resp != QMessageBox.StandardButton.Yes:
                    self.statusBar().showMessage("Count-matrix import cancelled.", 4000)
                    return
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                self.config.input.estimated_counts = True
            # Copy the matrix into the project and switch to count-matrix mode.
            dest = self.project_root / "config" / "counts_matrix.txt"
            # Write the parsed table as canonical TSV. ingest_counts.py picks its
            # separator from the file extension (.csv -> comma, else tab), so a
            # raw-byte copy of a CSV into a .txt name would be misread as TSV.
            df.to_csv(dest, sep="\t", index=False)
            samples = dataframe_from_rows([
                {"sample_id": sid, "condition": "unknown", "layout": "n/a", "fastq_1": ""}
                for sid in sample_ids
            ])
            save_metadata(samples, self.project_root / "config" / "samples.tsv")
            self.metadata_table.load_dataframe(samples)
            self.config.input.type = "count_matrix"
            self.config.input.count_matrix = "config/counts_matrix.txt"
            # Switching to count-matrix mode: drop any stale microarray accession or
            # uploaded results table so a later save doesn't write inputs that no
            # longer apply.
            self.config.microarray.gse_accession = None
            self.config.input.deseq2_results = None
            # Clear a microarray-only SYMBOL keytype so it can't carry into a
            # count-matrix run (whose ids are usually ENSEMBL); fall back to the
            # organism mapping.
            if self.config.enrichment.keytype == "SYMBOL":
                self.config.enrichment.keytype = None
            self.manager.save_config(self.project_root, self.config)
        finally:
            QApplication.restoreOverrideCursor()
        if hasattr(self, "gse_box"):
            self.gse_box.clear()
        self._apply_input_mode_ui()
        organism = self.config.reference.organism_name
        has_org = bool(self.config.enrichment.kegg_organism or self.config.enrichment.orgdb)
        org_note = (
            f"\n\nEnrichment/PPI organism: {organism}." if has_org else
            "\n\nFor GO/KEGG enrichment and the STRING PPI network, open the Reference "
            "Manager tab and select your organism — without it, enrichment and PPI are skipped.")
        self.input_preview.setPlainText(
            f"Count-matrix mode: {len(sample_ids)} samples — {', '.join(sample_ids)}\n\n"
            "Next: assign each sample a condition on the Metadata tab, set the contrast on "
            "Workflow Settings, then Start Run. Alignment is skipped."
            + org_note
        )
        self.statusBar().showMessage(f"Count matrix imported: {len(sample_ids)} samples. Assign conditions on the Metadata tab.", 8000)

    def _import_microarray_matrix(self) -> None:
        # Manual microarray input: a local gene x sample expression matrix (any platform,
        # already normalized log2 intensities), ingested through the limma path with no GEO
        # download — the counterpart of "Use a Count Matrix" for the microarray backend.
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a microarray expression matrix", "", "Expression matrix (*.tsv *.txt *.csv)")
        if not path:
            return
        src = Path(path)
        sep = "," if src.suffix.lower() == ".csv" else "\t"
        self.statusBar().showMessage("Importing microarray expression matrix...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                df = pd.read_csv(src, sep=sep, comment="#", dtype=str)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not read the matrix: {exc}")
                return
            if df.shape[1] < 2:
                QMessageBox.warning(self, APP_NAME, "The matrix needs a gene-id column plus at least one sample column.")
                return
            sample_ids = [str(c) for c in df.columns[1:]]
            dest = self.project_root / "config" / "microarray_expression.tsv"
            df.to_csv(dest, sep="\t", index=False)
            samples = dataframe_from_rows([
                {"sample_id": sid, "condition": "unknown", "layout": "n/a", "fastq_1": ""}
                for sid in sample_ids
            ])
            save_metadata(samples, self.project_root / "config" / "samples.tsv")
            self.metadata_table.load_dataframe(samples)
            self.config.input.type = "microarray"
            self.config.input.count_matrix = None
            self.config.input.deseq2_results = None
            self.config.microarray.source = "local_matrix"
            self.config.microarray.expression_matrix = "config/microarray_expression.tsv"
            self.config.microarray.gse_accession = None
            self.config.microarray.platform = None
            # Row ids are gene symbols/ids; enrichment keys on SYMBOL as with GEO probe mapping.
            self.config.enrichment.keytype = "SYMBOL"
            self.manager.save_config(self.project_root, self.config)
        finally:
            QApplication.restoreOverrideCursor()
        if hasattr(self, "gse_box"):
            self.gse_box.clear()
        self._apply_input_mode_ui()
        organism = self.config.reference.organism_name
        has_org = bool(self.config.enrichment.kegg_organism or self.config.enrichment.orgdb)
        org_note = (
            f"\n\nEnrichment/PPI organism: {organism}." if has_org else
            "\n\nFor GO/KEGG enrichment and the STRING PPI network, open the Reference "
            "Manager tab and select your organism.")
        self.input_preview.setPlainText(
            f"Microarray (local matrix): {len(sample_ids)} samples — {', '.join(sample_ids)}\n\n"
            "Ingested as a gene x sample expression matrix (limma). Next: assign each sample a "
            "condition on the Metadata tab, set the contrast on Workflow Settings, then Start Run."
            + org_note
        )
        self.statusBar().showMessage(f"Microarray matrix imported: {len(sample_ids)} samples. Assign conditions on the Metadata tab.", 8000)

    def _fetch_sra_metadata(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        accessions = [line.strip() for line in self.sra_box.toPlainText().splitlines() if line.strip()]
        if not accessions:
            QMessageBox.warning(self, APP_NAME, "Paste at least one accession first.")
            return
        if getattr(self, "_sra_worker", None) is not None and self._sra_worker.isRunning():
            return
        # The ENA Portal query can take tens of seconds for a large study, so run it
        # off the UI thread (like the GEO fetch) instead of freezing the window.
        self.input_preview.setPlainText("Querying ENA…")
        self.statusBar().showMessage("Fetching metadata from ENA…")
        worker = BackgroundWorker(lambda: fetch_ena_metadata(accessions))
        worker.done.connect(lambda meta: self._on_sra_fetched(accessions, meta))
        worker.failed.connect(self._on_sra_failed)
        self._sra_worker = worker
        worker.start()

    def _on_sra_failed(self, exc: object) -> None:
        if getattr(self, "_closing", False):
            return
        self.statusBar().clearMessage()
        self.input_preview.setPlainText("")
        QMessageBox.warning(self, APP_NAME, f"ENA query failed: {exc}")

    def _on_sra_fetched(self, accessions: list, meta: object) -> None:
        if getattr(self, "_closing", False) or self.project_root is None:
            return
        self.statusBar().clearMessage()
        samples = metadata_to_samples(meta)
        if samples.empty:
            self.input_preview.setPlainText("No runs found for those accessions.")
            return
        save_metadata(samples, self.project_root / "config" / "samples.auto_generated.tsv")
        save_metadata(samples, self.project_root / "config" / "samples.tsv")
        (self.project_root / "config" / "sra_accessions.txt").write_text("\n".join(accessions) + "\n", encoding="utf-8")
        self.metadata_table.load_dataframe(samples)
        if self.config is not None:
            self.config.input.type = "sra"
            layouts = set(samples["layout"])
            self.config.input.layout = layouts.pop() if len(layouts) == 1 else "mixed"  # type: ignore[assignment]
            self.manager.save_config(self.project_root, self.config)
            self._apply_input_mode_ui()
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.metadata_table.parentWidget()))
        self.input_preview.setPlainText(
            f"Built {len(samples)} sample(s). Set conditions in the Metadata tab, then run.\n\n"
            + samples[["sample_id", "layout", "read_count", "organism"]].to_string(index=False)
        )

    def _save_sra(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        accessions = [line.strip() for line in self.sra_box.toPlainText().splitlines() if line.strip()]
        (self.project_root / "config" / "sra_accessions.txt").write_text("\n".join(accessions) + "\n", encoding="utf-8")
        if self.config is not None:
            self.config.input.type = "sra"
            self.manager.save_config(self.project_root, self.config)
        self.input_preview.setPlainText(f"Saved {len(accessions)} accession(s) to config/sra_accessions.txt")

    def _build_metadata_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        # Group the actions so each button keeps its natural width and reads
        # clearly, instead of 13 buttons cramped into one shrinking row.
        groups = [
            ("Rows", [
                ("Add row", self.metadata_add_row),
                ("Delete rows", self.metadata_delete_rows),
                ("Duplicate rows", self.metadata_duplicate_rows),
            ]),
            ("Columns", [
                ("Add column", self._add_column),
                ("Rename column", self._rename_column),
                ("Remove column", self._remove_column),
            ]),
            ("Data", [
                ("Assign condition", self._assign_condition),
                ("Autofill replicates", self.metadata_autofill),
                ("Paste", self._paste_metadata),
            ]),
        ]
        top_row = QHBoxLayout()
        tooltips = {
            "Paste": "Paste clipboard cells (e.g. copied from Excel) at the selected cell. "
                     "A single copied value fills every selected cell. Ctrl+V works too "
                     "(if a cell is in edit mode, press Esc first).",
        }
        for title, specs in groups:
            box = QGroupBox(title)
            box_layout = QHBoxLayout(box)
            for text, slot in specs:
                btn = QPushButton(text)
                btn.clicked.connect(slot)
                if text in tooltips:
                    btn.setToolTip(tooltips[text])
                box_layout.addWidget(btn)
            top_row.addWidget(box)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        files_box = QGroupBox("File Operations")
        files_layout = QHBoxLayout(files_box)
        for text, slot in [
            ("Import TSV/CSV/XLSX", self._import_metadata),
            ("Export TSV", self._export_metadata),
            ("Restore auto-generated", self._restore_auto_metadata),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            files_layout.addWidget(btn)
        bottom_row.addWidget(files_box)
        bottom_row.addStretch(1)
        validate_btn = QPushButton("Validate")
        validate_btn.setProperty("primary", True)
        validate_btn.clicked.connect(self._validate_metadata)
        save_btn = QPushButton("Save samples.tsv")
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._save_metadata)
        bottom_row.addWidget(validate_btn)
        bottom_row.addWidget(save_btn)
        layout.addLayout(bottom_row)

        self.metadata_table = MetadataTable()
        self.metadata_messages = QTextEdit()
        self.metadata_messages.setReadOnly(True)
        layout.addWidget(self.metadata_table)
        layout.addWidget(self.metadata_messages)
        self.tabs.addTab(page, "Metadata")

    def _build_reference_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.reference_list = QListWidget()
        for entry in load_reference_catalog():
            self.reference_list.addItem(f"{entry['organism_name']} | {entry.get('strain')} | {entry.get('genome_size_category')}")
        choose = QPushButton("Use Selected Preset")
        choose.setProperty("primary", True)
        choose.clicked.connect(self._select_reference)
        self.reference_mode_banner = QLabel("")
        self.reference_mode_banner.setWordWrap(True)
        # Amber callout so the count-matrix/microarray guidance reads as an
        # advisory the user should act on, not a greyed-out aside.
        self.reference_mode_banner.setStyleSheet(
            "font-weight: 600; color: #8B5200; background: #FBEEDA; "
            "border: 1px solid #E5C99A; border-radius: 4px; padding: 6px;")
        self.reference_mode_banner.setVisible(False)
        self.current_organism_label = QLabel("Selected organism: — none —")
        self.current_organism_label.setWordWrap(True)
        self.current_organism_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.reference_mode_banner)
        layout.addWidget(self.current_organism_label)
        layout.addWidget(QLabel("Available presets"))
        layout.addWidget(self.reference_list)
        layout.addWidget(choose)

        # Custom reference import
        layout.addWidget(QLabel("Custom reference"))
        form = QFormLayout()
        self.ref_organism = QLineEdit()
        self.ref_genome = QLineEdit()
        genome_browse = QPushButton("Browse")
        genome_browse.clicked.connect(lambda: self._pick_reference_file(self.ref_genome, "FASTA (*.fa *.fasta *.fa.gz *.fasta.gz)"))
        genome_row = QHBoxLayout()
        genome_row.addWidget(self.ref_genome)
        genome_row.addWidget(genome_browse)
        self.ref_annotation = QLineEdit()
        ann_browse = QPushButton("Browse")
        ann_browse.clicked.connect(lambda: self._pick_reference_file(self.ref_annotation, "Annotation (*.gtf *.gff3 *.gff *.gtf.gz *.gff3.gz)"))
        ann_row = QHBoxLayout()
        ann_row.addWidget(self.ref_annotation)
        ann_row.addWidget(ann_browse)
        self.ref_format = QComboBox()
        self.ref_format.addItems(["gtf", "gff3"])
        self.ref_format.setToolTip(
            "Annotation format of the file above. Choose gff3 for a GFF3 annotation; it is "
            "converted to GTF automatically before indexing and counting."
        )
        validate = QPushButton("Validate Reference")
        validate.clicked.connect(self._validate_reference_ui)
        use_custom = QPushButton("Use Custom Reference (writes lock)")
        use_custom.clicked.connect(self._use_custom_reference)
        form.addRow("Organism", self.ref_organism)
        form.addRow("Genome FASTA", genome_row)
        form.addRow("Annotation", ann_row)
        form.addRow("Format", self.ref_format)
        form.addRow(validate, use_custom)
        layout.addLayout(form)
        self.reference_details = QTextEdit()
        self.reference_details.setReadOnly(True)
        layout.addWidget(self.reference_details)
        self.tabs.addTab(self._scrollable(page), "Reference Manager")

    def _pick_reference_file(self, target: QLineEdit, filter_str: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select reference file", "", filter_str)
        if path:
            target.setText(path)

    def _validate_reference_ui(self) -> None:
        genome = Path(self.ref_genome.text())
        annotation = Path(self.ref_annotation.text())
        messages = validate_reference(genome, annotation)
        self.reference_details.setPlainText("Reference validation:\n" + self._format_messages(messages))

    def _use_custom_reference(self) -> None:
        if self.config is None or self.project_root is None:
            QMessageBox.warning(self, APP_NAME, "Create or open a project first.")
            return
        genome = Path(self.ref_genome.text())
        annotation = Path(self.ref_annotation.text())
        # .is_file() (not .exists()): an empty field is Path(".") which exists as a directory and
        # would pass .exists(), then md5sum/open would fail on the directory with a raw traceback.
        if not genome.is_file() or not annotation.is_file():
            QMessageBox.warning(self, APP_NAME, "Genome FASTA and annotation must exist.")
            return
        genome_md5 = md5sum(genome)
        lock_path = self.project_root / "references" / "project_reference.lock.yaml"
        existing = yaml.safe_load(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
        if existing and existing.get("locked") and existing.get("genome_md5") not in (None, genome_md5):
            reply = QMessageBox.question(
                self, APP_NAME,
                "A different reference is already locked for this project. Replace it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        annotation_md5 = md5sum(annotation)
        self.config.reference.mode = "custom"
        self.config.reference.organism_name = self.ref_organism.text().strip() or "custom"
        # If the custom organism name matches a catalog preset, seed its enrichment/
        # PPI ids; an unknown name leaves them None (the smk fallback applies).
        entry = catalog_entry_for_organism(self.config.reference.organism_name)
        if entry is not None:
            enr = self.config.enrichment
            enr.orgdb = entry.get("orgdb") or None
            enr.kegg_organism = entry.get("kegg_organism") or None
            enr.gprofiler_organism = entry.get("gprofiler_organism") or None
            self.config.ppi.taxon = entry.get("string_taxon")
            if self.config.input.type != "microarray":
                enr.keytype = entry.get("enrichment_keytype") or None
        # Store WSL-resolvable paths: reference staging and validate_reference.py
        # run inside WSL, where a Windows path (C:\...) would not exist. The md5s
        # above were computed on the native paths (readable on the Windows side).
        self.config.reference.genome_fasta = windows_to_wsl_path(genome)
        self.config.reference.annotation_file = windows_to_wsl_path(annotation)
        self.config.reference.annotation_format = self.ref_format.currentText()  # type: ignore[assignment]
        self.config.reference.genome_md5 = genome_md5
        self.config.reference.annotation_md5 = annotation_md5
        self.manager.save_config(self.project_root, self.config)
        lock = {
            "locked": True,
            "organism": self.config.reference.organism_name,
            "mode": "custom",
            "genome_path": str(genome),
            "annotation_path": str(annotation),
            "genome_md5": genome_md5,
            "annotation_md5": annotation_md5,
            "date_selected": date.today().isoformat(),
        }
        lock_path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
        self.reference_details.setPlainText(
            "Custom reference selected and locked:\n" + yaml.safe_dump(lock, sort_keys=False)
        )

    def _quantifier_valid_for(self, aligner: str) -> tuple[str, ...]:
        # Which quantifiers are valid for each aligner (mirrors the Snakefile's
        # _VALID_QUANTIFIERS). STAR offers a real choice; HISAT2 and Salmon each have one.
        return {
            "STAR": ("featureCounts", "STAR_GeneCounts"),
            "HISAT2": ("featureCounts",),
            "Salmon": ("Salmon_tximport",),
        }.get(aligner, ("featureCounts",))

    def _on_aligner_changed(self, name: str) -> None:
        # Constrain the quantifier to those valid for the chosen aligner. STAR can use
        # featureCounts (default) or STAR_GeneCounts, so the combo is editable; HISAT2 and
        # Salmon have a single quantifier, so the combo is shown but locked to it.
        if not hasattr(self, "quantifier"):
            return
        valid = self._quantifier_valid_for(name)
        model = self.quantifier.model()
        for i in range(self.quantifier.count()):
            item = model.item(i)
            if item is not None:
                item.setEnabled(self.quantifier.itemText(i) in valid)
        if self.quantifier.currentText() not in valid:
            self.quantifier.setCurrentText(valid[0])
        self.quantifier.setEnabled(len(valid) > 1)
        # RSeQC needs a genome BAM; the Salmon route has none, so the Snakefile skips it.
        # Grey the toggle out under Salmon so the setting can't be silently dropped.
        if hasattr(self, "rseqc"):
            self.rseqc.setEnabled(name != "Salmon")
            if name == "Salmon":
                self.rseqc.setChecked(False)

    def _build_workflow_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.aligner = QComboBox()
        self.aligner.addItems(["STAR", "HISAT2", "Salmon"])
        # STAR and HISAT2 align to a sorted BAM -> featureCounts; Salmon quantifies
        # transcripts directly (tximport). All three converge on the same gene counts
        # -> DESeq2 and the identical downstream.
        self.aligner.currentTextChanged.connect(self._on_aligner_changed)
        self.quantifier = QComboBox()
        self.quantifier.addItems(["featureCounts", "STAR_GeneCounts", "Salmon_tximport"])
        self.quantifier.setToolTip(
            "How reads are summarised to gene counts. STAR offers featureCounts (default) or "
            "STAR_GeneCounts (reuses STAR's own per-gene counts, no extra pass); HISAT2 uses "
            "featureCounts; Salmon uses tximport. All converge on the same gene-count matrix."
        )
        # Constrain to the valid quantifiers for the current aligner (set now and on change).
        self._on_aligner_changed(self.aligner.currentText())
        self.trim = QCheckBox()
        self.trim.setChecked(True)
        self.trim.setToolTip(
            "Adapter and quality trimming with fastp (recommended). Uncheck to skip trimming and "
            "send the raw reads straight to the aligner — use only if your reads are already trimmed."
        )
        self.rrna = QCheckBox()
        self.rrna.setToolTip(
            "Remove ribosomal RNA reads with SortMeRNA after trimming, before alignment.\n"
            "The default rRNA reference (~150 MB) is downloaded and indexed once per project "
            "(the index is a few GB on disk). Useful for total-RNA / ribo-depleted libraries; "
            "poly-A selected libraries usually have little rRNA and may not need it."
        )
        # Trimmer selector (opt-in alternatives to fastp; enabled only when trimming is on).
        self.trimmer = QComboBox()
        self.trimmer.addItem("fastp (default)", "fastp")
        self.trimmer.addItem("Trim Galore", "trim-galore")
        self.trimmer.addItem("Trimmomatic", "trimmomatic")
        self.trimmer.setToolTip(
            "Adapter and quality trimmer. fastp (default) is fast and detects adapters "
            "automatically. Trim Galore (Cutadapt) and Trimmomatic are established alternatives. "
            "The quality (-q) and minimum-length settings apply to all three; poly-G is fastp-only. "
            "All three produce the same trimmed reads for the rest of the pipeline."
        )
        self.trim.toggled.connect(lambda on: self.trimmer.setEnabled(on))
        self.trimmer.setEnabled(self.trim.isChecked())
        # rRNA removal tool (enabled only when rRNA filtering is on).
        self.rrna_tool = QComboBox()
        self.rrna_tool.addItem("SortMeRNA (default)", "sortmerna")
        self.rrna_tool.addItem("RiboDetector", "ribodetector")
        self.rrna_tool.setToolTip(
            "Tool used when rRNA filtering is on. SortMeRNA (reference-based, default) downloads a "
            "~150 MB rRNA database and indexes it once. RiboDetector is a reference-free "
            "machine-learning classifier (no database download); it needs the full environment."
        )
        self.rrna.toggled.connect(lambda on: self.rrna_tool.setEnabled(on))
        self.rrna_tool.setEnabled(self.rrna.isChecked())
        # Contamination screening (FastQ Screen): a QC report, not a filter.
        self.contam_screen = QCheckBox()
        self.contam_screen.setToolTip(
            "Screen a read subsample against a panel of reference genomes (FastQ Screen) and report "
            "the percentage matching each — a contamination QC report, not a filter (no reads are "
            "removed). Requires a FastQ Screen config file (set it under Advanced parameters); the "
            "screen is skipped if none is given. Results appear in the MultiQC report. Leave off "
            "unless you suspect cross-species or vector/rRNA contamination."
        )
        self.enrichment = QCheckBox()
        self.enrichment.setChecked(True)
        self.enrichment.toggled.connect(lambda _=False: self._update_enrichment_warning())
        self.enrichment_warn = QLabel(
            "⚠ No organism is configured — select one on the Reference Manager tab, "
            "or GO/KEGG enrichment and the STRING PPI network will be skipped.")
        self.enrichment_warn.setWordWrap(True)
        self.enrichment_warn.setStyleSheet("color: #8B5200;")
        self.enrichment_warn.setVisible(False)
        self.figures = QCheckBox()
        self.figures.setChecked(True)
        self.gsva = QCheckBox()
        self.gsva.setToolTip(
            "GSVA sample-level pathway activity scores, computed against your custom gene sets "
            "(set them under Custom gene sets). Organism-safe: it uses only your gene sets, so it "
            "works for non-model organisms. Descriptive scores, not a significance test. Needs a "
            "custom GMT; ignored otherwise.")
        self.rseqc = QCheckBox()
        self.rseqc.setToolTip(
            "Extended alignment QC with RSeQC: read genomic-context distribution (exon / intron / "
            "intergenic) and 5' to 3' gene-body coverage, added to the MultiQC report. Needs a "
            "genome BAM, so it is unavailable on the Salmon route.")
        self.meta_analysis = QCheckBox()
        self.meta_analysis.setToolTip(
            "Multi-study meta-analysis: when the sample sheet carries a 'dataset' (study-of-origin) "
            "column with more than one study, run a per-study DESeq2 -> metaRNASeq inverse-normal "
            "p-combination + metafor effect-size pooling, with a dedicated cross-study comparative "
            "report (convergent/discordant genes, forest, concordance, shared-vs-distinct "
            "enrichment). Runs alongside the joint DESeq2. Ignored for single-study, microarray and "
            "results-upload runs.")
        self.per_study_enrichment = QCheckBox()
        self.per_study_enrichment.setToolTip(
            "Opt-in and slow: run the full GO/KEGG enrichment for every study in the "
            "meta-analysis, not just the pooled cross-study enrichment. Only available when "
            "multi-study meta-analysis is on.")
        # Dependent enable: only meaningful when meta-analysis is active.
        self.meta_analysis.toggled.connect(
            lambda on: self.per_study_enrichment.setEnabled(self.meta_analysis.isEnabled() and on))
        # fastp parameters
        self.fastp_q = QSpinBox()
        self.fastp_q.setRange(0, 40)
        self.fastp_q.setValue(15)
        self.fastp_len = QSpinBox()
        self.fastp_len.setRange(0, 300)
        self.fastp_len.setValue(36)
        self.trim_poly_g = QCheckBox()
        # DESeq2 design + contrast builder
        self.design = QLineEdit("~ condition")
        self.contrast_factor = QLineEdit("condition")
        self.numerator = QComboBox()
        self.numerator.setEditable(True)
        self.denominator = QComboBox()
        self.denominator.setEditable(True)
        self.reference_level = QComboBox()
        self.reference_level.setEditable(True)
        self.contrast_info = QLabel("")
        self.contrast_info.setWordWrap(True)
        self.contrast_info.setStyleSheet("color: #5A6472;")
        self.contrast_info.setVisible(False)
        refresh = QPushButton("Refresh conditions from metadata")
        refresh.clicked.connect(self._refresh_conditions)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.0001, 0.5)
        self.alpha.setSingleStep(0.01)
        self.alpha.setDecimals(4)
        self.alpha.setValue(0.05)
        self.lfc_threshold = QDoubleSpinBox()
        self.lfc_threshold.setRange(0.0, 10.0)
        self.lfc_threshold.setSingleStep(0.25)
        self.lfc_threshold.setDecimals(2)
        self.lfc_threshold.setValue(1.0)
        # Differential-expression engine (count-based routes). DESeq2 is the default;
        # limma-voom is an opt-in cross-check emitting the same tables/figures.
        self.de_engine = QComboBox()
        self.de_engine.addItem("DESeq2 (default)", "DESeq2")
        self.de_engine.addItem("limma-voom", "limma-voom")
        self.de_engine.addItem("edgeR (QLF)", "edgeR")
        self.de_engine.setToolTip(
            "Statistical engine for the differential test on count data. DESeq2 (default) suits "
            "most designs, including small ones. limma-voom and edgeR quasi-likelihood are optional "
            "cross-checks best suited to larger designs (about 6+ samples per group); at small n "
            "keep DESeq2. All three produce the same result tables and figures. Not used in "
            "microarray mode (which uses limma-trend) or when a ready DESeq2 results table is uploaded."
        )
        save = QPushButton("Save Workflow Settings")
        save.setProperty("primary", True)
        save.clicked.connect(self._save_workflow_settings)

        # Group the 14 settings into three labelled cards (Alignment & read
        # processing / Differential expression / Outputs) so the tab reads as
        # sections rather than one flat field list.
        align_group = QGroupBox("Alignment and read processing")
        align_form = QFormLayout(align_group)
        align_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        align_form.addRow(self._info_label("Aligner", "Read aligner. STAR (default) suits most studies; HISAT2 uses far less memory and still makes BAMs; Salmon is alignment-free, lowest memory, best for very large genomes. All three give the same gene counts. If unsure, keep STAR."), self.aligner)
        align_form.addRow(self._info_label("Quantifier", "How reads are summarised to gene counts. STAR can use featureCounts (default) or STAR_GeneCounts (STAR's own per-gene counts, no extra pass); HISAT2 uses featureCounts and Salmon uses tximport (those are fixed)."), self.quantifier)
        align_form.addRow(self._info_label("Read trimming", "Adapter and quality trimming (recommended). Uncheck only if your reads are already trimmed. Pick the trimmer below."), self.trim)
        align_form.addRow(self._info_label("Trimmer", "fastp (default), Trim Galore, or Trimmomatic. Opt-in alternatives to fastp; all three yield the same trimmed reads for the rest of the pipeline. Enabled only when trimming is on."), self.trimmer)
        align_form.addRow(self._info_label("fastp quality (-q)", "Minimum acceptable per-base Phred quality. Bases below this count as low quality. fastp default 15."), self.fastp_q)
        align_form.addRow(self._info_label("fastp min length (-l)", "Reads shorter than this (after trimming) are discarded. Protocol default 36."), self.fastp_len)
        align_form.addRow(self._info_label("fastp poly-G (-g)", "Trim poly-G tails, an artefact of 2-colour chemistry (NextSeq/NovaSeq). Leave off for HiSeq/MiSeq."), self.trim_poly_g)
        align_form.addRow("rRNA filtering", self.rrna)
        align_form.addRow(self._info_label("rRNA tool", "SortMeRNA (default, reference-based, ~150 MB database) or RiboDetector (reference-free, no database). Used only when rRNA filtering is on."), self.rrna_tool)
        align_form.addRow(self._info_label("Contamination screen", "Optional FastQ Screen report of the % of reads matching a panel of reference genomes — a QC report, not a filter. Needs a FastQ Screen config (set it under Advanced parameters); skipped if none is given. Results appear in MultiQC."), self.contam_screen)
        self.align_group = align_group
        layout.addWidget(align_group)

        de_group = QGroupBox("Differential expression")
        de_form = QFormLayout(de_group)
        de_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        de_form.addRow(self._info_label("DE engine", "Statistical engine for the differential test on count data. DESeq2 (default) fits most studies, including small ones; limma-voom is an optional cross-check for larger designs (about 6+ samples per group). Both write the same result tables and figures. Ignored in microarray mode and for DESeq2-results uploads."), self.de_engine)
        de_form.addRow(self._info_label("Design formula", "R model formula used by every engine. The last term is the effect of interest; put known batch effects before it, e.g. '~ batch + condition'."), self.design)
        design_helper = QPushButton("Design helper: adjust for batch / covariates…")
        design_helper.setToolTip(
            "Compose the design formula from your metadata columns without typing R. Tick the "
            "batch/covariate columns to adjust for; the condition of interest is added last.")
        design_helper.clicked.connect(self._open_design_helper)
        de_form.addRow("", design_helper)
        de_form.addRow(refresh)
        de_form.addRow(self._info_label("Contrast factor", "The metadata column compared in the differential test (usually 'condition')."), self.contrast_factor)
        de_form.addRow(self._info_label("Numerator (treated)", "The group whose change is measured. log2 fold change is numerator relative to denominator."), self.numerator)
        de_form.addRow(self._info_label("Denominator (reference)", "The baseline group. Positive log2 fold change = higher in the numerator than this."), self.denominator)
        de_form.addRow(self._info_label("Reference level", "The factor's baseline level (normally the same as the denominator); DESeq2 releveled to this."), self.reference_level)
        de_form.addRow("", self.contrast_info)
        de_form.addRow(self._info_label("Alpha (padj/FDR)", "Significance threshold on the Benjamini-Hochberg adjusted p-value (false discovery rate). Default 0.05."), self.alpha)
        de_form.addRow(self._info_label("log2FC threshold", "Minimum absolute log2 fold change for a gene to count as up/down-regulated. |log2FC| >= this AND padj < alpha. Default 1.0 (a 2-fold change)."), self.lfc_threshold)
        de_form.addRow(QLabel("featureCounts strandedness is auto-inferred per protocol."))
        self.organellar = QComboBox()
        self.organellar.addItem("Keep (include in analysis)", "keep")
        self.organellar.addItem("Discard before differential expression", "discard")
        self.organellar.addItem("Analyse separately (nuclear DE + organellar subset)", "separate")
        de_form.addRow(self._info_label(
            "Mitochondrial / chloroplast genes",
            "Organellar (mitochondrial + chloroplast) transcripts can dominate library size and "
            "skew DESeq2 normalization. Keep them, discard them before the differential test, or "
            "analyse them separately (the main DE runs on nuclear genes only; a separate organellar "
            "count subset and a per-sample organellar-fraction table are written). Applies to "
            "STAR/HISAT2/Salmon runs (needs a reference genome)."), self.organellar)
        self.de_group = de_group
        layout.addWidget(de_group)

        # ---- Advanced tool parameters (collapsible). Defaults reproduce the validated
        # behaviour; users can set each tool's important parameters manually here. ----
        adv_group = QGroupBox("Advanced parameters")
        adv_outer = QVBoxLayout(adv_group)
        self.adv_toggle = QCheckBox("Show advanced tool parameters")
        self.adv_toggle.setToolTip(
            "Per-tool parameters for fine control. The defaults reproduce the validated pipeline "
            "behaviour, so leave them unless you have a specific reason to change them.")
        self.adv_container = QWidget()
        adv_form = QFormLayout(self.adv_container)
        adv_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fastp_u = QSpinBox(); self.fastp_u.setRange(0, 100); self.fastp_u.setValue(40)
        self.fastp_polyx = QCheckBox()
        adv_form.addRow(self._info_label("fastp: unqualified % limit (-u)", "Maximum percentage of low-quality bases before a read is discarded. fastp default 40."), self.fastp_u)
        adv_form.addRow(self._info_label("fastp: trim poly-X (3')", "Trim 3' poly-A/poly-X tails (degraded or 3'-biased libraries). fastp default off."), self.fastp_polyx)
        self.tm_sw_q = QSpinBox(); self.tm_sw_q.setRange(0, 40); self.tm_sw_q.setValue(15)
        self.tm_leading = QSpinBox(); self.tm_leading.setRange(0, 40); self.tm_leading.setValue(3)
        self.tm_trailing = QSpinBox(); self.tm_trailing.setRange(0, 40); self.tm_trailing.setValue(3)
        adv_form.addRow(self._info_label("Trimmomatic: sliding-window quality", "Average Phred required over a 4-base sliding window (SLIDINGWINDOW:4:Q). Default 15."), self.tm_sw_q)
        adv_form.addRow(self._info_label("Trimmomatic: leading quality", "Trim leading bases below this quality (LEADING). Default 3."), self.tm_leading)
        adv_form.addRow(self._info_label("Trimmomatic: trailing quality", "Trim trailing bases below this quality (TRAILING). Default 3."), self.tm_trailing)
        self.rd_ensure = QComboBox()
        for _lbl, _v in (("norrna (keep confident non-rRNA)", "norrna"), ("rrna", "rrna"), ("both", "both"), ("none", "none")):
            self.rd_ensure.addItem(_lbl, _v)
        self.rd_chunk = QSpinBox(); self.rd_chunk.setRange(16, 4096); self.rd_chunk.setValue(256)
        adv_form.addRow(self._info_label("RiboDetector: ensure mode (-e)", "Which class is kept with high confidence. norrna keeps high-confidence non-rRNA reads (recommended)."), self.rd_ensure)
        adv_form.addRow(self._info_label("RiboDetector: chunk size", "Reads per batch (x1024): a memory/speed trade-off. Default 256."), self.rd_chunk)
        self.fs_subset = QSpinBox(); self.fs_subset.setRange(1000, 5000000); self.fs_subset.setSingleStep(10000); self.fs_subset.setValue(100000)
        adv_form.addRow(self._info_label("Contamination: reads subsampled", "How many reads FastQ Screen subsamples per sample. Default 100000."), self.fs_subset)
        self.fs_conf = QLineEdit()
        fs_conf_browse = QPushButton("Browse")
        fs_conf_browse.clicked.connect(lambda: self._pick_reference_file(self.fs_conf, "FastQ Screen config (*.conf *.txt);;All files (*)"))
        fs_conf_row = QHBoxLayout(); fs_conf_row.addWidget(self.fs_conf); fs_conf_row.addWidget(fs_conf_browse)
        fs_conf_widget = QWidget(); fs_conf_widget.setLayout(fs_conf_row)
        adv_form.addRow(self._info_label("Contamination: FastQ Screen config", "Path to a fastq_screen.conf listing the bowtie2 genome indexes to screen against (required to run the screen). The built-in genome auto-download is not used; point this at a panel you already have."), fs_conf_widget)
        self.star_twopass = QCheckBox()
        self.star_multimap = QSpinBox(); self.star_multimap.setRange(1, 200); self.star_multimap.setValue(10)
        self.star_mismatch = QDoubleSpinBox(); self.star_mismatch.setRange(0.0, 1.0); self.star_mismatch.setSingleStep(0.02); self.star_mismatch.setDecimals(2); self.star_mismatch.setValue(1.0)
        adv_form.addRow(self._info_label("STAR: two-pass mode", "Two-pass mapping improves novel-junction detection (slower). STAR default off."), self.star_twopass)
        adv_form.addRow(self._info_label("STAR: max multimappers", "Reads mapping to more than this many loci are discarded (outFilterMultimapNmax). Default 10."), self.star_multimap)
        adv_form.addRow(self._info_label("STAR: max mismatch ratio", "Max mismatches as a fraction of read length (outFilterMismatchNoverReadLmax). 1.0 = STAR default."), self.star_mismatch)
        self.fc_feature = QLineEdit("exon")
        self.fc_attribute = QLineEdit("gene_id")
        adv_form.addRow(self._info_label("featureCounts: feature type", "GTF feature counted (-t). Default exon."), self.fc_feature)
        adv_form.addRow(self._info_label("featureCounts: attribute type", "GTF attribute grouped into genes (-g). Default gene_id."), self.fc_attribute)
        self.de_min_count = QSpinBox(); self.de_min_count.setRange(0, 1000); self.de_min_count.setValue(10)
        self.de_shrink = QComboBox()
        for _v in ("apeglm", "ashr", "normal"):
            self.de_shrink.addItem(_v, _v)
        adv_form.addRow(self._info_label("DESeq2: min count prefilter", "Keep genes with at least this many reads in the smallest group. Default 10 (the validated value)."), self.de_min_count)
        adv_form.addRow(self._info_label("DESeq2: LFC shrinkage", "lfcShrink estimator for the MA/volcano effect sizes. Default apeglm (the validated value)."), self.de_shrink)
        self.adv_container.setVisible(False)
        self.adv_toggle.toggled.connect(self.adv_container.setVisible)
        adv_outer.addWidget(self.adv_toggle)
        adv_outer.addWidget(self.adv_container)
        layout.addWidget(adv_group)

        out_group = QGroupBox("Outputs")
        out_form = QFormLayout(out_group)
        out_form.addRow("Enrichment", self.enrichment)
        out_form.addRow("", self.enrichment_warn)
        out_form.addRow("Figures", self.figures)
        out_form.addRow(self._info_label("GSVA pathway activity", "Sample-level gene-set activity scores from your custom gene sets (organism-safe). Needs a custom GMT under Custom gene sets."), self.gsva)
        out_form.addRow(self._info_label("Extended QC (RSeQC)", "Read-distribution + gene-body-coverage QC added to the MultiQC report. Genome-BAM routes only (not Salmon)."), self.rseqc)
        out_form.addRow(self._info_label("Multi-study meta-analysis", "Combine 2+ studies (a 'dataset' column with >1 study): per-study DESeq2 + inverse-normal p-combination + effect-size pooling, with a cross-study comparative report. Ignored for single-study / microarray / results-upload."), self.meta_analysis)
        out_form.addRow(self._info_label("Per-study enrichment (opt-in — slow: runs enrichment for every study)", "Run the full GO/KEGG enrichment separately for each study in the meta-analysis. Slow; off by default. Requires multi-study meta-analysis."), self.per_study_enrichment)
        self.out_group = out_group
        layout.addWidget(out_group)

        cs_group = QGroupBox("Custom gene sets (enrichment, optional)")
        cs_form = QFormLayout(cs_group)
        cs_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        cs_help = QLabel(
            "Run enrichment against your own gene sets, alongside the built-in GO/KEGG. The gene "
            "IDs in these files must use the same identifier format as your reference (locus tags, "
            "Ensembl/RefSeq IDs, or symbols); a mismatch is flagged in the custom-enrichment check.")
        cs_help.setWordWrap(True)  # without this the long label is clipped at the panel edge
        cs_form.addRow(cs_help)
        self.custom_gmt = QLineEdit()
        self.custom_annot = QLineEdit()
        self.custom_background = QLineEdit()
        for label, le, filt, tip in (
            ("Gene-set GMT", self.custom_gmt, "Gene sets (*.gmt)",
             "A .gmt collection (one set per line: name, description, gene1, gene2, ...). Drives a custom ORA + GSEA on the DE results."),
            ("Annotation table", self.custom_annot, "Annotation table (*.tsv *.csv *.txt)",
             "Optional id->term table: column 1 = gene id, column 2 = term, optional column 3 = term name."),
            ("Background gene list", self.custom_background, "Gene list (*.txt *.tsv *.csv)",
             "Optional ORA universe (one gene id per line). Defaults to the tested genes if left blank."),
        ):
            le.setToolTip(tip)
            browse = QPushButton("Browse")
            browse.clicked.connect(lambda _=False, t=le, f=filt: self._pick_reference_file(t, f))
            holder_row = QHBoxLayout()
            holder_row.addWidget(le)
            holder_row.addWidget(browse)
            holder = QWidget()
            holder.setLayout(holder_row)
            cs_form.addRow(self._info_label(label, tip), holder)
        layout.addWidget(cs_group)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        layout.addStretch(1)
        self.tabs.addTab(self._scrollable(page), "Workflow Settings")

    def _open_design_helper(self) -> None:
        # Compose an additive design formula (~ covariates + condition) from the metadata
        # columns, so a non-expert can adjust for batch/covariates without typing R. Only
        # additive terms; interactions stay in the raw formula field.
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        cols = list(self.metadata_table.column_names()) if hasattr(self.metadata_table, "column_names") else []
        factor = self.contrast_factor.text().strip() or "condition"
        exclude = {"sample_id", "fastq_1", "fastq_2", "fastq_1_url", "fastq_2_url", "layout",
                   "original_accession", "experiment_accession", "gsm_accession", "platform",
                   "original_filename", "detected_pair_id", "condition", factor}
        candidates = [c for c in cols if c and c not in exclude]
        dlg = QDialog(self)
        dlg.setWindowTitle("Design helper")
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        _dh_help = QLabel(
            "Adjust the differential test for known batch / covariate columns. They are added "
            f"additively before the effect of interest:\n    ~ [covariates] + {factor}\n"
            "Interactions (e.g. genotype:treatment) must be typed in the formula field.")
        _dh_help.setWordWrap(True)
        lay.addWidget(_dh_help)
        current = self.design.text()
        boxes: list[tuple[str, QCheckBox]] = []
        if not candidates:
            _dh_none = QLabel("No extra metadata columns found — add columns on the Metadata tab first.")
            _dh_none.setWordWrap(True)
            lay.addWidget(_dh_none)
        for c in candidates:
            cb = QCheckBox(c)
            cb.setChecked(c in current.split())
            lay.addWidget(cb)
            boxes.append((c, cb))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = [c for c, cb in boxes if cb.isChecked()]
            self.design.setText("~ " + " + ".join(chosen + [factor]))

    def _refresh_conditions(self) -> None:
        df = self.metadata_table.to_dataframe()
        # Use the configured contrast factor column (not a hardcoded "condition"), matching
        # _open_design_helper / _save_workflow_settings, so a custom factor populates correctly.
        factor = self.contrast_factor.text().strip() or "condition"
        if factor not in df.columns:
            return
        values = sorted({str(v) for v in df[factor].tolist() if str(v) and str(v) != "unknown"})
        if not values:
            for combo in (self.numerator, self.denominator, self.reference_level):
                combo.clear()
            return
        # Distinct defaults so the contrast is never X_vs_X (which DESeq2 rejects):
        # denominator/reference = a control-like level if one is present, numerator
        # = a different level. A valid prior user pick is preserved.
        control_keys = ("control", "ctrl", "untreated", "wildtype", "wild-type", "wt",
                        "mock", "dmso", "vehicle", "baseline", "normal")
        reference = next((v for v in values if any(k in v.lower() for k in control_keys)), values[0])
        treated = next((v for v in values if v != reference), reference)
        defaults = {id(self.numerator): treated,
                    id(self.denominator): reference,
                    id(self.reference_level): reference}
        for combo in (self.numerator, self.denominator, self.reference_level):
            current = combo.currentText().strip()
            combo.clear()
            combo.addItems(values)
            combo.setCurrentText(current if current in values else defaults[id(combo)])

    def _busy_bar(self) -> QProgressBar:
        # An indeterminate "busy" bar (hidden until an action runs).
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        bar.setVisible(False)
        return bar

    def _build_resources_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        # System Information: a friendly summary instead of a raw key/value dump.
        system_group = QGroupBox("System Information")
        system_layout = QVBoxLayout(system_group)
        self.system_info_label = QLabel("Click 'Detect and Recommend' to scan your computer.")
        self.system_info_label.setWordWrap(True)
        self.recommendation_label = QLabel()
        self.recommendation_label.setWordWrap(True)
        detect = QPushButton("Detect and Recommend")
        detect.setProperty("primary", True)
        detect.clicked.connect(self._detect_resources)
        system_layout.addWidget(self.system_info_label)
        system_layout.addWidget(self.recommendation_label)
        system_layout.addWidget(detect)
        # WSL2 caps the VM's RAM/CPU (default ~50% of host) below the machine total, and that
        # cap — not the host total — bounds memory-heavy steps. Let the user raise it here
        # instead of hand-editing %UserProfile%\.wslconfig. Windows-only (no WSL on Linux).
        if sys.platform.startswith("win"):
            self.wsl_limits_btn = QPushButton("Edit WSL2 memory / CPU limits…")
            self.wsl_limits_btn.setToolTip(
                "Set the RAM and processor caps of the WSL2 virtual machine the pipeline runs in "
                "(%UserProfile%\\.wslconfig [wsl2]). Raising memory helps STAR on large genomes.")
            self.wsl_limits_btn.clicked.connect(self._edit_wsl_limits)
            system_layout.addWidget(self.wsl_limits_btn)
        self.resources_busy = self._busy_bar()
        system_layout.addWidget(self.resources_busy)
        layout.addWidget(system_group)

        # Resource profile: plain-language presets with an info button.
        profile_group = QGroupBox("Resource Profile")
        profile_form = QFormLayout(profile_group)
        self.profile = QComboBox()
        self.profile.addItems(["balanced", "low", "high", "custom"])  # lowercase: matches config
        self.profile.currentTextChanged.connect(self._on_profile_changed)
        profile_help = (
            "Balanced uses about 75% of your CPU and memory and suits most runs. "
            "Low is conservative if you are using other programs at the same time. "
            "High uses about 90% for a dedicated machine. "
            "Custom keeps the cores and memory you set below."
        )
        profile_form.addRow(self._info_label("Profile", profile_help), self.profile)
        layout.addWidget(profile_group)

        # Manual adjustment: plain-language labels for cores and memory.
        manual_group = QGroupBox("Manual Adjustment")
        manual_form = QFormLayout(manual_group)
        self.cores = QSpinBox()
        self.cores.setRange(1, 256)
        self.ram = QSpinBox()
        self.ram.setRange(1, 2048)
        manual_form.addRow(
            self._info_label("CPU cores to use",
                             "Number of processor cores the pipeline may use. Detect first to see how many your computer has."),
            self.cores)
        manual_form.addRow(
            self._info_label("Memory (GB)",
                             "RAM allocated to the pipeline. Alignment (STAR) is the most memory-intensive step."),
            self.ram)
        save = QPushButton("Save Resources")
        save.setToolTip("Persist the CPU core and memory allocation above to the project config.")
        save.clicked.connect(self._save_resources)
        manual_form.addRow(save)
        layout.addWidget(manual_group)

        layout.addStretch(1)
        self.tabs.addTab(self._scrollable(page), "Resources")

    def _edit_wsl_limits(self) -> None:
        from app.core.wslconfig import (
            apply_wsl_shutdown, read_wsl2_limits, write_wsl2_limits,
        )
        cur = read_wsl2_limits()
        cur_mem = int("".join(ch for ch in str(cur.get("memory") or "") if ch.isdigit()) or 0)
        cur_proc = int(cur.get("processors") or 0)
        sysinfo = getattr(self, "_last_system", None)
        host_gb = int(getattr(sysinfo, "total_ram_gb", 0) or 0) or 2048
        host_cpu = int(getattr(sysinfo, "logical_threads", 0) or 0) or 256

        dlg = QDialog(self)
        dlg.setWindowTitle("WSL2 memory / CPU limits")
        form = QFormLayout(dlg)
        note = QLabel(
            "These cap the WSL2 virtual machine the pipeline runs in (%UserProfile%\\.wslconfig). "
            "0 means leave WSL's default (about half your RAM, all CPUs). Changes take effect after "
            "WSL restarts.")
        note.setWordWrap(True)
        form.addRow(note)
        mem = QSpinBox(); mem.setRange(0, max(host_gb, 8)); mem.setValue(cur_mem); mem.setSuffix(" GB")
        proc = QSpinBox(); proc.setRange(0, max(host_cpu, 1)); proc.setValue(cur_proc)
        form.addRow("Memory cap (0 = default)", mem)
        form.addRow("Processors (0 = default)", proc)
        box = QDialogButtonBox()
        save_btn = box.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn = box.addButton("Save && restart WSL now", QDialogButtonBox.ButtonRole.ApplyRole)
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        form.addRow(box)

        def do_save(and_apply: bool) -> None:
            path = write_wsl2_limits(mem.value() or None, proc.value() or None)
            if and_apply:
                ok, msg = apply_wsl_shutdown()
                QMessageBox.information(self, APP_NAME, f"Saved {path}.\n\n{msg}")
            else:
                QMessageBox.information(
                    self, APP_NAME,
                    f"Saved {path}.\n\nRestart WSL to apply — click 'Save & restart WSL now', or run "
                    "'wsl --shutdown'. Then click 'Detect and Recommend' again to re-read the caps.")
            dlg.accept()

        save_btn.clicked.connect(lambda: do_save(False))
        apply_btn.clicked.connect(lambda: do_save(True))
        box.rejected.connect(dlg.reject)
        dlg.exec()

    def _build_runtime_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        estimate = QPushButton("Estimate Runtime")
        estimate.setProperty("primary", True)
        estimate.setToolTip(
            "Estimate wall-clock runtime from your sample count, input mode, and resource settings, "
            "calibrated against past runs on this machine.")
        estimate.clicked.connect(self._estimate_runtime)
        self.runtime_busy = self._busy_bar()
        self.runtime_text = QTextEdit()
        self.runtime_text.setReadOnly(True)
        self.runtime_text.setPlaceholderText(
            "Click Estimate Runtime for a wall-clock estimate based on your sample count and settings.")
        estimate_row = QHBoxLayout()
        estimate_row.addWidget(estimate)
        estimate_row.addStretch(1)
        layout.addLayout(estimate_row)
        layout.addWidget(self.runtime_busy)
        layout.addWidget(self.runtime_text)
        self.tabs.addTab(self._scrollable(page), "Runtime")

    def _build_sanity_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        run = QPushButton("Run Project and Metadata Checks")
        run.setProperty("primary", True)
        run.setToolTip(
            "Validate the project config and samples.tsv (sample sheet consistency, design/contrast "
            "sanity, file paths) before starting a run.")
        run.clicked.connect(self._run_sanity_checks)
        refresh = QPushButton("Refresh Phase Checks")
        refresh.setToolTip("Re-run the per-phase readiness checks against the project's current settings.")
        refresh.clicked.connect(self._refresh_phase_checks)
        buttons.addWidget(run)
        buttons.addWidget(refresh)
        self.approve_review = QCheckBox("I have reviewed and approved the items flagged for review above")
        self.sanity_busy = self._busy_bar()
        self.sanity_text = QTextEdit()
        self.sanity_text.setReadOnly(True)
        self.sanity_text.setPlaceholderText(
            "Run the project and metadata checks before starting, to catch configuration issues early.")
        layout.addLayout(buttons)
        layout.addWidget(self.approve_review)
        layout.addWidget(self.sanity_busy)
        layout.addWidget(self.sanity_text)
        self.tabs.addTab(self._scrollable(page), "Sanity Checks")

    def _build_run_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        # Resume banner: shown when a project is reopened with an interrupted (locked/incomplete) run,
        # so stop -> close -> reopen -> continue is one click. Amber warning styling reads on both themes.
        self.resume_banner = QLabel()
        self.resume_banner.setWordWrap(True)
        self.resume_banner.setStyleSheet(
            "background:#FFF4E5; color:#663C00; border:1px solid #E0A96D; border-radius:6px; padding:8px;")
        self.resume_banner.setVisible(False)
        self.resume_button = QPushButton("Resume Interrupted Run")
        self.resume_button.setProperty("primary", True)
        self.resume_button.setVisible(False)
        self.resume_button.clicked.connect(self._resume_interrupted)
        _banner_row = QHBoxLayout()
        _banner_row.addWidget(self.resume_banner, 1)
        _banner_row.addWidget(self.resume_button, 0)
        layout.addLayout(_banner_row)
        buttons = QHBoxLayout()
        run_tips = {
            "dry-run": "Show what the pipeline would do, without running anything.",
            "run": "Start the pipeline. Completed steps are reused; only missing outputs are produced.",
            "resume": "Continue a stopped or interrupted run from where it left off "
                      "(re-runs only incomplete/missing steps — the project is the saved state).",
            "unlock": "Release a stale lock left by a killed run so you can start again.",
        }
        for text, mode in [("Dry Run", "dry-run"), ("Start Run", "run"), ("Resume", "resume"), ("Unlock", "unlock")]:
            button = QPushButton(text)
            button.setToolTip(run_tips.get(mode, ""))
            if mode == "run":
                button.setProperty("primary", True)
            button.clicked.connect(lambda _checked=False, m=mode: self._start_snakemake(m))
            self.run_action_buttons[mode] = button
            buttons.addWidget(button)
        self.use_wsl = QCheckBox("Use WSL2")
        # WSL2 exists only on Windows; on Linux/macOS the pipeline runs natively in the local
        # micromamba environment, so default the toggle off and hide it there.
        _is_windows = sys.platform.startswith("win")
        self.use_wsl.setChecked(_is_windows)
        self.use_wsl.setVisible(_is_windows)
        self.use_wsl.setToolTip(
            "Run the pipeline inside the WSL2 Ubuntu distribution instead of natively on Windows. "
            "Recommended: the Linux toolchain (Snakemake, aligners, R/Bioconductor) is the validated "
            "route on Windows. Unchecked runs natively on Windows if a local environment is set up.")
        buttons.addWidget(self.use_wsl)
        actions = QHBoxLayout()
        stop = QPushButton("Stop")
        stop.setEnabled(False)
        stop.setToolTip("Terminate the running pipeline. Already-completed steps are kept and can be resumed later.")
        stop.clicked.connect(self._stop_run)
        self.stop_button = stop
        open_folder = QPushButton("Open Project Folder")
        open_folder.setToolTip("Open the project's root directory in the system file browser.")
        open_folder.clicked.connect(self._open_folder)
        open_report = QPushButton("Open MultiQC Report")
        open_report.setToolTip(
            "Open the aggregated MultiQC quality-control report (read QC, alignment/quantification "
            "metrics) in your browser. Produced when a run finishes.")
        open_report.clicked.connect(self._open_report)
        open_html = QPushButton("Open Results Report")
        open_html.setToolTip(
            "Open the self-contained HTML results report (figures, top genes, enrichment, and "
            "provenance in one file) in your browser. Produced when a run finishes.")
        open_html.clicked.connect(self._open_results_report)
        self.export_toolsref_button = QPushButton("Export Tools && References")
        self.export_toolsref_button.setToolTip(
            "Save a text file listing the tool versions, reference genome/annotation (accession, "
            "source, MD5) and enrichment database sources used in this run. Available after the run completes.")
        self.export_toolsref_button.clicked.connect(self._export_tools_references)
        self.export_design_button = QPushButton("Export Study Design")
        self.export_design_button.setToolTip(
            "Save a text file describing the study design: samples, conditions, layout, the DESeq2 "
            "design formula and contrasts. Available after the run completes.")
        self.export_design_button.clicked.connect(self._export_study_design)
        for w in (stop, open_folder, open_report, open_html, self.export_toolsref_button, self.export_design_button):
            actions.addWidget(w)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.elapsed_label = QLabel("Elapsed: 00:00:00")
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)
        self._run_start = 0.0
        self.command_text = QLineEdit()
        self.command_text.setReadOnly(True)  # displays the launched command; not user-editable
        self.status_label = QLabel("Idle")
        # Plain-language "current phase" line so non-CLI users can follow along;
        # the raw Snakemake log below is for power users.
        self.phase_label = QLabel("Ready — open a project, configure it, then click Start Run.")
        phase_font = self.phase_label.font()
        phase_font.setPointSize(phase_font.pointSize() + 2)
        phase_font.setBold(True)
        self.phase_label.setFont(phase_font)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("The Snakemake log streams here once a run starts.")
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress)
        progress_row.addWidget(self.elapsed_label)
        layout.addLayout(buttons)
        layout.addLayout(actions)
        layout.addWidget(self.phase_label)
        layout.addLayout(progress_row)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Command"))
        layout.addWidget(self.command_text)
        layout.addWidget(QLabel("Detailed log"))
        layout.addWidget(self.log_text)
        self.run_monitor_page = page
        self.tabs.addTab(page, "Run Monitor")
        self._refresh_export_buttons()

    def _set_run_status(self, text: str, color: str | None = None) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")

    def _set_running_ui(self, active: bool) -> None:
        # Only run/resume hold a live process; dry-run/unlock are short-lived but
        # still gate Start to avoid concurrent snakemake against one directory.
        self._run_active = active
        for button in self.run_action_buttons.values():
            button.setEnabled(not active)
        if self.stop_button is not None:
            self.stop_button.setEnabled(active)
        if active:
            self.progress.setStyleSheet("")
        # Hide the resume banner while a run is live; re-evaluate when it ends (a stopped/failed run
        # leaves the project resumable, a completed run does not).
        self._refresh_resume_banner()

    def _tick_elapsed(self) -> None:
        import time

        secs = int(time.monotonic() - self._run_start)
        self.elapsed_label.setText(f"Elapsed: {secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}")

    # Map a Snakemake rule name to a plain-language phase, longest/most specific
    # substrings first so e.g. "fastqc_trim" wins over "fastqc".
    _PHASE_BY_RULE = [
        ("download", "Downloading sequencing data"),
        ("fasterq", "Downloading sequencing data"),
        ("prefetch", "Downloading sequencing data"),
        ("fastqc_raw", "Quality control (raw reads)"),
        ("fastqc_trim", "Quality control (trimmed reads)"),
        ("fastqc", "Quality control"),
        ("fastp", "Trimming reads"),
        ("sortmerna", "Filtering rRNA"),
        ("rrna", "Filtering rRNA"),
        ("star_index", "Building genome index"),
        ("hisat2_index", "Building genome index"),
        ("salmon_index", "Building transcriptome index"),
        ("reference", "Preparing the reference genome"),
        ("star_align", "Aligning reads to the genome"),
        ("hisat2_align", "Aligning reads to the genome"),
        ("align", "Aligning reads to the genome"),
        ("salmon_quant", "Quantifying transcripts"),
        ("ingest_counts", "Reading the count matrix"),
        ("ingest_deseq2_results", "Reading the DESeq2 results table"),
        ("featurecounts", "Counting reads per gene"),
        ("htseq", "Counting reads per gene"),
        ("genes_of_interest", "Genes-of-interest figures"),
        ("deseq2", "Differential expression (DESeq2)"),
        ("enrichment", "Functional enrichment (GO / GSEA)"),
        ("figures", "Generating figures"),
        ("multiqc", "Aggregating the QC report"),
        ("validate", "Running sanity checks"),
        ("input_check", "Running sanity checks"),
        ("sanity", "Running sanity checks"),
        ("_check", "Running sanity checks"),
        ("reports", "Writing run reports"),
        ("summary", "Writing run reports"),
    ]

    def _friendly_phase(self, rule_name: str) -> str | None:
        name = rule_name.lower()
        for key, label in self._PHASE_BY_RULE:
            if key in name:
                return label
        return None

    def _on_run_line(self, line: str) -> None:
        self.log_text.append(line)
        # Snakemake prints these on any rule/workflow failure. We watch for them because
        # the WSL launcher runs through `micromamba run`, which returns exit 0 even when
        # snakemake failed — so the process exit code alone would report a failed run as
        # "Completed". A definitive error line marks the run failed regardless of the code.
        if re.search(r"Error in rule\s|WorkflowError|Exiting because a job execution failed"
                     r"|MissingOutputException", line):
            self._run_error_detected = True
        # An R environment that cannot load its Bioconductor stack (a dropped GO.db or an
        # r-base drift) fails with one of these signatures: our validate_project load-test
        # ("will not load in the bulkseq env"), or a raw R load error inside enrichment/ingest.
        # This class is repairable by rebuilding the env from the lock, so flag it separately
        # from a bad-contrast / missing-input setup error (which is NOT an env problem) to offer
        # a one-click rebuild at the end.
        if not getattr(self, "_env_broken_detected", False) and re.search(
                r"will not load in the bulkseq env|there is no package called"
                r"|unable to load shared object", line):
            self._env_broken_detected = True
        match = re.search(r"(\d+)\s+of\s+(\d+)\s+steps", line)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            if total:
                self.progress.setValue(int(done / total * 100))
        # Surface a plain-language phase when Snakemake announces a job's rule.
        rule_match = re.search(r"(?:^|\s)(?:local|check)?rule\s+([A-Za-z0-9_]+)\s*:", line)
        if rule_match:
            if rule_match.group(1) == "star_align":
                self._saw_star_align = True
            phase = self._friendly_phase(rule_match.group(1))
            if phase:
                self.phase_label.setText(f"Current step: {phase}")
        # Detect a stale lock / incomplete-output state and offer auto-recovery
        # once per run so a killed-WSL orphan does not wedge every later start.
        if not self._recovery_offered and re.search(
            r"LockException|IncompleteFilesException|Directory cannot be locked|incomplete", line
        ):
            self._recovery_offered = True
            QTimer.singleShot(0, self._offer_auto_recovery)
        # Early low-mapping guardrail: as each STAR alignment finishes it writes a
        # *_Log.final.out with the uniquely-mapped %. If a sample maps poorly
        # (usually a wrong reference or contamination) warn and offer to stop
        # before more hours are wasted.
        # Snakemake 9 prints the rule header and "Finished job N." on separate lines,
        # so the old single-line regex never matched. Trigger the mapping check on
        # any job completion once a star_align rule has been announced; the check
        # itself is idempotent and scans the STAR Log.final.out files on disk.
        if (not self._mapping_halt_decided and getattr(self, "_saw_star_align", False)
                and "Finished job" in line):
            QTimer.singleShot(0, self._check_alignment_mapping)

    def _offer_auto_recovery(self) -> None:
        reply = QMessageBox.question(
            self,
            APP_NAME,
            "The working directory is locked or has incomplete outputs (usually a "
            "previous run that was stopped). Unlock it and resume with "
            "--rerun-incomplete now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Ensure the wedged run is fully gone before unlocking/resuming. Flag the
        # pending resume so _on_run_finished launches it once the killed process is
        # actually reaped — a fixed timer raced the "run already active" guard.
        self._pending_recover = True
        self.log_text.append("Auto-recovery: stopping the wedged run, then unlocking to resume…")
        self._stop_run(announce=False)
        if self.runner is not None and self.config is not None:
            self.runner.unlock(self.config)

    def _refresh_resume_banner(self) -> None:
        # Show the resume banner iff the current project has an interrupted, resumable run (a lock or
        # incomplete outputs left by a stop / crash / app-close) and no run is currently active. Called
        # on project load, when a run finishes/stops, and when a run starts.
        if getattr(self, "resume_banner", None) is None:
            return
        active = self._run_active or (self.runner is not None and self.runner.is_running())
        state = (snakemake_run_state(self.project_root)
                 if (self.project_root is not None and not active) else {"resumable": False})
        show = bool(state.get("resumable"))
        self.resume_banner.setVisible(show)
        self.resume_button.setVisible(show)
        if show:
            self.resume_banner.setText(
                "This project has an unfinished run — it was stopped, interrupted, or the app was closed "
                "while it was running. Click Resume to continue from where it left off; completed steps "
                "are reused (the project folder is the saved state).")

    def _resume_interrupted(self) -> None:
        # One-click resume from the reopen banner. A hard-killed / app-closed run leaves a lock, so
        # unlock first, then resume: reuse the auto-recovery chain (_pending_recover -> _on_run_finished
        # launches --rerun-incomplete once the unlock finishes). If only incomplete outputs exist (no
        # lock), resume directly.
        # Do NOT hide the banner here: if the run does not actually start (a pre-run gate blocks it, e.g.
        # a persisted REVIEW_REQUIRED check needs approval), hiding it now would strand the banner hidden
        # with nothing running. _set_running_ui(True) -> _refresh_resume_banner hides it once the run
        # really starts; a gate-abort re-shows it (see _start_snakemake_impl), so the banner stays honest.
        if self.project_root is None or self.config is None:
            return
        state = snakemake_run_state(self.project_root)
        if state.get("locked"):
            self._pending_recover = True
            self.log_text.append("Resuming: releasing the previous run's lock, then continuing…")
            self._start_snakemake("unlock")
        else:
            self._start_snakemake("resume")

    def _check_alignment_mapping(self) -> None:
        # Inspect any STAR Log.final.out files written so far; if a sample's
        # uniquely-mapped % is below the threshold, warn once and offer to stop.
        if self._mapping_halt_decided or self.project_root is None:
            return
        aligned = self.project_root / "results" / "aligned"
        if not aligned.exists():
            return
        for log in sorted(aligned.glob("*_Log.final.out")):
            sample = log.name[: -len("_Log.final.out")]
            if sample in self._mapping_checked:
                continue
            pct = self._parse_unique_mapped_pct(log)
            if pct is None:
                continue  # STAR has not finished writing this report yet
            self._mapping_checked.add(sample)
            if pct < MIN_UNIQUE_MAPPED_WARN_PCT:
                self._mapping_halt_decided = True
                self._warn_low_mapping(sample, pct)
                return

    @staticmethod
    def _parse_unique_mapped_pct(path: Path) -> float | None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        m = re.search(r"Uniquely mapped reads %\s*\|\s*([0-9.]+)%", text)
        return float(m.group(1)) if m else None

    def _warn_low_mapping(self, sample: str, pct: float) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(
            f"Low alignment rate\n\nSample {sample} uniquely mapped only {pct:.1f}% of reads "
            f"(warning threshold {MIN_UNIQUE_MAPPED_WARN_PCT:.0f}%)."
        )
        box.setInformativeText(
            "This usually means the reference does not match the reads (wrong organism), or "
            "heavy rRNA/adapter contamination. Continuing will likely waste hours and produce "
            "an unusable result.\n\nStop the run, or continue anyway?"
        )
        stop_btn = box.addButton("Stop run", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Continue anyway", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stop_btn)
        box.exec()
        if box.clickedButton() is stop_btn:
            self.log_text.append(f"Low mapping ({pct:.1f}%) on {sample}: stopping run at user request.")
            self._stop_run(announce=True)
        else:
            self.log_text.append(f"Low mapping ({pct:.1f}%) on {sample}: continuing at user request.")

    def _on_run_finished(self, code: int) -> None:
        self.elapsed_timer.stop()
        # Record the wall-clock finish of an actual pipeline run for the timing report.
        if getattr(self, "_run_start_wall", None) and not getattr(self, "_run_finish_wall", None):
            self._run_finish_wall = datetime.now().isoformat(timespec="seconds")
        was_stop = self._stop_in_progress
        was_mode = self._run_mode
        self._set_running_ui(False)
        self._stop_in_progress = False
        self._run_mode = None
        # Auto-recovery: the wedged run has now fully exited, so it is safe to
        # unlock+resume without racing the "run already active" guard. This runs
        # before the was_stop branch because the recovery deliberately stopped it.
        if getattr(self, "_pending_recover", False):
            self._pending_recover = False
            self.log_text.append("Auto-recovery: previous run exited; resuming with --rerun-incomplete.")
            QTimer.singleShot(0, lambda: self._start_snakemake("recover"))
            return
        if was_stop:
            self.progress.setStyleSheet("")
            self._set_run_status("Stopped", "#8B5200")
            self.phase_label.setText("")
            self.log_text.append("Run stopped.")
            return
        # Treat a snakemake-reported failure as failure even when the exit code is 0
        # (the WSL `micromamba run` launcher masks non-zero codes to 0).
        failed_in_output = getattr(self, "_run_error_detected", False)
        if code == 0 and not failed_in_output:
            self.progress.setValue(100)
            self.progress.setStyleSheet("")
            self._set_run_status("Completed", "#2E7D32")
            self.phase_label.setText("Finished")
            # An enrichment-term heatmap writes the fixed term_heatmap.*; copy it to a
            # per-term name (before the gallery re-scan) so each extracted term persists.
            if was_mode == "term" and self.project_root is not None:
                self._copy_term_heatmap()
            # A completed run / "Regenerate figures" writes new PNGs into
            # results/figures; re-scan so the Outputs figure picker shows them
            # without the user having to click "Refresh figures" first. A PPI-only
            # rebuild or a dry-run/unlock writes no such figures, so skip those.
            if (self.project_root is not None and hasattr(self, "figure_pick")
                    and was_mode in ("run", "resume", "recover", "figures", "goi", "term")):
                self._refresh_gallery()
            # After a full run, new enrichment terms exist — refresh the term picker.
            if was_mode in ("run", "resume", "recover") and hasattr(self, "term_pick"):
                self._populate_term_picker()
            if was_mode in ("run", "resume", "recover"):
                self.statusBar().showMessage(
                    "Run complete. View figures and tables on the Outputs tab, and the "
                    "interactive network on the PPI Network tab.", 20000)
            # A "Rebuild from STRING" produces a new network; reload it into the
            # interactive viewer so it reflects the rebuild instead of the old graph.
            if was_mode == "ppi" and self.project_root is not None:
                self._load_ppi_network()
            # A completed run writes the provenance files; enable their export buttons.
            self._refresh_export_buttons()
            # Hook 2 (runtime calibration): a fresh full run just finished — record predicted
            # vs actual wall time so future estimates converge to this machine. Local runs only
            # (the stash marks SRA), so network jitter is never learned as hardware speed.
            ae = getattr(self, "_active_estimate", None)
            if (was_mode == "run" and ae and ae.get("calibratable")
                    and self._run_start_wall and self._run_finish_wall):
                try:
                    wall_min = (datetime.fromisoformat(self._run_finish_wall)
                                - datetime.fromisoformat(self._run_start_wall)).total_seconds() / 60.0
                    record_run(ae["cores"], ae["predicted_raw"], wall_min,
                               ae["gbase"], ae["aligner"])
                except Exception:
                    pass
            self._active_estimate = None
        else:
            # Failure: do not imply success. Red bar, red status, keep partial %.
            self.progress.setStyleSheet("QProgressBar::chunk { background-color: #C0392B; }")
            status = "Failed — a rule reported an error (see the log)" if failed_in_output and code == 0 \
                else f"Failed (exit code {code})"
            self._set_run_status(status, "#C0392B")
            self.phase_label.setText("")
            if failed_in_output:
                self.log_text.append(
                    "A step failed. Scroll up for the 'Error in rule' line and its reason; the "
                    "full detail is in the rule's log under logs/ in the project folder.")
            # If the failure was the R environment failing to load its packages (not a data or
            # design problem), offer a one-click path to rebuild it from the pinned lock.
            if getattr(self, "_env_broken_detected", False):
                QTimer.singleShot(0, self._offer_env_rebuild)
        self.log_text.append(f"Process finished with exit code {code}")

    def _offer_env_rebuild(self) -> None:
        # The run failed because the R/Bioconductor stack in the bulkseq env would not load (a
        # dropped GO.db or an r-base drift). An in-place install cannot repair an ABI-inconsistent
        # stack, so send the user to the environment check, where Rebuild recreates it cleanly
        # from the pinned lock. The readiness R card now load-tests the stack, so it shows red.
        reply = QMessageBox.question(
            self, APP_NAME,
            "This run stopped because the R/Bioconductor environment could not load its packages "
            "(usually a dropped GO.db, or an R update that left the packages incompatible). This "
            "is not a problem with your data or settings — the environment needs a clean rebuild "
            "from the pinned lockfile.\n\nOpen the environment check to rebuild it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.show_readiness_dialog()

    def _stop_run(self, _checked: bool = False, announce: bool = True) -> None:
        if self.runner is None or self._stop_in_progress:
            return
        self._stop_in_progress = True
        if self.stop_button is not None:
            self.stop_button.setEnabled(False)
        if announce:
            self.log_text.append("Stopping run and releasing WSL processes...")
            self._set_run_status("Stopping...", "#8B5200")
        # Kills the whole WSL process tree (not just the wsl.exe relay) and reaps
        # the local handle; _on_run_finished then resets state for the next run.
        self.runner.stop()

    def _open_folder(self) -> None:
        if self.project_root is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project_root)))

    def _open_report(self) -> None:
        if self.project_root is None:
            return
        report = self.project_root / "results" / "qc" / "multiqc" / "multiqc_report.html"
        if report.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))
        else:
            self.log_text.append(f"MultiQC report not found yet: {report}")

    def _open_results_report(self) -> None:
        if self.project_root is None:
            return
        report = self.project_root / "results" / "reports" / "results_report.html"
        if report.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))
        else:
            self.log_text.append(f"Results report not found yet: {report}")

    def _refresh_export_buttons(self) -> None:
        # Enable the Run-Monitor provenance exports once a run has produced the files.
        root = getattr(self, "project_root", None)
        reports = (root / "results" / "reports") if root else None
        for attr, fname in (("export_toolsref_button", "tools_references.txt"),
                            ("export_design_button", "study_design.txt")):
            button = getattr(self, attr, None)
            if button is not None:
                button.setEnabled(bool(reports and (reports / fname).exists()))

    def _export_report_file(self, name: str, label: str) -> None:
        if self.project_root is None:
            return
        src = self.project_root / "results" / "reports" / name
        if not src.exists():
            QMessageBox.information(self, f"Export {label}",
                f"The {label} file is written when a run completes. Run the pipeline first, then export.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, f"Export {label}", name, "Text (*.txt)")
        if not dest:
            return
        try:
            shutil.copyfile(src, dest)
            self.log_text.append(f"Exported {label} to {dest}")
        except OSError as exc:
            QMessageBox.warning(self, f"Export {label}", f"Could not write {dest}:\n{exc}")

    def _export_tools_references(self) -> None:
        self._export_report_file("tools_references.txt", "tools & references")

    def _export_study_design(self) -> None:
        self._export_report_file("study_design.txt", "study design")

    def _build_reports_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        generate = QPushButton("Generate Reports")
        generate.setProperty("primary", True)
        generate.clicked.connect(self._generate_reports)
        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setPlaceholderText(
            "Run the pipeline, then click Generate Reports for the run summary, timing and sanity checks.")
        generate_row = QHBoxLayout()
        generate_row.addWidget(generate)
        generate_row.addStretch(1)
        layout.addLayout(generate_row)
        layout.addWidget(self.report_text)
        self.tabs.addTab(self._scrollable(page), "Reports")

    def _build_outputs_tab(self) -> None:
        # Resizable workspace: a vertical splitter separates the table (top) from
        # the figure area (bottom); inside the figure area a horizontal splitter
        # separates the figure viewer (left) from the tabbed controls (right).
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Table picker row.
        controls = QHBoxLayout()
        self.output_table_pick = QComboBox()
        self.output_table_pick.addItems(
            ["results/counts/counts.txt", "results/deseq2/deseq2_results.csv",
             "results/deseq2/upregulated_genes.csv", "results/deseq2/downregulated_genes.csv",
             "results/deseq2/normalized_counts.csv", "results/deseq2/unchanged_genes.csv",
             "results/enrichment/kegg_ora.csv", "results/enrichment/kegg_gsea.csv",
             "results/stats/wilcoxon_results.csv", "results/stats/set_overlap.csv",
             "results/networks/enrichment_emap_nodes.csv",
             "results/networks/enrichment_genemap_nodes.csv",
             "results/networks/string_ppi_nodes.csv", "results/networks/ppi_hub_genes.csv"]
        )
        load = QPushButton("Load table preview")
        load.clicked.connect(self._load_output_table)
        open_results = QPushButton("Open results folder")
        open_results.clicked.connect(lambda: self._open_subpath("results"))
        controls.addWidget(QLabel("Table:"))
        controls.addWidget(self.output_table_pick, 1)
        controls.addWidget(load)
        controls.addWidget(open_results)
        layout.addLayout(controls)

        # --- Table panel (top of the vertical splitter) ---
        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(QLabel("Table preview (first 200 rows)"))
        self.output_table = QTableWidget()
        self.output_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Click a header to sort the loaded preview (numeric columns sort numerically
        # via _SortableItem). Toggled off during (re)population in _load_output_table.
        self.output_table.setSortingEnabled(True)
        self.output_table.horizontalHeader().setSortIndicatorShown(True)
        self.output_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table_layout.addWidget(self.output_table)

        # --- Figure panel (left of the horizontal splitter) ---
        figure_panel = QWidget()
        figure_layout = QVBoxLayout(figure_panel)
        figure_layout.setContentsMargins(0, 0, 0, 0)
        figure_layout.addWidget(QLabel("Figures — scroll to zoom, drag to pan"))
        fig_controls = QHBoxLayout()
        self.figure_pick = QComboBox()
        self.figure_pick.currentTextChanged.connect(self._show_selected_figure)
        regen_figs = QPushButton("Regenerate figures")
        regen_figs.setToolTip("Re-render figures with the current style. Does not re-run alignment or DESeq2. Progress shows on the Run Monitor tab.")
        regen_figs.clicked.connect(self._regenerate_figures)
        refresh_figs = QPushButton("Refresh figures")
        refresh_figs.setToolTip("Reload the figure list and image from disk.")
        refresh_figs.clicked.connect(self._refresh_gallery)
        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(lambda: self.figure_viewer.fit())
        actual_btn = QPushButton("100%")
        actual_btn.clicked.connect(lambda: self.figure_viewer.actual_size())
        self.svg_toggle = QCheckBox("Vector (SVG)")
        self.svg_toggle.setToolTip("Show the vector SVG of the selected figure — crisp at any zoom. "
                                   "PNG is faster for very complex figures.")
        self.svg_toggle.setEnabled(SVG_AVAILABLE)
        # Reserve enough width for the indicator + label so it is never clipped at
        # the right edge of the controls row.
        self.svg_toggle.setMinimumWidth(self.svg_toggle.sizeHint().width() + 12)
        self.svg_toggle.toggled.connect(lambda _=False: self._show_selected_figure(self.figure_pick.currentText()))
        fig_controls.addWidget(QLabel("Figure:"))
        fig_controls.addWidget(self.figure_pick, 1)
        fig_controls.addWidget(regen_figs)
        fig_controls.addWidget(refresh_figs)
        fig_controls.addWidget(fit_btn)
        fig_controls.addWidget(actual_btn)
        fig_controls.addWidget(self.svg_toggle)
        figure_layout.addLayout(fig_controls)
        self.figure_viewer = ImageViewer()
        self.figure_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.figure_viewer.setMinimumSize(360, 320)
        self.figure_viewer.update_theme(IMAGEVIEWER_BG.get(self._current_theme_mode(), IMAGEVIEWER_BG["light"]))
        figure_layout.addWidget(self.figure_viewer, 1)

        # --- Controls panel (right of the horizontal splitter): tabbed ---
        control_panel = QTabWidget()
        control_panel.setMinimumWidth(280)
        control_panel.setMaximumWidth(460)
        control_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        control_panel.addTab(self._scrollable(self._build_figure_style_group()), "Figure Style")
        control_panel.addTab(self._scrollable(self._build_goi_group()), "Genes of Interest")
        control_panel.addTab(self._scrollable(self._build_enrichment_terms_group()), "Enrichment Terms")

        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        results_splitter.setChildrenCollapsible(False)
        results_splitter.setHandleWidth(6)
        results_splitter.addWidget(figure_panel)
        results_splitter.addWidget(control_panel)
        results_splitter.setStretchFactor(0, 3)
        results_splitter.setStretchFactor(1, 1)
        results_splitter.setSizes([560, 420])  # wider control panel so the figure-override columns fit
        self._outputs_results_splitter = results_splitter

        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.setChildrenCollapsible(True)
        main_splitter.setHandleWidth(8)
        main_splitter.addWidget(table_panel)
        main_splitter.addWidget(results_splitter)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setSizes([220, 560])
        self._outputs_main_splitter = main_splitter

        layout.addWidget(main_splitter, 1)

        # Restore saved splitter positions, if any.
        s = QSettings()
        for key, sp in (("_outputs_main_splitter", main_splitter), ("_outputs_results_splitter", results_splitter)):
            st = s.value(f"outputs/{key}", QByteArray())
            if isinstance(st, QByteArray) and not st.isEmpty():
                sp.restoreState(st)

        self.tabs.addTab(page, "Outputs")

    def _build_ppi_tab(self) -> None:
        # A dedicated, interactive STRING PPI network (cytoscape.js in a web view),
        # separate from the static figure: hover for per-protein detail, drag/zoom,
        # and customise layout / colour / size / confidence before exporting.
        from app.ui.ppi_viewer import PpiViewer

        page = QWidget()
        layout = QVBoxLayout(page)
        help_label = QLabel(
            "Interactive protein-protein interaction network (STRING) from the differential-"
            "expression / genes-of-interest set. Hover a protein for its symbol, expression, "
            "log2 fold-change, adjusted p-value and topology; drag nodes to rearrange and scroll "
            "to zoom. Customise the layout, colour, size and confidence, then export PNG or SVG.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        row1 = QHBoxLayout()
        load_btn = QPushButton("Load / refresh network")
        load_btn.setProperty("primary", True)
        load_btn.setToolTip("Assemble the network from this project's results and display it.")
        load_btn.clicked.connect(self._load_ppi_network)
        # Friendly display labels mapped to the internal cytoscape.js values (held
        # as userData) so a biologist sees "Force-directed", not "fcose".
        self.ppi_layout_pick = QComboBox()
        for label, val in [("Force-directed (fCoSE)", "fcose"), ("Compact (CoSE)", "cose"),
                           ("Circle", "circle"), ("Concentric", "concentric"), ("Grid", "grid")]:
            self.ppi_layout_pick.addItem(label, val)
        self.ppi_layout_pick.currentIndexChanged.connect(
            lambda _i: self.ppi_viewer.set_layout(self.ppi_layout_pick.currentData()))
        self.ppi_color_pick = QComboBox()
        for label, val in [("log₂ fold change", "log2FoldChange"), ("Module / cluster", "module")]:
            self.ppi_color_pick.addItem(label, val)
        self.ppi_color_pick.currentIndexChanged.connect(
            lambda _i: self.ppi_viewer.set_color_by(self.ppi_color_pick.currentData()))
        self.ppi_view_pick = QComboBox()
        for label, val in [("All", "all"), ("Up-regulated", "up"), ("Down-regulated", "down")]:
            self.ppi_view_pick.addItem(label, val)
        self.ppi_view_pick.setToolTip("Show all proteins, or only those up- or down-regulated "
                                      "(by log2 fold-change sign).")
        self.ppi_view_pick.currentIndexChanged.connect(
            lambda _i: self.ppi_viewer.set_direction_filter(self.ppi_view_pick.currentData()))
        self.ppi_size_pick = QComboBox()
        for label, val in [("Node degree", "degree"), ("Mean expression", "meanExpr"),
                           ("−log₁₀ adj. p", "neglog10padj")]:
            self.ppi_size_pick.addItem(label, val)
        self.ppi_size_pick.currentIndexChanged.connect(
            lambda _i: self.ppi_viewer.set_size_by(self.ppi_size_pick.currentData()))
        self.ppi_labels_cb = QCheckBox("Labels")
        self.ppi_labels_cb.setChecked(True)
        self.ppi_labels_cb.toggled.connect(lambda on: self.ppi_viewer.set_labels(on))
        self.ppi_italic_cb = QCheckBox("Italic")
        self.ppi_italic_cb.setChecked(True)
        self.ppi_italic_cb.setToolTip("Show gene symbols in italic (HGNC convention).")
        self.ppi_italic_cb.toggled.connect(lambda on: self.ppi_viewer.set_gene_italic(on))
        self.ppi_focus_cb = QCheckBox("Focus labels on click")
        self.ppi_focus_cb.setChecked(True)
        self.ppi_focus_cb.setToolTip("When you click a protein, show only its own and its "
                                     "interactors' labels; hide the rest of the network's names.")
        self.ppi_focus_cb.toggled.connect(lambda on: self.ppi_viewer.set_focus_labels(on))
        row1.addWidget(load_btn)
        row1.addWidget(QLabel("Layout:"))
        row1.addWidget(self.ppi_layout_pick)
        row1.addWidget(QLabel("Colour:"))
        row1.addWidget(self.ppi_color_pick)
        row1.addWidget(QLabel("Show:"))
        row1.addWidget(self.ppi_view_pick)
        row1.addWidget(QLabel("Size:"))
        row1.addWidget(self.ppi_size_pick)
        row1.addWidget(self.ppi_labels_cb)
        row1.addWidget(self.ppi_italic_cb)
        row1.addWidget(self.ppi_focus_cb)
        row1.addStretch(1)
        layout.addLayout(row1)

        # Row 2 — VIEW filter: a client-side slider that only hides edges in the
        # already-loaded graph (it cannot show edges below the build threshold).
        row2 = QHBoxLayout()
        view_lbl = QLabel("View filter — hide edges below:")
        row2.addWidget(view_lbl)
        self.ppi_conf = QSlider(Qt.Orientation.Horizontal)
        self.ppi_conf.setRange(0, 100)
        self.ppi_conf.setValue(0)
        self.ppi_conf.setMaximumWidth(180)
        self.ppi_conf.setToolTip("View-only filter: hides interactions below this confidence in the "
                                 "network shown right now. It does NOT re-contact STRING and cannot go "
                                 "below the build threshold — to show weaker edges, lower the rebuild "
                                 "score on the right and click Rebuild.")
        self.ppi_conf.valueChanged.connect(self._ppi_confidence_changed)
        self.ppi_conf_lbl = QLabel("0.00")
        row2.addWidget(self.ppi_conf)
        row2.addWidget(self.ppi_conf_lbl)
        row2.addStretch(1)
        # REBUILD: an on-panel score spinbox drives the rebuild, so changing it here and
        # clicking Rebuild actually re-contacts STRING at that confidence (the old button
        # silently used the far-away Figure-Style spinbox, so it looked like a no-op).
        row2.addWidget(QLabel("Rebuild at score ≥"))
        self.ppi_rebuild_score = QSpinBox()
        self.ppi_rebuild_score.setRange(0, 1000)
        self.ppi_rebuild_score.setSingleStep(50)
        self.ppi_rebuild_score.setValue(400)
        self.ppi_rebuild_score.setToolTip("STRING combined-score cutoff to rebuild at (0-1000; 400 = "
                                          "medium, 700 = high confidence). Lower it to pull in weaker "
                                          "interactions, then click Rebuild.")
        self.ppi_rebuild_score.valueChanged.connect(self._sync_score_to_figstyle)
        row2.addWidget(self.ppi_rebuild_score)
        rebuild_btn = QPushButton("Rebuild from STRING…")
        rebuild_btn.setToolTip("Re-contact string-db.org and rebuild the network at the 'Rebuild at "
                               "score' shown to the left. This replaces the current network.")
        rebuild_btn.clicked.connect(self._regenerate_ppi)
        row2.addWidget(rebuild_btn)
        layout.addLayout(row2)

        # Row 3 — EXPORT: save the current network as an image or Cytoscape files.
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Export:"))
        self.ppi_export_bg = QComboBox()
        self.ppi_export_bg.addItems(["White", "Transparent"])
        self.ppi_export_bg.setToolTip("Background of the exported PNG/SVG (labels stay dark either way).")
        export_png = QPushButton("Export PNG")
        export_png.setEnabled(False)
        export_png.setToolTip("Load a network first.")
        export_png.clicked.connect(lambda: self._ppi_export("png"))
        export_svg = QPushButton("Export SVG")
        export_svg.setEnabled(False)
        export_svg.setToolTip("Load a network first.")
        export_svg.clicked.connect(lambda: self._ppi_export("svg"))
        save_cyto = QPushButton("Save Cytoscape files…")
        save_cyto.setEnabled(False)
        save_cyto.setToolTip("Load a network first.")
        save_cyto.clicked.connect(self._save_ppi_cytoscape)
        self.ppi_export_png = export_png
        self.ppi_export_svg = export_svg
        self.ppi_save_cyto = save_cyto
        row3.addWidget(QLabel("background"))
        row3.addWidget(self.ppi_export_bg)
        row3.addWidget(export_png)
        row3.addWidget(export_svg)
        row3.addWidget(save_cyto)
        row3.addStretch(1)
        layout.addLayout(row3)

        self.ppi_status = QLabel("No network loaded — click “Load / refresh network”.")
        self.ppi_status.setWordWrap(True)
        layout.addWidget(self.ppi_status)

        self.ppi_viewer = PpiViewer()
        self.ppi_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ppi_viewer.setMinimumHeight(360)
        self.ppi_viewer.update_theme(self._ppi_theme_palette())
        layout.addWidget(self.ppi_viewer, 1)

        self.tabs.addTab(page, "PPI Network")

    def _ppi_theme_palette(self) -> dict:
        mode = self._current_theme_mode()
        pal = PALETTES.get(mode, PALETTES["light"])
        return {
            "bg": IMAGEVIEWER_BG.get(mode, IMAGEVIEWER_BG["light"]),
            "text": pal.get("TEXT", "#1a1a1a"),
            "edge": pal.get("BORDER", "#c7c7c7"),
            "muted": pal.get("MUTED_TEXT", "#8a8a8a"),
        }

    def _load_ppi_network(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        # Fallback: no web engine -> show the static PPI figure.
        if not self.ppi_viewer.available:
            png = self.project_root / "results" / "figures" / "ppi_network.png"
            if png.exists():
                self.ppi_viewer.load_static(png)
                self.ppi_status.setText("Interactive view unavailable — showing the static PPI figure.")
            else:
                self.ppi_status.setText("No PPI network found for this project yet.")
            return
        from app.core.ppi_graph import build_ppi_cytoscape_json

        try:
            graph = build_ppi_cytoscape_json(self.project_root)
        except Exception as exc:  # never crash the tab
            self.ppi_status.setText(f"Could not assemble the network: {exc}")
            return
        meta = graph.get("meta", {})
        n_nodes = int(meta.get("node_count", 0))
        n_edges = int(meta.get("edge_count", 0))
        self.ppi_viewer.load_graph(graph["elements"])
        # The graph is pre-filtered at build time; the slider can only tighten.
        floor = int(round(float(meta.get("score_floor", 0.0)) * 100))
        self.ppi_conf.blockSignals(True)
        self.ppi_conf.setMinimum(floor)
        self.ppi_conf.setValue(floor)
        self.ppi_conf.blockSignals(False)
        self.ppi_conf_lbl.setText(f"{floor / 100:.2f}")
        has_network = n_nodes > 0
        if hasattr(self, "ppi_export_png"):
            self.ppi_export_png.setEnabled(has_network)
            self.ppi_export_svg.setEnabled(has_network)
            self.ppi_export_png.setToolTip("" if has_network else "Load a network first.")
            self.ppi_export_svg.setToolTip("" if has_network else "Load a network first.")
            if hasattr(self, "ppi_save_cyto"):
                self.ppi_save_cyto.setEnabled(has_network)
                self.ppi_save_cyto.setToolTip("" if has_network else "Load a network first.")
        if not has_network:
            self.ppi_status.setText(
                "No PPI network for this run — STRING returned no interactions (the organism may "
                "lack STRING coverage, or its genes have no mapped symbols). The static figure, if any, "
                "is on the Outputs tab.")
        else:
            self.ppi_status.setText(
                f"{n_nodes} proteins, {n_edges} interactions. Hover a protein for details; "
                "click to highlight its neighbours; drag and scroll to explore.")

    def _ppi_confidence_changed(self, value: int) -> None:
        floor = value / 100.0
        self.ppi_conf_lbl.setText(f"{floor:.2f}")
        if hasattr(self, "ppi_viewer"):
            self.ppi_viewer.set_confidence(floor)

    def _save_ppi_cytoscape(self) -> None:
        # Copy the Cytoscape interchange files (GraphML / SIF / cytoscape.js JSON +
        # node/edge/hub tables for the STRING PPI and the enrichment networks) to a
        # folder the user picks. GraphML keeps node attributes on import.
        if not self._require_project() or self.project_root is None:
            return
        net_dir = self.project_root / "results" / "networks"
        if not (net_dir / "string_ppi.graphml").exists():
            QMessageBox.information(
                self, APP_NAME,
                "No STRING PPI network files yet. Run the pipeline (or 'Rebuild from STRING…') first.")
            return
        dest = QFileDialog.getExistingDirectory(self, "Choose a folder for the Cytoscape network files")
        if not dest:
            return
        names = [
            "string_ppi.graphml", "string_ppi.sif", "string_ppi.cyjs",
            "string_ppi_nodes.csv", "string_ppi_edges.csv", "ppi_hub_genes.csv",
            "enrichment_emap.graphml", "enrichment_emap.sif", "enrichment_emap.cyjs",
            "enrichment_genemap.graphml", "enrichment_genemap.sif", "enrichment_genemap.cyjs",
        ]
        copied = 0
        for n in names:
            src = net_dir / n
            if src.exists():
                try:
                    shutil.copyfile(src, Path(dest) / n)
                    copied += 1
                except Exception:
                    pass
        QMessageBox.information(
            self, APP_NAME,
            f"Saved {copied} Cytoscape network file(s) to:\n{dest}\n\n"
            "Open string_ppi.graphml in Cytoscape (File → Import → Network from File) to keep node "
            "attributes (module, degree, betweenness, log2FC). .sif is bare topology; .cyjs is for "
            "cytoscape.js / web.")

    def _ppi_export(self, fmt: str) -> None:
        if not hasattr(self, "ppi_viewer") or not self.ppi_viewer.available:
            QMessageBox.information(self, APP_NAME, "Interactive export needs the web view; "
                                   "use the static figure on the Outputs tab instead.")
            return
        default = f"ppi_network.{fmt}"
        path, _ = QFileDialog.getSaveFileName(self, "Export PPI network", default,
                                              f"{fmt.upper()} (*.{fmt})")
        if not path:
            return
        bg = "transparent" if self.ppi_export_bg.currentText() == "Transparent" else "white"
        self.ppi_viewer.export_image(fmt, bg, lambda data: self._save_ppi_export(path, fmt, data))

    def _save_ppi_export(self, path: str, fmt: str, data) -> None:
        if not data:
            QMessageBox.warning(self, APP_NAME, "Nothing to export — load a network first.")
            return
        try:
            if fmt == "png":
                import base64

                b64 = data.split(",", 1)[1] if "," in data else data
                Path(path).write_bytes(base64.b64decode(b64))
            else:
                Path(path).write_text(data, encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Export failed: {exc}")
            return
        self.ppi_status.setText(f"Exported {fmt.upper()} to {path}")

    def _build_goi_group(self) -> QWidget:
        # No group title — the enclosing "Genes of Interest" tab already names it.
        group = QWidget()
        v = QVBoxLayout(group)
        help_label = QLabel("Paste gene IDs (one per line) in the same identifier format as your reference — locus tags (e.g. FBgn..., FGSG_...), Ensembl/RefSeq IDs, or gene symbols present in the GTF. They are matched to this run's genes; any that do not match are flagged in the genes-of-interest report (with examples of the run's ID format). On the next run you get a focused z-scored heatmap, per-condition expression plots, a counts table, and — when PPI seeding is set to the gene list — a STRING network for these genes.")
        help_label.setWordWrap(True)  # without this the long label forces a huge min width
        v.addWidget(help_label)
        self.goi_box = QTextEdit()
        self.goi_box.setAcceptRichText(False)  # paste gene IDs as plain text, no source formatting
        self.goi_box.setPlaceholderText("One gene ID per line")
        self.goi_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        save = QPushButton("Save genes of interest")
        save.clicked.connect(self._save_goi)
        generate = QPushButton("Generate from existing results")
        generate.setToolTip("Build the genes-of-interest heatmap, expression plots, and table "
                            "from the already-computed DESeq2 results — no re-alignment or "
                            "re-analysis. Requires a completed run.")
        generate.clicked.connect(self._generate_goi)
        v.addWidget(self.goi_box)
        goi_buttons = QHBoxLayout()
        goi_buttons.addWidget(save)
        goi_buttons.addWidget(generate)
        v.addLayout(goi_buttons)
        return group

    def _generate_goi(self) -> None:
        # Extract the genes-of-interest figures/tables from the existing DESeq2
        # object (no full re-run). Requires a completed DESeq2 run.
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        self._apply_figure_style()  # GOI figures honor the current style, like Regenerate figures
        n = self._persist_goi()
        if n == 0:
            QMessageBox.information(
                self, APP_NAME,
                "Add at least one gene ID before generating the genes-of-interest outputs.")
            return
        rds = self.project_root / "results" / "deseq2" / "deseq2_objects.rds"
        if not rds.exists():
            QMessageBox.warning(
                self, APP_NAME,
                "No DESeq2 results were found for this project yet. Run the pipeline once "
                "(Run Monitor) to produce them; afterwards this button regenerates the "
                "genes-of-interest figures from those results without re-analyzing.")
            return
        # A DESeq2-results upload has no per-sample counts (the synthetic RDS carries dds=vsd=NULL),
        # so the focused GOI heatmap / per-gene panels cannot be built — gate it here like the
        # Enrichment-Terms heatmap, rather than letting make_goi.R crash on colData(NULL) mid-run.
        if self.config.input.type == "deseq2_results":
            QMessageBox.information(
                self, APP_NAME,
                "Genes-of-interest figures need per-sample counts, which a DESeq2-results upload "
                "does not include. Use a FASTQ/SRA, count-matrix, or microarray run for these.")
            return
        self._start_snakemake("goi")

    def _persist_goi(self) -> int:
        # Write the genes-of-interest box to config/genes_of_interest.txt and wire
        # it into config (or clear it when empty). Returns the gene count. No dialog.
        assert self.project_root is not None and self.config is not None
        genes = [g.strip() for g in self.goi_box.toPlainText().splitlines() if g.strip()]
        if not genes:
            self.config.gene_sets.custom_gene_list = None
        else:
            path = self.project_root / "config" / "genes_of_interest.txt"
            path.write_text("\n".join(genes) + "\n", encoding="utf-8")
            self.config.gene_sets.custom_gene_list = "config/genes_of_interest.txt"
        self.manager.save_config(self.project_root, self.config)
        return len(genes)

    def _save_goi(self) -> None:
        if not self._require_project() or self.config is None:
            return
        n = self._persist_goi()
        if n == 0:
            QMessageBox.information(self, APP_NAME, "Genes of interest cleared.")
        else:
            QMessageBox.information(self, APP_NAME, f"Saved {n} gene(s). Re-run, or click 'Regenerate figures', to produce the genes-of-interest heatmap and expression plots.")

    # ---- Enrichment-term gene extraction --------------------------------------
    # The enrichment CSVs each carry a per-term gene list ("/"-separated) in a geneID or
    # core_enrichment column. These methods let the user pick a term, pull its genes' DESeq2
    # stats into a table (instant, pandas-only), and build a focused heatmap by reusing the
    # genes-of-interest R script via the "term" run mode — all from the finished run.
    _TERM_SOURCES = [
        ("results/enrichment/go_ora_up.csv", "GO up-regulated"),
        ("results/enrichment/go_ora_down.csv", "GO down-regulated"),
        ("results/enrichment/go_ora_all.csv", "GO combined"),
        ("results/enrichment/gsea.csv", "GO GSEA"),
        ("results/enrichment/kegg_ora.csv", "KEGG ORA"),
        ("results/enrichment/kegg_gsea.csv", "KEGG GSEA"),
    ]

    def _build_enrichment_terms_group(self) -> QWidget:
        group = QWidget()
        v = QVBoxLayout(group)
        help_label = QLabel(
            "Pick an enrichment term to pull its member genes into a DESeq2 table and a focused "
            "heatmap — from the finished run, no re-analysis. Requires a completed run whose "
            "enrichment used the clusterProfiler backend (g:Profiler runs record no gene lists).")
        help_label.setWordWrap(True)
        v.addWidget(help_label)
        self.term_pick = QComboBox()
        self.term_pick.currentIndexChanged.connect(self._on_term_selected)
        v.addWidget(self.term_pick)
        row = QHBoxLayout()
        refresh = QPushButton("Refresh terms")
        refresh.clicked.connect(self._populate_term_picker)
        self.term_table_btn = QPushButton("Extract genes → table")
        self.term_table_btn.setToolTip("Write this term's genes with their DESeq2 stats to a CSV "
                                       "and show it in the table — instant, from existing results.")
        self.term_table_btn.clicked.connect(lambda: self._extract_term_genes(heatmap=False))
        self.term_heatmap_btn = QPushButton("Build heatmap + expression")
        self.term_heatmap_btn.setToolTip("Reuses the finished DESeq2 results — no re-alignment "
                                         "or re-analysis. Adds a focused heatmap for the term's genes.")
        self.term_heatmap_btn.clicked.connect(lambda: self._extract_term_genes(heatmap=True))
        row.addWidget(refresh)
        row.addWidget(self.term_table_btn)
        row.addWidget(self.term_heatmap_btn)
        v.addLayout(row)
        self.term_status = QLabel("")
        self.term_status.setWordWrap(True)
        self.term_status.setStyleSheet("color:#6B7785;font-size:9pt;")
        v.addWidget(self.term_status)
        v.addStretch(1)
        self._populate_term_picker()
        return group

    @staticmethod
    def _term_gene_column(df) -> str | None:
        # The gene-list column is geneID (ORA) or core_enrichment (GSEA); gProfiler CSVs have neither.
        for col in ("geneID", "core_enrichment"):
            if col in df.columns:
                return col
        return None

    def _populate_term_picker(self) -> None:
        if not hasattr(self, "term_pick"):
            return
        self.term_pick.blockSignals(True)
        self.term_pick.clear()
        added = 0
        if self.project_root is not None:
            for rel, label in self._TERM_SOURCES:
                path = self.project_root / rel
                if not path.exists() or path.stat().st_size == 0:
                    continue
                try:
                    df = pd.read_csv(path, dtype=str).fillna("")
                except Exception:
                    continue
                gene_col = self._term_gene_column(df)
                if gene_col is None or df.empty:
                    continue
                # Header row (disabled) then each term.
                self.term_pick.addItem(f"──  {label}  ──")
                self.term_pick.model().item(self.term_pick.count() - 1).setEnabled(False)
                count_col = "Count" if "Count" in df.columns else ("setSize" if "setSize" in df.columns else None)
                for i in range(len(df)):
                    desc = df.iloc[i].get("Description", "") or df.iloc[i].get("ID", f"term {i}")
                    padj = df.iloc[i].get("p.adjust", "")
                    cnt = df.iloc[i].get(count_col, "") if count_col else ""
                    bits = []
                    if cnt:
                        bits.append(f"n={cnt}")
                    if padj:
                        try:
                            bits.append(f"padj={float(padj):.1e}")
                        except (ValueError, TypeError):
                            pass
                    disp = f"{desc}" + (f"  ({', '.join(bits)})" if bits else "")
                    self.term_pick.addItem(disp, {"csv": rel, "row": i, "gene_col": gene_col, "desc": str(desc)})
                    added += 1
        # Land on the first real term, not the disabled group header at index 0, so the action
        # buttons are enabled immediately.
        for i in range(self.term_pick.count()):
            if isinstance(self.term_pick.itemData(i), dict):
                self.term_pick.setCurrentIndex(i)
                break
        self.term_pick.blockSignals(False)
        if added == 0:
            self.term_status.setText(
                "No extractable enrichment terms found. Run the pipeline (with the clusterProfiler "
                "enrichment backend) first; g:Profiler runs do not record per-term gene lists.")
        else:
            self.term_status.setText(f"{added} term(s) available.")
        self._on_term_selected()

    def _on_term_selected(self, _idx: int = 0) -> None:
        if not hasattr(self, "term_table_btn"):
            return
        data = self.term_pick.currentData()
        has_term = isinstance(data, dict)
        self.term_table_btn.setEnabled(has_term)
        # Heatmap needs an expression matrix; a DESeq2-results upload has none.
        has_counts = (self.project_root is not None
                      and (self.project_root / "results" / "deseq2" / "normalized_counts.csv").exists())
        self.term_heatmap_btn.setEnabled(has_term and has_counts)
        self.term_heatmap_btn.setToolTip(
            "Reuses the finished DESeq2 results — no re-analysis."
            if has_counts else
            "No expression matrix in a DESeq2-results upload; the gene table is still available.")

    def _resolve_term_genes(self, tokens: list[str]):
        # Match a term's raw tokens (symbols on GO routes, entrez on KEGG-OrgDb/GSEA, locus tags on
        # KEGG-only) to rows of deseq2_results.csv. Route-agnostic: try symbol, then gene_id/base_id,
        # then entrez via id_map. Returns (subset_df, n_unmatched).
        assert self.project_root is not None
        res = pd.read_csv(self.project_root / "results" / "deseq2" / "deseq2_results.csv", dtype=str).fillna("")
        res["_base"] = res["gene_id"].str.replace(r"\.\d+$", "", regex=True)
        by_symbol = {s: i for i, s in enumerate(res.get("symbol", pd.Series([], dtype=str))) if s}
        by_id = {v: i for i, v in enumerate(res["gene_id"])}
        by_base = {v: i for i, v in enumerate(res["_base"])}
        entrez_to_row: dict[str, int] = {}
        id_map_path = self.project_root / "results" / "enrichment" / "id_map.csv"
        if id_map_path.exists() and id_map_path.stat().st_size > 0:
            try:
                idm = pd.read_csv(id_map_path, dtype=str).fillna("")
                sym_or_id = {}
                for _, r in idm.iterrows():
                    key = (r.get("symbol") or "").strip() or (r.get("gene_id") or "").strip()
                    ent = (r.get("entrez") or "").strip()
                    if ent and key:
                        sym_or_id[ent] = key
                for ent, key in sym_or_id.items():
                    ri = by_symbol.get(key, by_id.get(key, by_base.get(key)))
                    if ri is not None:
                        entrez_to_row[ent] = ri
            except Exception:
                pass
        rows: list[int] = []
        unmatched = 0
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            ri = by_symbol.get(tok)
            if ri is None:
                ri = by_id.get(tok, by_base.get(tok.split(".")[0]))
            if ri is None:
                ri = entrez_to_row.get(tok)
            if ri is None:
                unmatched += 1
            else:
                rows.append(ri)
        sub = res.iloc[sorted(set(rows))].drop(columns=["_base"], errors="ignore")
        return sub, unmatched

    @staticmethod
    def _term_slug(desc: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", desc).strip("_").lower()
        return (slug or "term")[:60]

    def _extract_term_genes(self, heatmap: bool) -> None:
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        data = self.term_pick.currentData()
        if not isinstance(data, dict):
            return
        res_csv = self.project_root / "results" / "deseq2" / "deseq2_results.csv"
        if not res_csv.exists():
            QMessageBox.warning(self, APP_NAME, "No DESeq2 results found yet. Run the pipeline first.")
            return
        try:
            df = pd.read_csv(self.project_root / data["csv"], dtype=str).fillna("")
            tokens = str(df.iloc[data["row"]][data["gene_col"]]).split("/")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not read the term's genes: {exc}")
            return
        sub, unmatched = self._resolve_term_genes(tokens)
        if sub.empty:
            QMessageBox.information(
                self, APP_NAME,
                "None of this term's genes matched the DESeq2 results table. "
                "(This can happen if the enrichment and DESeq2 identifier spaces differ.)")
            return
        if "padj" in sub.columns:
            sub = sub.assign(_p=pd.to_numeric(sub["padj"], errors="coerce")).sort_values("_p").drop(columns="_p")
        slug = self._term_slug(data["desc"])
        terms_dir = self.project_root / "results" / "enrichment" / "terms"
        terms_dir.mkdir(parents=True, exist_ok=True)
        rel = f"results/enrichment/terms/{slug}_genes.csv"
        sub.to_csv(self.project_root / rel, index=False)
        note = f" ({unmatched} of the term's genes were not in the results table.)" if unmatched else ""
        self.term_status.setText(f"Wrote {len(sub)} genes for '{data['desc']}' → {rel}.{note}")
        self._register_output_table(rel)
        if not heatmap:
            return
        # Heatmap: write the matched genes (symbols preferred, else gene_id) and reuse make_goi.R.
        genes = [(r.get("symbol") or "").strip() or (r.get("gene_id") or "").strip()
                 for _, r in sub.iterrows()]
        genes = [g for g in genes if g]
        (self.project_root / "config").mkdir(parents=True, exist_ok=True)
        (self.project_root / "config" / "enrichment_term.txt").write_text("\n".join(genes) + "\n", encoding="utf-8")
        if not (self.project_root / "results" / "deseq2" / "deseq2_objects.rds").exists():
            QMessageBox.warning(self, APP_NAME, "The DESeq2 objects file is missing; re-run the pipeline first.")
            return
        self._apply_figure_style()      # term heatmap honors the current figure style
        self._term_slug_pending = slug  # for the per-term copy after the run completes
        self._start_snakemake("term")

    def _register_output_table(self, rel_path: str) -> None:
        idx = self.output_table_pick.findText(rel_path)
        if idx < 0:
            self.output_table_pick.addItem(rel_path)
            idx = self.output_table_pick.count() - 1
        self.output_table_pick.setCurrentIndex(idx)
        self._load_output_table()

    def _copy_term_heatmap(self) -> None:
        # The "term" rule always writes the fixed term_heatmap.*; copy to a per-term name so
        # every extracted term stays visible in the gallery (the fixed file is overwritten each time).
        slug = getattr(self, "_term_slug_pending", None)
        if not slug or self.project_root is None:
            return
        figs = self.project_root / "results" / "figures"
        for kind in ("heatmap", "expression"):
            for ext in ("png", "svg"):
                src = figs / f"term_{kind}.{ext}"
                if src.exists():
                    try:
                        shutil.copyfile(src, figs / f"term_{slug}_{kind}.{ext}")
                    except Exception:
                        pass
        self._term_slug_pending = None

    def _regenerate_ppi(self) -> None:
        # Rebuild the STRING PPI network from the existing DESeq2 results with the
        # current score threshold / hub-label count, without re-aligning or re-DESeq2.
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        rds = self.project_root / "results" / "deseq2" / "deseq2_objects.rds"
        if not rds.exists():
            QMessageBox.warning(
                self, APP_NAME,
                "No DESeq2 results were found for this project yet. Run the pipeline once "
                "(Run Monitor) to produce them; afterwards this rebuilds the STRING PPI "
                "network from those results without re-analyzing.")
            return
        # Both score spinboxes are kept in lockstep (see _sync_score_*), so either reads
        # the same value; use the on-panel one and rebuild at it.
        score = int(self.ppi_rebuild_score.value())
        self.config.ppi.score_threshold = score
        self.config.ppi.hub_label_count = int(self.ppi_hub_labels.value())
        self.manager.save_config(self.project_root, self.config)
        self._start_snakemake("ppi")

    def _sync_score_to_rebuild(self, value: int) -> None:
        if hasattr(self, "ppi_rebuild_score") and self.ppi_rebuild_score.value() != value:
            self.ppi_rebuild_score.blockSignals(True)
            self.ppi_rebuild_score.setValue(value)
            self.ppi_rebuild_score.blockSignals(False)

    def _sync_score_to_figstyle(self, value: int) -> None:
        if hasattr(self, "ppi_score") and self.ppi_score.value() != value:
            self.ppi_score.blockSignals(True)
            self.ppi_score.setValue(value)
            self.ppi_score.blockSignals(False)

    def _info_label(self, text: str, help_text: str) -> QWidget:
        # A form-row label with a small info button that explains a complex
        # parameter (tooltip on hover, full text on click).
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        label = QLabel(text)
        label.setWordWrap(True)  # lets narrow form columns wrap instead of forcing width
        row.addWidget(label)
        info = QToolButton()
        info.setText("ⓘ")  # circled small i
        info.setAutoRaise(True)
        info.setCursor(Qt.CursorShape.PointingHandCursor)
        info.setToolTip(help_text)
        info.clicked.connect(lambda: QMessageBox.information(self, text, help_text))
        row.addWidget(info)
        row.addStretch(1)
        return holder

    # Per-figure-group override columns (key -> header label).
    OVERRIDE_COLS = [
        ("palette", "Palette"), ("font_family", "Font"), ("point_size", "Point"),
        ("base_font_size", "Base font"), ("width_in", "Width"), ("height_in", "Height"),
    ]

    def _make_override_widget(self, key: str, families: list[str]):
        # Each widget has an explicit "inherit" state (blank data / special value 0) so an
        # untouched cell falls back to the global setting.
        if key == "palette":
            cb = QComboBox(); cb.addItem("(inherit)", "")
            for p in self.PALETTE_NAMES:
                cb.addItem(p, p)
            return cb
        if key == "font_family":
            cb = QComboBox(); cb.setEditable(True); cb.addItem("(inherit)")
            cb.addItems(families)
            return cb
        if key == "base_font_size":
            s = QSpinBox(); s.setRange(0, 48); s.setSpecialValueText("inherit"); s.setValue(0)
            return s
        # point_size / width_in / height_in — float, 0 = inherit
        s = QDoubleSpinBox(); s.setDecimals(1); s.setSingleStep(0.5); s.setSpecialValueText("inherit")
        s.setRange(0.0, 12.0 if key == "point_size" else 60.0); s.setValue(0.0)
        return s

    def _build_figure_override_table(self) -> QWidget:
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget
        families = QFontDatabase.families()
        t = QTableWidget(len(self.PALETTE_GROUPS), 1 + len(self.OVERRIDE_COLS))
        t.setHorizontalHeaderLabels(["Figure group"] + [lbl for _, lbl in self.OVERRIDE_COLS])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.fig_override_widgets = {}
        for r, (gkey, glabel) in enumerate(self.PALETTE_GROUPS):
            item = QTableWidgetItem(glabel.split(" (")[0])
            item.setToolTip(glabel)
            t.setItem(r, 0, item)
            self.fig_override_widgets[gkey] = {}
            for c, (okey, _lbl) in enumerate(self.OVERRIDE_COLS, start=1):
                w = self._make_override_widget(okey, families)
                t.setCellWidget(r, c, w)
                self.fig_override_widgets[gkey][okey] = w
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        t.setMinimumHeight(len(self.PALETTE_GROUPS) * 36 + 32)
        return t

    @staticmethod
    def _override_value(key: str, w) -> str:
        # Widget -> config string ("" means inherit).
        if key == "palette":
            return w.currentData() or ""
        if key == "font_family":
            txt = w.currentText().strip()
            return "" if txt in ("", "(inherit)") else txt
        v = w.value()
        if v == 0:
            return ""
        return str(int(v)) if key == "base_font_size" else str(v)

    @staticmethod
    def _set_override_widget(key: str, w, val: str) -> None:
        if key == "palette":
            idx = w.findData(val or ""); w.setCurrentIndex(idx if idx >= 0 else 0)
        elif key == "font_family":
            w.setCurrentText(val if val else "(inherit)")
        else:
            try:
                w.setValue(float(val) if val else 0)
            except (TypeError, ValueError):
                w.setValue(0)

    def _build_figure_style_group(self) -> QWidget:
        # Style controls for the DESeq2 figures; written to config.figures_style
        # and consumed by workflow/scripts/make_figures.R. No group title — the
        # enclosing "Figure Style" tab already names it.
        group = QWidget()
        form = QFormLayout(group)
        # Stack each field under its (wrapping) label so the form fits the narrow
        # control panel without a horizontal scrollbar.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.fig_palette = QComboBox()
        self.PALETTE_NAMES = ["Blue-Red", "Viridis", "Magma", "Plasma", "Cividis",
                              "Spectral", "Red-Yellow-Blue", "Greyscale"]
        self.fig_palette.addItems(self.PALETTE_NAMES)
        # Per-figure-group style override table. Each cell defaults to "inherit" (blank / 0),
        # so figures stay uniform with the global settings unless a group is deliberately
        # changed. Built by _build_figure_override_table(); stored in self.fig_override_widgets.
        self.PALETTE_GROUPS = [
            ("core", "Core figures (PCA, volcano, MA, heatmaps)"),
            ("correlation", "Sample-correlation heatmaps"),
            ("enrichment", "Enrichment plots"),
            ("network", "PPI network"),
            ("comparative_meta", "Multi-study meta-analysis figures"),
        ]
        self.fig_point_size = QDoubleSpinBox()
        self.fig_point_size.setRange(0.1, 12.0)
        self.fig_point_size.setSingleStep(0.1)
        self.fig_point_size.setDecimals(1)
        self.fig_point_size.setValue(2.5)
        self.fig_base_font = QSpinBox()
        self.fig_base_font.setRange(4, 48)
        self.fig_base_font.setValue(12)
        # Font family as a dropdown of installed fonts (editable so a font only
        # present in the WSL R environment can still be typed). The first entry
        # means "ggplot default" and maps to an empty value.
        self.fig_font_family = QComboBox()
        self.fig_font_family.setEditable(True)
        self.fig_font_family.addItem(self.FONT_DEFAULT_LABEL)
        self.fig_font_family.addItems(QFontDatabase.families())
        self.fig_label_bold = QCheckBox("Bold axis tick labels")
        self.fig_label_bold.setToolTip("Render the axis tick labels (the value text along each axis) in bold.")
        self.fig_title_bold = QCheckBox("Bold axis titles")
        self.fig_title_bold.setToolTip("Render the axis titles (the axis name/unit text) in bold.")
        self.fig_gene_italic = QCheckBox("Italicize gene symbols")
        self.fig_gene_italic.setChecked(True)
        self.fig_gene_italic.setToolTip(
            "Render gene symbols in italic (the HGNC convention) on the volcano labels, "
            "the DEG and genes-of-interest heatmap rows, and the report tables."
        )
        self.fig_volcano_top = QSpinBox()
        self.fig_volcano_top.setRange(0, 200)
        self.fig_volcano_top.setValue(15)
        self.fig_heatmap_top = QSpinBox()
        self.fig_heatmap_top.setRange(1, 500)
        self.fig_heatmap_top.setValue(30)
        self.fig_pca_ntop = QSpinBox()
        self.fig_pca_ntop.setRange(10, 50000)
        self.fig_pca_ntop.setValue(500)
        self.fig_width = QDoubleSpinBox()
        self.fig_width.setRange(1.0, 30.0)
        self.fig_width.setSingleStep(0.5)
        self.fig_width.setDecimals(1)
        self.fig_width.setValue(6.0)
        self.fig_height = QDoubleSpinBox()
        self.fig_height.setRange(1.0, 30.0)
        self.fig_height.setSingleStep(0.5)
        self.fig_height.setDecimals(1)
        self.fig_height.setValue(5.0)
        self.fig_dpi = QSpinBox()
        self.fig_dpi.setRange(72, 1200)
        self.fig_dpi.setValue(300)
        self._fig_dpi_prev = 300
        self.fig_dpi.valueChanged.connect(self._on_fig_dpi_changed)
        self.fig_dim_unit = QComboBox()
        self.fig_dim_unit.addItems(["in", "cm", "px"])
        self._fig_dim_unit_prev = "in"
        self.fig_dim_unit.currentTextChanged.connect(self._on_fig_unit_changed)
        # Curated subset of the W2 figure-tuning fields. The rest stay config-file
        # driven (defaults in default_config.yaml).
        # Volcano y-axis scaling: how the tall -log10(padj) tail (hyper-significant / extreme genes)
        # is shown. cap = squish to a cap line with off-scale triangles (default); full = true heights;
        # sqrt = compressed so extreme genes stay visible without squashing the bulk.
        self.fig_volcano_yscale = QComboBox()
        for label, data in (("Cap the tail (off-scale markers)", "cap"),
                            ("Show all at full height", "full"),
                            ("Compress (sqrt scale)", "sqrt")):
            self.fig_volcano_yscale.addItem(label, data)
        self.fig_volcano_ycap = QDoubleSpinBox()
        self.fig_volcano_ycap.setRange(0.0, 400.0)
        self.fig_volcano_ycap.setSingleStep(5.0)
        self.fig_volcano_ycap.setDecimals(1)
        self.fig_volcano_ycap.setValue(0.0)
        self.fig_volcano_ycap.setSpecialValueText("auto")  # 0 = auto (quantile)
        # The numeric cap only applies in 'cap' mode; grey it out otherwise.
        self.fig_volcano_yscale.currentIndexChanged.connect(
            lambda _i: self.fig_volcano_ycap.setEnabled(self.fig_volcano_yscale.currentData() == "cap"))
        self.fig_volcano_alpha = QDoubleSpinBox()
        self.fig_volcano_alpha.setRange(0.05, 1.0)
        self.fig_volcano_alpha.setSingleStep(0.05)
        self.fig_volcano_alpha.setDecimals(2)
        self.fig_volcano_alpha.setValue(0.55)
        self.fig_pca_fixed_aspect = QCheckBox("Fix PCA aspect ratio")
        self.fig_pca_fixed_aspect.setChecked(False)
        self.fig_sample_labels = QCheckBox("Show per-sample labels on PCA and heatmaps")
        self.fig_sample_labels.setChecked(True)
        self.fig_sample_labels.setToolTip(
            "Sample-id text on the PCA, the sample-distance and correlation heatmaps, and the sample "
            "columns of the top-DEG / up / down and genes-of-interest heatmaps. Turn off to declutter a "
            "run with many samples or replicates (common on microarray series and large studies); the "
            "condition colour bar above each heatmap still marks the groups."
        )
        self.fig_heatmap_zlim = QDoubleSpinBox()
        self.fig_heatmap_zlim.setRange(0.1, 10.0)
        self.fig_heatmap_zlim.setSingleStep(0.5)
        self.fig_heatmap_zlim.setDecimals(1)
        self.fig_heatmap_zlim.setValue(2.5)
        self.fig_enrich_show = QSpinBox()
        self.fig_enrich_show.setRange(1, 100)
        self.fig_enrich_show.setValue(15)
        self.fig_ppi_layout = QComboBox()
        self.fig_ppi_layout.setEditable(True)  # accept layouts the R side may add
        self.fig_ppi_layout.addItems(["fr", "stress", "kk", "drl", "circle", "grid"])
        self.fig_ppi_layout.setCurrentText("fr")
        save_style = QPushButton("Save figure style")
        save_style.clicked.connect(self._save_figure_style)
        form.addRow(self._info_label("Palette", "Colour scheme for all figures. Blue-Red is diverging; Viridis is colour-blind friendly; Greyscale prints well in mono."), self.fig_palette)
        _ov_hdr = QLabel("Per-figure-group overrides (optional)")
        _ov_hdr.setStyleSheet("font-weight: 600; margin-top: 4px;")
        form.addRow(_ov_hdr)
        _ov_note = QLabel("Each cell defaults to inherit (the global settings above). Set a cell "
                          "to give one figure group its own palette, font, point size, base font, "
                          "or size. Width/height 0 = inherit.")
        _ov_note.setWordWrap(True)
        _ov_note.setStyleSheet("color: #6B7785; font-size: 9pt;")
        form.addRow(_ov_note)
        form.addRow(self._build_figure_override_table())
        form.addRow(self._info_label("Point size", "Dot size in PCA/volcano scatter plots (ggplot2 size units)."), self.fig_point_size)
        form.addRow(self._info_label("Base font size", "Base text size for all figures (ggplot2 theme base_size, points)."), self.fig_base_font)
        form.addRow(self._info_label("Font family", "Font for figure text. Leave as default unless the font is also available in the WSL R environment."), self.fig_font_family)
        form.addRow(self.fig_label_bold)
        form.addRow(self.fig_title_bold)
        form.addRow(self.fig_gene_italic)
        form.addRow(self._info_label("Volcano top-N labels", "How many of the most significant genes to label on the volcano plot. 0 = none."), self.fig_volcano_top)
        form.addRow(self._info_label("Heatmap top-N genes", "Number of top genes (by adjusted p) shown in the top-DEG heatmap."), self.fig_heatmap_top)
        form.addRow(self._info_label("PCA n-top genes", "Number of most-variable genes used to compute the PCA. Protocol default 500."), self.fig_pca_ntop)
        form.addRow(self._info_label("Size units", "Units for the width/height below. Pixels (px) are converted using the DPI."), self.fig_dim_unit)
        form.addRow(self._info_label("Width", "Saved figure width (PNG and SVG), in the units selected above."), self.fig_width)
        form.addRow(self._info_label("Height", "Saved figure height (PNG and SVG), in the units selected above."), self.fig_height)
        form.addRow(self._info_label("DPI (PNG)", "Resolution for the raster PNG export. SVG is vector and unaffected. 300 is publication quality. Also converts px width/height to inches."), self.fig_dpi)
        form.addRow(self._info_label("Volcano y-axis", "How the tall -log10(adjusted p) tail is shown so a marginal gene with extreme significance is visible. 'Cap the tail' squishes hyper-significant genes to a cap line marked with hollow triangles (default); 'Show all at full height' plots every gene at its true height; 'Compress (sqrt)' shrinks the tall tail so extreme genes stay readable without squashing the rest."), self.fig_volcano_yscale)
        form.addRow(self._info_label("Volcano y cap", "Upper limit for the volcano -log10(adjusted p) axis when 'Cap the tail' is selected. 'auto' (0) caps at the 99.5th percentile so a few hyper-significant genes do not squash the rest."), self.fig_volcano_ycap)
        form.addRow(self._info_label("Volcano point alpha", "Opacity of the significant points in the volcano plot (0-1). Lower values reveal density in the dense core."), self.fig_volcano_alpha)
        form.addRow(self.fig_pca_fixed_aspect)
        form.addRow(self.fig_sample_labels)
        form.addRow(self._info_label("Heatmap z limit", "Symmetric cap on the row z-scores in the top-DEG heatmap; values beyond +/- this map to the extreme colours."), self.fig_heatmap_zlim)
        form.addRow(self._info_label("Enrichment categories shown", "Number of terms shown in the enrichment dot/ridge/KEGG plots."), self.fig_enrich_show)
        form.addRow(self._info_label("PPI layout", "Graph layout algorithm for the PPI network figure (graphlayouts). 'fr' (Fruchterman-Reingold) is force-directed and the default; 'stress' is a compact alternative."), self.fig_ppi_layout)
        form.addRow(save_style)
        # --- PPI network (STRING) controls: customise + regenerate in-app ---
        ppi_subheader = QLabel("PPI network (STRING) — also saved by 'Save figure style' above")
        ppi_subheader.setStyleSheet("font-weight: bold; margin-top: 6px;")
        form.addRow(ppi_subheader)
        self.ppi_score = QSpinBox()
        self.ppi_score.setRange(0, 1000)
        self.ppi_score.setValue(400)
        # Keep this Figure-Style threshold and the PPI-tab "Rebuild at score" spinbox in
        # lockstep, so either Regenerate button rebuilds at the value the user just set.
        self.ppi_score.valueChanged.connect(self._sync_score_to_rebuild)
        self.ppi_hub_labels = QSpinBox()
        self.ppi_hub_labels.setRange(0, 100)
        self.ppi_hub_labels.setValue(15)
        regen_ppi = QPushButton("Regenerate PPI network")
        regen_ppi.clicked.connect(self._regenerate_ppi)
        form.addRow(self._info_label("PPI score threshold", "STRING combined-score cutoff for the protein-protein network (0-1000; 400 = medium, 700 = high confidence). Higher gives a sparser, higher-confidence network."), self.ppi_score)
        form.addRow(self._info_label("PPI hub labels", "How many top hub proteins (by degree) to label on the PPI network figure."), self.ppi_hub_labels)
        form.addRow(regen_ppi)
        return group

    @staticmethod
    def _dim_to_inches(value: float, unit: str, dpi: int) -> float:
        if unit == "cm":
            return value / 2.54
        if unit == "px":
            return value / max(dpi, 1)
        return value

    @staticmethod
    def _dim_from_inches(inches: float, unit: str, dpi: int) -> float:
        if unit == "cm":
            return inches * 2.54
        if unit == "px":
            return inches * dpi
        return inches

    def _configure_dim_spins(self, unit: str) -> None:
        for spin in (self.fig_width, self.fig_height):
            if unit == "px":
                spin.setDecimals(0); spin.setRange(72.0, 9000.0); spin.setSingleStep(50.0)
            elif unit == "cm":
                spin.setDecimals(2); spin.setRange(2.5, 76.0); spin.setSingleStep(0.5)
            else:
                spin.setDecimals(1); spin.setRange(1.0, 30.0); spin.setSingleStep(0.5)

    def _on_fig_unit_changed(self, new_unit: str) -> None:
        # Convert the displayed width/height so the physical size is preserved
        # when the user switches units.
        old_unit = getattr(self, "_fig_dim_unit_prev", "in")
        if new_unit == old_unit:
            return
        dpi = self.fig_dpi.value()
        w_in = self._dim_to_inches(self.fig_width.value(), old_unit, dpi)
        h_in = self._dim_to_inches(self.fig_height.value(), old_unit, dpi)
        self._configure_dim_spins(new_unit)
        self.fig_width.setValue(self._dim_from_inches(w_in, new_unit, dpi))
        self.fig_height.setValue(self._dim_from_inches(h_in, new_unit, dpi))
        self._fig_dim_unit_prev = new_unit

    def _on_fig_dpi_changed(self, new_dpi: int) -> None:
        # In pixel mode the canonical size is inches = px / dpi, so changing DPI
        # without adjusting the px display would silently rescale the saved figure.
        # Recompute the px values to hold the physical size constant.
        old_dpi = getattr(self, "_fig_dpi_prev", new_dpi)
        self._fig_dpi_prev = new_dpi
        if self.fig_dim_unit.currentText() != "px" or old_dpi == new_dpi or new_dpi <= 0:
            return
        for spin in (self.fig_width, self.fig_height):
            inches = spin.value() / max(old_dpi, 1)
            spin.blockSignals(True)
            spin.setValue(inches * new_dpi)
            spin.blockSignals(False)

    def _apply_figure_style(self) -> bool:
        # Copy the style controls into config and persist (no dialog). Returns
        # False if there is no open project.
        if self.config is None or self.project_root is None:
            return False
        style = self.config.figures_style
        style.palette = self.fig_palette.currentText()  # type: ignore[assignment]
        # Store only the cells the user actually set (non-inherit), so the config stays
        # clean and every unset key inherits the global setting.
        overrides: dict[str, dict[str, str]] = {}
        for gkey, widgets in self.fig_override_widgets.items():
            g = {okey: v for okey, w in widgets.items()
                 if (v := self._override_value(okey, w))}
            if g:
                overrides[gkey] = g
        style.figure_overrides = overrides
        style.point_size = self.fig_point_size.value()
        style.base_font_size = self.fig_base_font.value()
        font = self.fig_font_family.currentText().strip()
        style.font_family = "" if font == self.FONT_DEFAULT_LABEL else font
        style.label_bold = self.fig_label_bold.isChecked()
        style.gene_symbol_italic = self.fig_gene_italic.isChecked()
        style.title_bold = self.fig_title_bold.isChecked()
        style.volcano_top_n = self.fig_volcano_top.value()
        style.heatmap_top_n = self.fig_heatmap_top.value()
        style.pca_ntop = self.fig_pca_ntop.value()
        unit = self.fig_dim_unit.currentText()
        dpi = self.fig_dpi.value()
        # width_in/height_in stay the canonical inches the R export uses.
        style.width_in = round(self._dim_to_inches(self.fig_width.value(), unit, dpi), 4)
        style.height_in = round(self._dim_to_inches(self.fig_height.value(), unit, dpi), 4)
        style.dpi = dpi
        style.dimension_unit = unit  # type: ignore[assignment]
        style.volcano_y_scale = self.fig_volcano_yscale.currentData()
        style.volcano_y_cap = self.fig_volcano_ycap.value()
        style.volcano_point_alpha = self.fig_volcano_alpha.value()
        style.pca_fixed_aspect = self.fig_pca_fixed_aspect.isChecked()
        style.sample_labels = self.fig_sample_labels.isChecked()
        style.heatmap_zlim = self.fig_heatmap_zlim.value()
        style.enrich_show_category = self.fig_enrich_show.value()
        style.ppi_layout = self.fig_ppi_layout.currentText().strip() or "fr"
        # PPI score / hub-label controls live on this tab but feed config.ppi (not figures_style);
        # persist them here so a normal Run honors them, not just Regenerate PPI.
        if hasattr(self, "ppi_score"):
            self.config.ppi.score_threshold = self.ppi_score.value()
        if hasattr(self, "ppi_hub_labels"):
            self.config.ppi.hub_label_count = self.ppi_hub_labels.value()
        self.manager.save_config(self.project_root, self.config)
        return True

    def _save_figure_style(self) -> None:
        if not self._apply_figure_style():
            QMessageBox.warning(self, APP_NAME, "Create or open a project first.")
            return
        QMessageBox.information(self, APP_NAME, "Figure style saved. Click 'Regenerate figures' to apply it now.")

    def _regenerate_figures(self) -> None:
        # Persist the current style (no dialog), then re-render only the figure
        # rules (no re-alignment / re-DESeq2) via the runner's "figures" mode.
        # Progress and status appear on the Run Monitor tab.
        if not self._require_project() or self.config is None:
            return
        self._apply_figure_style()
        self._persist_goi()  # include unsaved genes-of-interest edits in the re-render
        self._start_snakemake("figures")

    def _open_subpath(self, relative: str) -> None:
        if self.project_root is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project_root / relative)))

    def _load_output_table(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        # Namespaced entries (per-study) carry their relative path as userData;
        # plain entries fall back to the display text, which is the relative path.
        rel = self.output_table_pick.currentData() or self.output_table_pick.currentText()
        path = self.project_root / rel
        if not path.exists():
            self.output_table.setRowCount(0)
            self.output_table.setColumnCount(1)
            self.output_table.setHorizontalHeaderLabels(["info"])
            self.output_table.setRowCount(1)
            self.output_table.setItem(0, 0, QTableWidgetItem(f"Not found yet: {path.name} (run the pipeline first)"))
            return
        sep = "," if path.suffix == ".csv" else "\t"
        try:
            df = pd.read_csv(path, sep=sep, comment="#", dtype=str, nrows=200).fillna("")
        except Exception as exc:  # truncated / locked / malformed file
            self.output_table.setRowCount(0)
            self.output_table.setColumnCount(1)
            self.output_table.setHorizontalHeaderLabels(["info"])
            self.output_table.setRowCount(1)
            self.output_table.setItem(0, 0, QTableWidgetItem(f"Could not read {path.name}: {exc}"))
            return
        # Disable sorting while filling, or Qt re-sorts on every insert and scrambles
        # cell placement; re-enable afterwards so header clicks sort the loaded rows.
        self.output_table.setSortingEnabled(False)
        self.output_table.setColumnCount(len(df.columns))
        self.output_table.setHorizontalHeaderLabels([str(c) for c in df.columns])
        self.output_table.setRowCount(len(df))
        for r in range(len(df)):
            for c in range(len(df.columns)):
                self.output_table.setItem(r, c, _SortableItem(str(df.iat[r, c])))
        self.output_table.setSortingEnabled(True)

    def _refresh_gallery(self) -> None:
        prev = self.figure_pick.currentText()
        self.figure_pick.blockSignals(True)
        self.figure_pick.clear()
        # (display name, absolute path) pairs. Regular figures carry no userData
        # (path is reconstructed from results/figures); per-study figures carry the
        # full path so they resolve outside that directory.
        entries: list[tuple[str, object]] = []
        if self.project_root is not None:
            for f in sorted((self.project_root / "results" / "figures").glob("*.png")):
                entries.append((f.name, None))
            # Multi-study meta-analysis: surface per-study figures, namespaced by study
            # id (e.g. "PRJNA123 / volcano"). Gated on the manifest so single-study runs
            # are unaffected. PNGs only — the Vector toggle swaps to the matching .svg.
            manifest = self.project_root / "results" / "meta" / "per_study" / "manifest.json"
            if manifest.exists():
                per_study = self.project_root / "results" / "meta" / "per_study"
                # figures/ plus the opt-in enrichment/ dotplot (same <study>/<sub>/<file> layout).
                for sub in ("figures", "enrichment"):
                    for f in sorted(per_study.glob(f"*/{sub}/*.png")):
                        study = f.parent.parent.name
                        entries.append((f"{study} / {f.stem}", str(f)))
        if entries:
            self.figure_pick.setEnabled(True)
            for display, data in entries:
                self.figure_pick.addItem(display, data)
            # Keep the user on the figure they were viewing across a refresh /
            # post-run rescan; fall back to the first only if it's gone.
            idx = self.figure_pick.findText(prev)
            self.figure_pick.setCurrentIndex(idx if idx >= 0 else 0)
            self.figure_pick.blockSignals(False)
            self._show_selected_figure(self.figure_pick.currentText())
        else:
            self.figure_pick.addItem("(no figures yet — run the pipeline first)")
            self.figure_pick.setEnabled(False)
            self.figure_pick.blockSignals(False)
            self.figure_viewer.clear()

    def _show_selected_figure(self, name: str) -> None:
        if not name or name.startswith("(no figures") or self.project_root is None:
            return
        # Per-study figures carry their full path as userData; regular figures have
        # None and are reconstructed under results/figures from the bare filename.
        data = self.figure_pick.currentData()
        path = Path(data) if data else self.project_root / "results" / "figures" / name
        # When the vector toggle is on, prefer the matching .svg (crisp at any zoom).
        if getattr(self, "svg_toggle", None) is not None and self.svg_toggle.isChecked():
            svg = path.with_suffix(".svg")
            if svg.exists():
                path = svg
            else:
                self.statusBar().showMessage(f"No SVG for {name}; showing the PNG.", 3000)
        if path.exists():
            self.figure_viewer.set_image(path)

    def _browse_workdir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Working directory", self.workdir.text())
        if directory:
            self.workdir.setText(directory)

    def _use_wsl_workdir(self) -> None:
        # Resolve the WSL-native projects folder off the UI thread (a cold WSL can
        # take a moment to answer) and fill the field when it returns. Guard against
        # double-clicks so only one probe runs at a time.
        existing = getattr(self, "_wsl_workdir_worker", None)
        if existing is not None and existing.isRunning():
            return
        self.statusBar().showMessage("Locating the WSL filesystem...", 4000)
        worker = BackgroundWorker(wsl_recommended_workdir)
        worker.done.connect(self._on_wsl_workdir_resolved)
        worker.failed.connect(self._on_wsl_workdir_failed)
        self._wsl_workdir_worker = worker  # hold a reference so the thread isn't GC'd
        worker.start()

    def _on_wsl_workdir_resolved(self, path: object) -> None:
        if getattr(self, "_closing", False):
            return
        if not path:
            QMessageBox.information(
                self, APP_NAME,
                "Could not determine the WSL filesystem location. Is WSL2 installed with a "
                "distribution running? You can still pick a Windows folder, which works but is "
                "slower for large genomics files.",
            )
            return
        self.workdir.setText(str(path))
        self.statusBar().showMessage(f"Working directory set to the WSL filesystem: {path}", 8000)

    def _on_wsl_workdir_failed(self, exc: object) -> None:
        if getattr(self, "_closing", False):
            return
        QMessageBox.warning(
            self, APP_NAME,
            f"Could not reach WSL to locate its filesystem:\n{exc}\n\n"
            "Make sure WSL2 is installed and a distribution is running, or pick a Windows "
            "folder instead.",
        )

    def _autodetect_wsl_workdir(self) -> None:
        # On startup, prefer the WSL-native filesystem for WSL users without
        # blocking the instant startup: resolve it in the background and adopt it
        # only if the user has not changed the default Windows path yet.
        if shutil.which("wsl") is None:
            return
        worker = BackgroundWorker(wsl_recommended_workdir)
        worker.done.connect(self._on_autodetect_wsl_workdir)
        self._wsl_autodetect_worker = worker
        worker.start()

    def _on_autodetect_wsl_workdir(self, path: object) -> None:
        if getattr(self, "_closing", False):
            return
        if path and self.workdir.text() == self._default_workdir:
            self.workdir.setText(str(path))

    def show_readiness_dialog(self) -> None:
        # Reuse an open dialog rather than spawning a second one. Two dialogs each
        # carry their own install guard, so a first-run auto-open plus a manual
        # "Check Environment" click could start two concurrent setups.
        try:
            existing = self.readiness_dialog
            if existing is not None and existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
        except RuntimeError:
            pass  # prior dialog's C++ object was already deleted
        self.readiness_dialog = ReadinessDialog(self)
        self.readiness_dialog.show()

    def _create_project(self) -> None:
        name = self.project_name.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, "Enter a project name before creating a project.")
            return
        workdir = Path(self.workdir.text().strip() or str(Path.home() / "BulkSeqProjects"))
        messages = validate_working_directory(workdir, use_wsl=self.use_wsl.isChecked())
        if any(m.get("status") == "FAIL" for m in messages):
            self.project_status.setPlainText(
                "Cannot create project here:\n" + self._format_workdir_messages(messages)
            )
            QMessageBox.warning(self, APP_NAME, self._format_workdir_messages(messages))
            return
        try:
            root = self.manager.create_project(name, workdir)
        except (OSError, ValueError) as exc:
            self.project_status.setPlainText(f"Project creation failed: {exc}")
            QMessageBox.critical(self, APP_NAME, f"Project creation failed:\n{exc}")
            return
        self._load_project(root)
        self.project_status.setPlainText(
            f"Created {root}\n" + self._format_workdir_messages(messages)
        )

    def _create_benchmark_project(self, benchmark_id: str | None = None) -> None:
        workdir = Path(self.workdir.text().strip() or str(Path.home() / "BulkSeqProjects"))
        messages = validate_working_directory(workdir, use_wsl=self.use_wsl.isChecked())
        if any(m.get("status") == "FAIL" for m in messages):
            self.project_status.setPlainText(
                "Cannot create benchmark project here:\n" + self._format_workdir_messages(messages)
            )
            QMessageBox.warning(self, APP_NAME, self._format_workdir_messages(messages))
            return
        catalog = load_benchmark_catalog()
        if not catalog:
            QMessageBox.warning(self, APP_NAME, "No benchmark datasets are bundled.")
            return
        if benchmark_id is not None:
            benchmark = next((b for b in catalog if b["id"] == benchmark_id), catalog[0])
        elif len(catalog) > 1:
            # Let the user choose which bundled dataset to scaffold.
            labels = [f"{b['name']} — {b['organism_name']}" for b in catalog]
            choice, ok = QInputDialog.getItem(
                self, APP_NAME, "Choose a benchmark dataset:", labels, 0, False)
            if not ok:
                return
            benchmark = catalog[labels.index(choice)]
        else:
            benchmark = catalog[0]
        benchmark_id = str(benchmark["id"])
        try:
            root = create_benchmark_project(benchmark_id, workdir, self.project_name.text() or benchmark_id)
        except (OSError, ValueError) as exc:
            self.project_status.setPlainText(f"Benchmark project creation failed: {exc}")
            QMessageBox.critical(self, APP_NAME, f"Benchmark project creation failed:\n{exc}")
            return
        self._load_project(root)
        self.project_status.setPlainText(
            f"Created benchmark project: {root}\n"
            f"Dataset: {benchmark['name']} ({benchmark['organism_name']})\n"
            f"Accessions: {', '.join(str(sample.get('original_accession') or sample.get('sample_id', '')) for sample in benchmark['samples'])}\n"
            + self._format_workdir_messages(messages)
        )

    def _open_project(self) -> None:
        # Start the picker where the user's projects actually live, not the process CWD (which
        # is the app's install/AppData folder). Prefer the folder the last project was opened
        # from, else the current working directory — which on WSL is the WSL-native project
        # location the app auto-detects, so a project generated in WSL is found immediately.
        settings = QSettings()
        start = str(settings.value("last_project_dir", "") or "").strip()
        if not start or not Path(start).exists():
            start = self.workdir.text().strip()
        directory = QFileDialog.getExistingDirectory(self, "Open project", start)
        if directory:
            self._load_project(Path(directory))

    def _load_project(self, root: Path) -> None:
        # Opening (or creating) another project while a run is live would repoint
        # project_root under the running thread, whose log/finished signals would
        # then write into the new project. Block until the run is stopped.
        if self._run_active or (self.runner is not None and self.runner.is_running()):
            QMessageBox.warning(
                self, APP_NAME,
                "A run is currently active. Stop it on the Run Monitor tab before "
                "opening or creating another project.")
            return
        # Validate before mutating state so opening a non-project folder cannot
        # leave self.project_root pointing at an invalid directory.
        config_path = root / "config" / "config.yaml"
        if not config_path.exists():
            QMessageBox.warning(
                self, APP_NAME,
                f"Not a BulkSeq Studio project (missing config/config.yaml):\n{root}",
            )
            self.project_status.setPlainText(f"Not a project folder: {root}")
            return
        try:
            config = self.manager.load_config(root)
        except Exception as exc:  # malformed or unreadable config.yaml
            QMessageBox.critical(self, APP_NAME, f"Could not read project config:\n{exc}")
            self.project_status.setPlainText(f"Failed to open project: {exc}")
            return
        self.project_root = root
        self.config = config
        # Smoothly flag a comma decimal separator (from a comma-locale hand-edit): the values
        # were read as dots, but tell the user so they can re-save to normalize the file.
        _dec_warnings = decimal_comma_warnings(root)
        if _dec_warnings:
            QMessageBox.information(self, APP_NAME, "\n\n".join(_dec_warnings))
        # Drop the previous project's transient state (log, status, figures,
        # network) before showing the new one.
        self._clear_transient_ui()
        self._populate_widgets_from_config()
        samples = root / "config" / "samples.tsv"
        if samples.exists():
            self.metadata_table.load_tsv(samples)
            # _populate_widgets_from_config seeded the contrast dropdowns from the PREVIOUS
            # table; re-seed now that the new project's samples are loaded (valid selected
            # values are preserved by _refresh_conditions).
            self._refresh_conditions()
        self._refresh_gallery()
        self._refresh_export_buttons()
        if hasattr(self, "term_pick"):
            self._populate_term_picker()
        self._remember_recent_project(root)
        # Remember the folder this project lives in so the "Open project" picker starts there
        # next time (instead of the app's install/AppData folder).
        QSettings().setValue("last_project_dir", str(root.parent))
        self.project_status.setPlainText(f"Open project: {root}")
        # If this project was left with an unfinished run, surface the one-click Resume banner.
        self._refresh_resume_banner()

    def _clear_transient_ui(self) -> None:
        # Reset run/output widgets so a previously opened project's log, status,
        # figures and network do not linger after switching projects.
        self.log_text.clear()
        self.command_text.clear()
        self._set_run_status("Idle")
        self.phase_label.setText("Ready — configure your project, then click Start Run.")
        self.progress.setValue(0)
        self.progress.setStyleSheet("")
        self.elapsed_label.setText("Elapsed: 00:00:00")
        self.input_preview.clear()
        self.output_table.setRowCount(0)
        self.report_text.clear()
        self.runtime_text.clear()
        # The run-approval gate and the previous project's sanity output must not
        # carry over: approval is per project (a stale tick could let an unreviewed
        # run start).
        self.approve_review.setChecked(False)
        self.sanity_text.clear()
        self.ppi_status.setText("No network loaded — click “Load / refresh network”.")
        if hasattr(self, "ppi_export_png"):
            self.ppi_export_png.setEnabled(False)
            self.ppi_export_svg.setEnabled(False)
            if hasattr(self, "ppi_save_cyto"):
                self.ppi_save_cyto.setEnabled(False)

    def _remember_recent_project(self, root: Path) -> None:
        # Keep up to 8 most-recently-opened project paths in QSettings for the
        # Project tab's recent-projects picker.
        s = QSettings()
        recent = s.value("recent_projects", []) or []
        if isinstance(recent, str):
            recent = [recent]
        rp = str(root)
        recent = [p for p in recent if p != rp]
        recent.insert(0, rp)
        s.setValue("recent_projects", recent[:8])
        if hasattr(self, "_refresh_recent_projects"):
            self._refresh_recent_projects()

    def _populate_widgets_from_config(self) -> None:
        # Repopulate every editable widget from the loaded config so a Save on any
        # tab does not silently overwrite on-disk values with widget defaults.
        if self.config is None:
            return
        wf = self.config.workflow
        self.aligner.setCurrentText(wf.aligner)
        # Constrain the quantifier to the aligner, then restore the saved choice when it is
        # valid (STAR can be featureCounts or STAR_GeneCounts); otherwise the aligner default
        # stands. Call _on_aligner_changed directly because setCurrentText emits no signal when
        # the value is unchanged (e.g. loading a STAR project while STAR is already current).
        self._on_aligner_changed(self.aligner.currentText())
        if wf.quantifier in self._quantifier_valid_for(wf.aligner):
            self.quantifier.setCurrentText(wf.quantifier)
        self.trim.setChecked(wf.trimming)
        self.rrna.setChecked(wf.rrna_filtering)
        _tr_idx = self.trimmer.findData(getattr(wf, "trimmer", "fastp"))
        self.trimmer.setCurrentIndex(_tr_idx if _tr_idx >= 0 else 0)
        _rt_idx = self.rrna_tool.findData(getattr(wf, "rrna_tool", "sortmerna"))
        self.rrna_tool.setCurrentIndex(_rt_idx if _rt_idx >= 0 else 0)
        self.contam_screen.setChecked(getattr(wf, "contamination_screen", False))
        self.trimmer.setEnabled(self.trim.isChecked())
        self.rrna_tool.setEnabled(self.rrna.isChecked())
        self.enrichment.setChecked(wf.enrichment)
        self.figures.setChecked(wf.figures)
        self.gsva.setChecked(getattr(wf, "gsva", False))
        self.rseqc.setChecked(getattr(wf, "rseqc", False))
        self.meta_analysis.setChecked(getattr(wf, "meta_analysis", False))
        self.per_study_enrichment.setChecked(getattr(wf, "per_study_enrichment", False))
        # Re-sync the dependent enable after both checked-states are set.
        self.per_study_enrichment.setEnabled(
            self.meta_analysis.isEnabled() and self.meta_analysis.isChecked())
        _eng_idx = self.de_engine.findData(getattr(wf, "de_engine", "DESeq2"))
        self.de_engine.setCurrentIndex(_eng_idx if _eng_idx >= 0 else 0)
        _org_idx = self.organellar.findData(getattr(wf, "organellar_genes", "keep"))
        self.organellar.setCurrentIndex(_org_idx if _org_idx >= 0 else 0)
        # Microarray source / log2 (Input Data tab). Block signals so loading does not
        # trigger a redundant config save via _on_micro_option_changed.
        _mc = self.config.microarray
        for _combo, _val in ((self.micro_source, getattr(_mc, "source", "geo_series_matrix")),
                             (self.micro_log2, getattr(_mc, "log2_transform", "auto"))):
            _combo.blockSignals(True)
            _mi = _combo.findData(_val)
            _combo.setCurrentIndex(_mi if _mi >= 0 else 0)
            _combo.blockSignals(False)
        self.fastp_q.setValue(self.config.fastp.qualified_quality_phred)
        self.fastp_len.setValue(self.config.fastp.length_required)
        self.trim_poly_g.setChecked(self.config.fastp.trim_poly_g)
        # Advanced tool parameters (all NULL-safe so older configs still load).
        self.fastp_u.setValue(self.config.fastp.unqualified_percent_limit)
        self.fastp_polyx.setChecked(getattr(self.config.fastp, "trim_poly_x", False))
        _tm = self.config.trimmomatic
        self.tm_sw_q.setValue(getattr(_tm, "sliding_window_quality", 15))
        self.tm_leading.setValue(getattr(_tm, "leading", 3))
        self.tm_trailing.setValue(getattr(_tm, "trailing", 3))
        _rde = self.rd_ensure.findData(getattr(self.config.ribodetector, "ensure", "norrna"))
        self.rd_ensure.setCurrentIndex(_rde if _rde >= 0 else 0)
        self.rd_chunk.setValue(getattr(self.config.ribodetector, "chunk_size", 256))
        self.fs_subset.setValue(getattr(self.config.contamination, "subset", 100000))
        self.fs_conf.setText(getattr(self.config.contamination, "conf", None) or "")
        self.star_twopass.setChecked(self.config.star.twopass_mode)
        self.star_multimap.setValue(self.config.star.multimap_nmax)
        self.star_mismatch.setValue(self.config.star.mismatch_nover_read_lmax)
        self.fc_feature.setText(self.config.featurecounts.feature_type)
        self.fc_attribute.setText(self.config.featurecounts.attribute_type)
        self.de_min_count.setValue(self.config.deseq2.min_count)
        _des = self.de_shrink.findData(self.config.deseq2.shrinkage_method)
        self.de_shrink.setCurrentIndex(_des if _des >= 0 else 0)
        self.design.setText(self.config.deseq2.design_formula)
        self.alpha.setValue(self.config.deseq2.alpha)
        self.lfc_threshold.setValue(self.config.deseq2.lfc_threshold)
        self._refresh_conditions()
        contrasts = self.config.deseq2.contrasts
        contrast = contrasts[0] if contrasts else None
        if contrast:
            self.contrast_factor.setText(contrast.factor)
            self.numerator.setCurrentText(contrast.numerator)
            self.denominator.setCurrentText(contrast.denominator)
        if hasattr(self, "contrast_info"):
            if contrasts and len(contrasts) > 1:
                others = ", ".join(f"{c.numerator} vs {c.denominator}" for c in contrasts[1:])
                self.contrast_info.setText(
                    f"Editing contrast 1 of {len(contrasts)}. The others are preserved on "
                    f"save: {others}.")
                self.contrast_info.setVisible(True)
            else:
                self.contrast_info.setVisible(False)
        ref_level = self.config.deseq2.reference_level
        if ref_level:
            self.reference_level.setCurrentText(next(iter(ref_level.values())))
        self.profile.setCurrentText(self.config.resources.profile)
        self.cores.setValue(self.config.resources.total_threads)
        self.ram.setValue(self.config.resources.total_memory_gb)
        fig = self.config.figures_style
        self.fig_palette.setCurrentText(fig.palette)
        overrides = getattr(fig, "figure_overrides", {}) or {}
        for gkey, widgets in self.fig_override_widgets.items():
            g = overrides.get(gkey, {}) or {}
            for okey, w in widgets.items():
                self._set_override_widget(okey, w, g.get(okey, ""))
        self.fig_point_size.setValue(fig.point_size)
        self.fig_base_font.setValue(fig.base_font_size)
        self.fig_font_family.setCurrentText(fig.font_family or self.FONT_DEFAULT_LABEL)
        self.fig_label_bold.setChecked(fig.label_bold)
        self.fig_gene_italic.setChecked(fig.gene_symbol_italic)
        self.fig_title_bold.setChecked(fig.title_bold)
        self.fig_volcano_top.setValue(fig.volcano_top_n)
        self.fig_heatmap_top.setValue(fig.heatmap_top_n)
        self.fig_pca_ntop.setValue(fig.pca_ntop)
        # Block the DPI signal so loading a project doesn't trigger the px-rescale
        # handler against the previous project's unit; sync _fig_dpi_prev after.
        self.fig_dpi.blockSignals(True)
        self.fig_dpi.setValue(fig.dpi)
        self.fig_dpi.blockSignals(False)
        self._fig_dpi_prev = fig.dpi
        unit = getattr(fig, "dimension_unit", "in") or "in"
        self.fig_dim_unit.blockSignals(True)
        self.fig_dim_unit.setCurrentText(unit)
        self.fig_dim_unit.blockSignals(False)
        self._fig_dim_unit_prev = unit
        self._configure_dim_spins(unit)
        self.fig_width.setValue(self._dim_from_inches(fig.width_in, unit, fig.dpi))
        self.fig_height.setValue(self._dim_from_inches(fig.height_in, unit, fig.dpi))
        _yscale_idx = self.fig_volcano_yscale.findData(fig.volcano_y_scale)
        self.fig_volcano_yscale.setCurrentIndex(_yscale_idx if _yscale_idx >= 0 else 0)
        self.fig_volcano_ycap.setValue(fig.volcano_y_cap)
        self.fig_volcano_ycap.setEnabled(fig.volcano_y_scale == "cap")
        self.fig_volcano_alpha.setValue(fig.volcano_point_alpha)
        self.fig_pca_fixed_aspect.setChecked(fig.pca_fixed_aspect)
        self.fig_sample_labels.setChecked(fig.sample_labels)
        self.fig_heatmap_zlim.setValue(fig.heatmap_zlim)
        self.fig_enrich_show.setValue(fig.enrich_show_category)
        self.fig_ppi_layout.setCurrentText(fig.ppi_layout or "fr")
        self.ppi_score.setValue(self.config.ppi.score_threshold)
        self.ppi_hub_labels.setValue(self.config.ppi.hub_label_count)
        if hasattr(self, "ppi_rebuild_score"):
            self.ppi_rebuild_score.setValue(self.config.ppi.score_threshold)
        goi_path = self.config.gene_sets.custom_gene_list
        if goi_path and self.project_root is not None and (self.project_root / goi_path).exists():
            self.goi_box.setPlainText((self.project_root / goi_path).read_text(encoding="utf-8").strip())
        else:
            self.goi_box.clear()
        self.custom_gmt.setText(self.config.gene_sets.custom_gene_sets or "")
        self.custom_annot.setText(self.config.gene_sets.functional_annotation_table or "")
        self.custom_background.setText(self.config.gene_sets.background_gene_list or "")
        organism = self.config.reference.organism_name
        for i in range(self.reference_list.count()):
            if self.reference_list.item(i).text().startswith(f"{organism} "):
                self.reference_list.setCurrentRow(i)
                self.reference_list.scrollToItem(self.reference_list.item(i))
                break
        # Backfill the per-organism enrichment/PPI ids on reopen for projects saved
        # before this feature. Only fill when None (don't override an intentional
        # None); keytype is omitted so count-matrix's deliberate None is preserved.
        entry = catalog_entry_for_organism(organism)
        if entry is not None:
            enr = self.config.enrichment
            if enr.orgdb is None:
                enr.orgdb = entry.get("orgdb") or None
            if enr.kegg_organism is None:
                enr.kegg_organism = entry.get("kegg_organism") or None
            if enr.gprofiler_organism is None:
                enr.gprofiler_organism = entry.get("gprofiler_organism") or None
            if self.config.ppi.taxon is None:
                self.config.ppi.taxon = entry.get("string_taxon")
        # Restore the custom-reference fields so reopening a project does not show
        # them blank (which would invite an accidental empty re-lock).
        ref = self.config.reference
        if ref.mode == "custom":
            self.ref_organism.setText(ref.organism_name if ref.organism_name != "unset" else "")
            self.ref_genome.setText(ref.genome_fasta or "")
            self.ref_annotation.setText(ref.annotation_file or "")
            if ref.annotation_format in ("gtf", "gff3"):
                self.ref_format.setCurrentText(ref.annotation_format)
        self._apply_input_mode_ui()

    def _design_variables(self) -> list[str]:
        # Parse a DESeq2 design formula (e.g. "~ batch + condition") into the
        # metadata columns it references, plus the contrast factor, so missing
        # columns are flagged in Sanity Checks before DESeq2 runs.
        if self.config is None:
            return []
        # Read the LIVE design field (mirrors _active_contrast, which reads the live combos in the same
        # validation call) so a design edited via the helper or typed but not yet saved is checked
        # against the metadata columns — reading the saved formula would give false reassurance.
        raw = self.design.text() if getattr(self, "design", None) is not None else self.config.deseq2.design_formula
        formula = str(raw).split("~", 1)[-1]
        tokens = re.split(r"[+*:]", formula)
        variables = [t.strip() for t in tokens if t.strip()]
        for contrast in self.config.deseq2.contrasts:
            if contrast.factor and contrast.factor not in variables:
                variables.append(contrast.factor)
        return variables

    def _select_fastqs(self) -> None:
        if not self._require_project():
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Select FASTQ files", "", "FASTQ (*.fastq *.fq *.fastq.gz *.fq.gz)")
        if not files:
            return
        rows = detect_fastq_inputs(files)
        df = dataframe_from_rows(rows)
        # Under WSL the run reads samples.tsv inside Linux, so a Windows-drive FASTQ path
        # (C:\...) is unresolvable — translate the file columns to /mnt/<drive>/... first.
        if getattr(self, "use_wsl", None) is not None and self.use_wsl.isChecked():
            for _col in ("fastq_1", "fastq_2"):
                if _col in df.columns:
                    df[_col] = df[_col].map(lambda p: windows_to_wsl_path(p) if p else p)
        assert self.project_root is not None
        save_metadata(df, self.project_root / "config" / "samples.auto_generated.tsv")
        save_metadata(df, self.project_root / "config" / "samples.tsv")
        # Selecting FASTQs switches the project back to the alignment route. Clear
        # any prior count-matrix / microarray mode so the run takes the fastq
        # branch (the SRA/count-matrix/GEO handlers set their own type the same way).
        self.config.input.type = "fastq"
        self.config.input.count_matrix = None
        self.config.input.deseq2_results = None
        self.config.microarray.gse_accession = None
        self.manager.save_config(self.project_root, self.config)
        self.metadata_table.load_dataframe(df)
        self._apply_input_mode_ui()
        self.input_preview.setPlainText(df.to_string(index=False))

    def _import_metadata(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import metadata", "", "Tables (*.tsv *.csv *.xlsx)")
        if not path:
            return
        p = Path(path)
        # Wrap the read like the count-matrix / DESeq2 / microarray importers do, so a malformed table
        # shows a clean message instead of a raw traceback dialog via the global excepthook.
        try:
            if p.suffix.lower() == ".xlsx":
                df = pd.read_excel(p, dtype=str).fillna("")
            else:
                df = pd.read_csv(p, sep="\t" if p.suffix.lower() == ".tsv" else ",", dtype=str).fillna("")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not read the table: {exc}")
            return
        self.metadata_table.load_dataframe(df)

    def metadata_add_row(self) -> None:
        self.metadata_table.append_empty_row()

    def metadata_delete_rows(self) -> None:
        self.metadata_table.delete_selected_rows()

    def metadata_duplicate_rows(self) -> None:
        self.metadata_table.duplicate_selected_rows()

    def metadata_autofill(self) -> None:
        self.metadata_table.autofill_replicates()

    def _add_column(self) -> None:
        name, ok = QInputDialog.getText(self, APP_NAME, "New column name:")
        if ok and name.strip():
            # Reject a duplicate name: two same-named columns collapse (last-wins) in to_dataframe,
            # silently losing one column's data on save.
            if name.strip() in self.metadata_table.column_names():
                QMessageBox.warning(self, APP_NAME, f"A column named '{name.strip()}' already exists. Pick a different name.")
                return
            self.metadata_table.add_column(name.strip())

    def _rename_column(self) -> None:
        col = self.metadata_table.currentColumn()
        if col < 0:
            return
        current = self.metadata_table.column_names()[col]
        name, ok = QInputDialog.getText(self, APP_NAME, "Rename column:", text=current)
        if ok and name.strip():
            # Reject a name that collides with a DIFFERENT column (renaming to itself is a harmless no-op),
            # to avoid the silent last-wins data loss when to_dataframe keys rows by header text.
            others = [n for i, n in enumerate(self.metadata_table.column_names()) if i != col]
            if name.strip() in others:
                QMessageBox.warning(self, APP_NAME, f"A column named '{name.strip()}' already exists. Pick a different name.")
                return
            self.metadata_table.rename_column(col, name.strip())

    def _remove_column(self) -> None:
        col = self.metadata_table.currentColumn()
        if col >= 0:
            self.metadata_table.remove_column(col)

    def _assign_condition(self) -> None:
        value, ok = QInputDialog.getText(self, APP_NAME, "Assign condition to selected rows:")
        if ok and value.strip():
            self.metadata_table.assign_condition(value.strip())

    def _paste_metadata(self) -> None:
        # Explicit button so pasting works regardless of gesture (e.g. after a
        # double-click would otherwise route Ctrl+V into the cell editor).
        self.metadata_table.paste_clipboard()

    def _export_metadata(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export metadata", "samples.tsv", "TSV (*.tsv);;CSV (*.csv)")
        if not path:
            return
        sep = "," if path.lower().endswith(".csv") else "\t"
        self.metadata_table.to_dataframe().to_csv(path, sep=sep, index=False)

    def _restore_auto_metadata(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        auto = self.project_root / "config" / "samples.auto_generated.tsv"
        if auto.exists():
            self.metadata_table.load_tsv(auto)

    def _save_metadata(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        save_metadata(self.metadata_table.to_dataframe(), self.project_root / "config" / "samples.tsv")
        self.metadata_messages.setPlainText("Saved config/samples.tsv")

    def _active_contrast(self) -> tuple[str, str] | None:
        # The contrast arms drive the multi-study confounding gate (which condition-vs-condition is
        # being compared); read them from the DE-tab combos so the gate fires for >2-level designs.
        num = self.numerator.currentText().strip() if hasattr(self, "numerator") else ""
        den = self.denominator.currentText().strip() if hasattr(self, "denominator") else ""
        return (num, den) if num and den else None

    def _validate_metadata(self) -> None:
        allow_pending_sra = self.config is not None and self.config.input.type in (
            "sra", "count_matrix", "microarray", "deseq2_results")
        messages = validate_metadata(
            self.metadata_table.to_dataframe(),
            allow_pending_sra=allow_pending_sra,
            design_variables=self._design_variables(),
            contrast=self._active_contrast(),
        )
        self.metadata_messages.setPlainText(self._format_messages(messages))

    def _select_reference(self) -> None:
        if not self._require_project():
            return
        row = self.reference_list.currentRow()
        catalog = load_reference_catalog()
        if row < 0 or row >= len(catalog) or self.config is None or self.project_root is None:
            return
        entry = catalog[row]
        ref = self.config.reference
        ref.mode = "preset"
        ref.organism_name = str(entry["organism_name"])
        # Propagate the per-organism enrichment/PPI identifiers the workflow reads.
        enr = self.config.enrichment
        enr.orgdb = entry.get("orgdb") or None
        enr.kegg_organism = entry.get("kegg_organism") or None
        enr.gprofiler_organism = entry.get("gprofiler_organism") or None
        self.config.ppi.taxon = entry.get("string_taxon")
        # Don't clobber the microarray SYMBOL keytype (mirrors the L468 guard).
        if self.config.input.type != "microarray":
            enr.keytype = entry.get("enrichment_keytype") or None
        ref.strain = str(entry.get("strain") or "")
        ref.genome_size_category = str(entry.get("genome_size_category") or "custom")
        ref.source = str(entry.get("source") or "")
        ref.release = str(entry.get("release") or "")
        ref.package_id = str(entry.get("assembly_accession") or "")
        gtf_url = entry.get("annotation_gtf_url")
        fasta_url = entry.get("genome_fasta_url")
        if fasta_url and gtf_url:
            # Wire the verified download URLs + canonical local paths so the
            # pipeline fetches and indexes this reference automatically.
            ref.genome_fasta_url = str(fasta_url)
            ref.annotation_gtf_url = str(gtf_url)
            ref.genome_fasta = "references/genome.fa"
            ref.annotation_file = "references/annotation.gtf"
            ref.annotation_format = "gtf"
            note = "Reference selected; the pipeline will download and index it on run."
        else:
            # Clear local paths too, or a lingering custom path makes the run gate
            # falsely pass while the (URL-based) download rules have nothing to fetch.
            ref.genome_fasta_url = None
            ref.annotation_gtf_url = None
            ref.genome_fasta = None
            ref.annotation_file = None
            note = (
                "This preset has no ready GTF (see notes). Use the Custom Reference "
                "section below to supply your own genome FASTA + annotation."
            )
        self.manager.save_config(self.project_root, self.config)
        if ref.genome_fasta_url:
            self.statusBar().showMessage(f"Reference set: {ref.organism_name} — ready to run.", 6000)
        else:
            self.statusBar().showMessage(
                "This preset has no download URLs — supply a custom genome + annotation below.", 8000)
        details = "\n".join(f"{k}: {v}" for k, v in entry.items())
        prefix = "Reference set: " if ref.genome_fasta_url else ""
        self.reference_details.setPlainText(f"{prefix}{note}\n\nAssembly: {entry.get('assembly_accession')} ({entry.get('assembly_name')})  release: {entry.get('release')}\n\n{details}")
        self._update_organism_label()
        self._update_enrichment_warning()

    def _workflow_settings_problem(self) -> str | None:
        # Catch contrasts DESeq2 will reject, before they reach the run.
        num = self.numerator.currentText().strip()
        den = self.denominator.currentText().strip()
        if num and den and num == den:
            return (f"Numerator and denominator are both '{num}'. DESeq2 needs two "
                    "different groups for a contrast.")
        factor = self.contrast_factor.text().strip() or "condition"
        cols = list(self.metadata_table.column_names()) if hasattr(self.metadata_table, "column_names") else []
        if cols and factor not in cols:
            return (f"Contrast factor '{factor}' is not a metadata column. "
                    f"Available columns: {', '.join(cols)}.")
        return None

    def _save_workflow_settings(self, _checked: bool = False, validate: bool = True) -> bool:
        # _checked absorbs QPushButton.clicked's bool, which would otherwise bind to
        # `validate` and silently disable the contrast guard on the Save button path.
        if self.config is None or self.project_root is None:
            return False
        # Only the differential-expression modes use the contrast, so don't let a
        # stale numerator/denominator block Unlock / recovery / figure regeneration.
        if validate:
            problem = self._workflow_settings_problem()
            if problem:
                QMessageBox.warning(self, APP_NAME, problem + "\n\nWorkflow settings were not saved.")
                return False
        self.config.workflow.aligner = self.aligner.currentText()  # type: ignore[assignment]
        self.config.workflow.quantifier = self.quantifier.currentText()  # type: ignore[assignment]
        self.config.workflow.trimming = self.trim.isChecked()
        self.config.workflow.trimmer = self.trimmer.currentData()  # type: ignore[assignment]
        self.config.workflow.rrna_filtering = self.rrna.isChecked()
        self.config.workflow.rrna_tool = self.rrna_tool.currentData()  # type: ignore[assignment]
        self.config.workflow.contamination_screen = self.contam_screen.isChecked()
        self.config.workflow.enrichment = self.enrichment.isChecked()
        self.config.workflow.figures = self.figures.isChecked()
        self.config.workflow.gsva = self.gsva.isChecked()
        self.config.workflow.rseqc = self.rseqc.isChecked()
        self.config.workflow.meta_analysis = self.meta_analysis.isChecked()
        self.config.workflow.per_study_enrichment = self.per_study_enrichment.isChecked()
        self.config.workflow.de_engine = self.de_engine.currentData()  # type: ignore[assignment]
        self.config.workflow.organellar_genes = self.organellar.currentData()  # type: ignore[assignment]
        # Custom gene-set files are Snakemake inputs read INSIDE WSL, so a Browse-picked Windows/UNC
        # path (C:\... or \\wsl.localhost\...) must be WSL-resolved like the reference genome above —
        # otherwise the raw path is unusable in WSL and aborts the whole run at DAG build. Convert only
        # a genuine Windows path; an already-/mnt or POSIX path (reloaded from config, or native Linux)
        # is left unchanged so a reload+save never double-converts (windows_to_wsl_path is not idempotent).
        def _to_wsl_input(text: str) -> str | None:
            t = text.strip()
            if not t:
                return None
            is_windows_path = t.startswith("\\\\") or "\\" in t or (len(t) >= 2 and t[1] == ":")
            return windows_to_wsl_path(t) if is_windows_path else t
        self.config.gene_sets.custom_gene_sets = _to_wsl_input(self.custom_gmt.text())
        self.config.gene_sets.functional_annotation_table = _to_wsl_input(self.custom_annot.text())
        self.config.gene_sets.background_gene_list = _to_wsl_input(self.custom_background.text())
        self.config.fastp.qualified_quality_phred = self.fastp_q.value()
        self.config.fastp.length_required = self.fastp_len.value()
        self.config.fastp.trim_poly_g = self.trim_poly_g.isChecked()
        # Advanced tool parameters.
        self.config.fastp.unqualified_percent_limit = self.fastp_u.value()
        self.config.fastp.trim_poly_x = self.fastp_polyx.isChecked()
        self.config.trimmomatic.sliding_window_quality = self.tm_sw_q.value()
        self.config.trimmomatic.leading = self.tm_leading.value()
        self.config.trimmomatic.trailing = self.tm_trailing.value()
        self.config.ribodetector.ensure = self.rd_ensure.currentData()  # type: ignore[assignment]
        self.config.ribodetector.chunk_size = self.rd_chunk.value()
        self.config.contamination.subset = self.fs_subset.value()
        self.config.contamination.conf = self.fs_conf.text().strip() or None
        self.config.star.twopass_mode = self.star_twopass.isChecked()
        self.config.star.multimap_nmax = self.star_multimap.value()
        self.config.star.mismatch_nover_read_lmax = self.star_mismatch.value()
        self.config.featurecounts.feature_type = self.fc_feature.text().strip() or "exon"
        self.config.featurecounts.attribute_type = self.fc_attribute.text().strip() or "gene_id"
        self.config.deseq2.min_count = self.de_min_count.value()
        self.config.deseq2.shrinkage_method = self.de_shrink.currentData()  # type: ignore[assignment]
        self.config.deseq2.design_formula = self.design.text()
        self.config.deseq2.alpha = self.alpha.value()
        self.config.deseq2.lfc_threshold = self.lfc_threshold.value()
        factor = self.contrast_factor.text().strip() or "condition"
        if self.reference_level.currentText().strip():
            self.config.deseq2.reference_level = {factor: self.reference_level.currentText().strip()}
        if self.config.deseq2.contrasts:
            contrast = self.config.deseq2.contrasts[0]
            contrast.factor = factor
            contrast.numerator = self.numerator.currentText().strip() or contrast.numerator
            contrast.denominator = self.denominator.currentText().strip() or contrast.denominator
            contrast.name = f"{contrast.numerator}_vs_{contrast.denominator}"
        self.manager.save_config(self.project_root, self.config)
        return True

    def _detect_resources(self) -> None:
        # Detection probes WSL/conda (~seconds), so run it off-thread; the busy bar
        # animates and the UI stays responsive instead of freezing.
        if getattr(self, "_detect_worker", None) is not None and self._detect_worker.isRunning():
            return
        root = self.project_root or Path(self.workdir.text())
        profile = self.profile.currentText()
        self.statusBar().showMessage("Detecting system resources…")
        self.resources_busy.setVisible(True)

        def work():
            system = detect_system(root)
            return system, recommend_profile(system, profile)

        self._detect_worker = BackgroundWorker(work)
        self._detect_worker.done.connect(self._on_detect_done)
        self._detect_worker.failed.connect(self._on_detect_failed)
        self._detect_worker.start()

    def _on_detect_failed(self, exc: object) -> None:
        self.resources_busy.setVisible(False)
        self.statusBar().showMessage(f"Resource detection failed: {exc}", 8000)

    def _on_profile_changed(self, profile: str) -> None:
        # Recompute cores/RAM for the chosen preset using the last detected system,
        # so switching profile reflects immediately instead of staying stale until
        # the next Detect. Custom keeps whatever the user typed.
        system = getattr(self, "_last_system", None)
        if system is None or profile == "custom":
            return
        rec = recommend_profile(system, profile)
        self.cores.setValue(int(rec["total_threads"]))
        self.ram.setValue(int(rec["total_memory_gb"]))

    def _on_detect_done(self, result: object) -> None:
        self.resources_busy.setVisible(False)
        system, rec = result
        self._last_system = system
        self.cores.setValue(int(rec["total_threads"]))
        self.ram.setValue(int(rec["total_memory_gb"]))
        info = (
            f"{system.cpu_model} — {system.physical_cores} cores "
            f"({system.logical_threads} threads), {system.total_ram_gb:.0f} GB RAM, "
            f"{system.disk_free_gb:.0f} GB free disk."
        )
        if getattr(system, "wsl_ram_gb", 0):
            # The pipeline runs in WSL2, whose caps (not the host total) bound it.
            info += (f"\nWSL2 sees {system.wsl_cpus} CPUs / {system.wsl_ram_gb:.0f} GB — "
                     "recommendations use these limits. Raise them in %UserProfile%\\.wslconfig "
                     "([wsl2] memory=, processors=) then 'wsl --shutdown' if you want more.")
        self.system_info_label.setText(info)
        self.recommendation_label.setText(
            f"Recommended for the '{self.profile.currentText()}' profile: "
            f"{rec['total_threads']} cores and {rec['total_memory_gb']} GB RAM."
        )
        self.statusBar().showMessage(
            f"Detected {system.physical_cores} cores / {system.total_ram_gb:.0f} GB RAM — "
            f"recommending {rec['total_threads']} cores, {rec['total_memory_gb']} GB.",
            8000,
        )

    def _save_resources(self) -> None:
        if self.config is None or self.project_root is None:
            return
        self.config.resources.profile = self.profile.currentText()  # type: ignore[assignment]
        self.config.resources.total_threads = self.cores.value()
        self.config.resources.total_memory_gb = self.ram.value()
        self.manager.save_config(self.project_root, self.config)

    def _estimate_runtime(self) -> None:
        if not self._require_project() or self.config is None:
            return
        if getattr(self, "_estimate_worker", None) is not None and self._estimate_worker.isRunning():
            return
        # Estimate against the machine this instance runs on, not the possibly stale
        # cores/RAM saved in the project config. Detection probes WSL (~seconds), so
        # run it off-thread; reuse the last detection if resources were already probed.
        self.runtime_busy.setVisible(True)
        cfg = self.config
        df = self.metadata_table.to_dataframe()
        root = self.project_root or Path(self.workdir.text())
        profile = self.profile.currentText()
        cached = getattr(self, "_last_system", None)

        def work():
            system = cached or detect_system(root)
            if profile == "custom":
                # Custom keeps the cores/RAM the user set by hand.
                threads = cfg.resources.total_threads
                mem = cfg.resources.total_memory_gb
            else:
                rec = recommend_profile(system, profile)
                threads = int(rec["total_threads"])
                mem = int(rec["total_memory_gb"])
            # Calibration keys on config.resources.total_threads (the same integer the run
            # and Hook-2 use), so read and write always hit the same QSettings key.
            cf, n = calibration_factor(int(cfg.resources.total_threads))
            estimate = estimate_runtime(cfg, df, threads=threads, memory_gb=mem,
                                        calibration_factor=cf, calibration_runs=n)
            return system, threads, mem, estimate

        self._estimate_worker = BackgroundWorker(work)
        self._estimate_worker.done.connect(self._on_estimate_done)
        self._estimate_worker.failed.connect(self._on_estimate_failed)
        self._estimate_worker.start()

    def _on_estimate_done(self, result: object) -> None:
        self.runtime_busy.setVisible(False)
        system, threads, mem, estimate = result
        self._last_system = system
        # Keep the estimate and the actual run in agreement: the run reads these
        # cores/RAM (via _save_resources -> build_snakemake_command). Update the
        # in-memory config and the spinboxes; the disk save happens on run/save.
        if self.profile.currentText() != "custom":
            self.cores.setValue(int(threads))
            self.ram.setValue(int(mem))
            if self.config is not None:
                self.config.resources.total_threads = int(threads)
                self.config.resources.total_memory_gb = int(mem)
        self.runtime_text.setPlainText("\n".join(f"{k}: {v}" for k, v in estimate.items()))

    def _on_estimate_failed(self, exc: object) -> None:
        self.runtime_busy.setVisible(False)
        # Fall back to a config-based estimate so the button still works offline.
        try:
            estimate = estimate_runtime(self.config, self.metadata_table.to_dataframe())
            self.runtime_text.setPlainText("\n".join(f"{k}: {v}" for k, v in estimate.items()))
        except Exception:
            self.runtime_text.setPlainText(f"Could not estimate runtime: {exc}")

    def _run_sanity_checks(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        self.sanity_busy.setVisible(True)
        QApplication.processEvents()
        try:
            allow_pending_sra = self.config is not None and self.config.input.type in (
                "sra", "count_matrix", "microarray", "deseq2_results")
            messages = validate_metadata(
                self.metadata_table.to_dataframe(),
                allow_pending_sra=allow_pending_sra,
                design_variables=self._design_variables(),
                contrast=self._active_contrast(),
            )
            messages = list(messages) + self._enrichment_config_messages()
            write_check(self.project_root, "01_input_validation", messages)
            text = self._format_messages(messages)
            # Show the aggregate phase-check summary, then the per-message detail
            # below it (the summary previously overwrote the detail entirely).
            self._refresh_phase_checks()
            if text:
                self.sanity_text.append("")
                self.sanity_text.append("Latest validation detail:")
                self.sanity_text.append(text)
        finally:
            self.sanity_busy.setVisible(False)

    def _phase_check_statuses(self) -> dict[str, str]:
        # Read every checks/*.json the GUI and pipeline have produced.
        statuses: dict[str, str] = {}
        if self.project_root is None:
            return statuses
        import json

        for path in sorted((self.project_root / "checks").glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            statuses[path.stem] = payload.get("status", "PASS")
        return statuses

    def _refresh_phase_checks(self) -> None:
        if not self._require_project():
            return
        statuses = self._phase_check_statuses()
        if not statuses:
            self.sanity_text.setPlainText("No phase checks yet. Run checks or the pipeline first.")
            return
        priority = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
        worst = max(statuses.values(), key=lambda s: priority.get(s, 0))
        lines = [f"Overall: {worst}", ""]
        lines += [f"{name}: {status}" for name, status in statuses.items()]
        self.sanity_text.setPlainText("\n".join(lines))

    def _run_gate_ok(self) -> bool:
        # A reference must be resolvable, or the pipeline dies mid-run with a
        # cryptic "genome_fasta_url is not set". Block early with clear guidance.
        if self.config is not None:
            no_reference_mode = self.config.input.type in ("count_matrix", "microarray", "deseq2_results")
            ref = self.config.reference
            has_url = bool(ref.genome_fasta_url and ref.annotation_gtf_url)
            has_local = bool(ref.genome_fasta and ref.annotation_file)
            # Count-matrix and microarray modes skip alignment, so no reference is required.
            if not no_reference_mode and not (has_url or has_local):
                QMessageBox.warning(
                    self, APP_NAME,
                    "No reference is set, so the run cannot start.\n\n"
                    "Open the Reference Manager tab and either select a preset organism "
                    "and click 'Use Selected Preset', or import a custom genome FASTA + "
                    "annotation. Then start the run again.",
                )
                return False
            # Single-end and paired-end are both supported, but a run must be homogeneous
            # (one rule cannot emit both a 1- and a 2-file trimmed output). Block mixed layouts.
            if not no_reference_mode and self.project_root is not None:
                samples_path = self.project_root / "config" / "samples.tsv"
                if samples_path.exists():
                    try:
                        sdf = pd.read_csv(samples_path, sep="\t", dtype=str).fillna("")
                        layouts = ({str(v).lower() for v in sdf["layout"].tolist()} & {"single", "paired"}
                                   if "layout" in sdf.columns else set())
                    except Exception:
                        layouts = set()
                    if {"single", "paired"} <= layouts:
                        QMessageBox.warning(
                            self, APP_NAME,
                            "Mixed paired-end and single-end samples in one run are not supported.\n\n"
                            "Split them into two projects (one per layout), then start the run again.",
                        )
                        return False
            goi = self.config.gene_sets.custom_gene_list
            if goi and self.project_root is not None and not (self.project_root / goi).exists():
                QMessageBox.warning(
                    self, APP_NAME,
                    f"The genes-of-interest file '{goi}' is missing. Re-save your genes "
                    "of interest on the Outputs tab, or clear the list, before running.",
                )
                return False
        # Block on FAIL; require explicit approval for REVIEW_REQUIRED.
        statuses = self._phase_check_statuses()
        if any(s == "FAIL" for s in statuses.values()):
            QMessageBox.warning(self, APP_NAME, "Cannot start: one or more sanity checks FAILED. Resolve them first.")
            return False
        if any(s == "REVIEW_REQUIRED" for s in statuses.values()) and not self.approve_review.isChecked():
            QMessageBox.warning(self, APP_NAME, "REVIEW_REQUIRED checks present. Tick the approval box on the Sanity tab to proceed.")
            return False
        return True

    def _start_snakemake(self, mode: str) -> None:
        # Never let a failure here crash the app; surface it in the log + a dialog.
        try:
            self._start_snakemake_impl(mode)
        except Exception as exc:
            import traceback as _tb
            detail = _tb.format_exc()
            try:
                self.log_text.append(f"Failed to start run: {exc}")
                self.log_text.append(detail)
            except Exception:
                pass
            self._pending_recover = False  # launch failed, no runner started: don't strand the recover flag
            self._set_running_ui(False)
            QMessageBox.critical(self, APP_NAME, f"Failed to start the run:\n\n{exc}")

    def _start_snakemake_impl(self, mode: str) -> None:
        if self.config is None or self.project_root is None:
            return
        # Guard double-starts: one snakemake per directory at a time.
        if self._run_active or (self.runner is not None and self.runner.is_running()):
            self.log_text.append("A run is already active. Stop it before starting another.")
            self._pending_recover = False  # a stranded recover flag would mis-handle the active run's finish
            return
        # An existing project keeps its own copy of workflow/, so a workflow fix from an
        # app update would not reach it. Re-sync the bundled scripts when the project's
        # recorded workflow_version is older than this build's, before any run or figure
        # regeneration. Best-effort: never block a run if the copy fails.
        try:
            synced = self.manager.sync_workflow_if_outdated(self.project_root)
            if synced:
                self.log_text.append(f"Updated project workflow scripts to match this app version ({synced}).")
        except Exception as exc:
            self.log_text.append(f"Could not refresh project workflow scripts: {exc}")
        if mode in ("run", "resume", "recover") and not self._run_gate_ok():
            self._refresh_resume_banner()  # a blocked resume/recover must not leave the banner stranded hidden
            return
        # Backstop the enrichment trap directly at run start (the sanity-check gate
        # only fires if the user ran checks first). Not for recovery.
        if mode in ("run", "resume") and not self._confirm_enrichment_config():
            self._refresh_resume_banner()
            return
        # Persist the in-memory metadata table so the run uses current edits;
        # Snakemake reads config/samples.tsv from disk, not the GUI table.
        save_metadata(self.metadata_table.to_dataframe(), self.project_root / "config" / "samples.tsv")
        # Validate the contrast only for the differential-expression modes; unlock,
        # dry-run and the figure/ppi/goi regenerations reuse existing DE results.
        if not self._save_workflow_settings(validate=mode in ("run", "resume", "recover")):
            return  # invalid contrast; the user was warned
        self._save_resources()
        # Persist figure-style + PPI controls so in-session edits are honored by the run
        # (previously only Save/Regenerate applied them; a plain Run dropped them).
        self._apply_figure_style()
        run_tag = _new_run_tag() if self.use_wsl.isChecked() else None
        command = build_snakemake_command(
            self.project_root,
            self.config,
            mode=mode,
            use_wsl=self.use_wsl.isChecked(),
            run_tag=run_tag,
        )
        self.command_text.setText(command.display)
        if not self.use_wsl.isChecked() and shutil.which("snakemake") is None:
            self.log_text.append("Snakemake is not available on PATH. Command was constructed but not started.")
            self.log_text.append(command.display)
            self._pending_recover = False  # no runner will start, so don't strand the recover flag
            self._refresh_resume_banner()
            return
        if self.use_wsl.isChecked() and shutil.which("wsl") is None:
            self.log_text.append("WSL is not available on PATH. Command was constructed but not started.")
            self.log_text.append(command.display)
            self._pending_recover = False
            self._refresh_resume_banner()
            return
        import time

        self.runner = SnakemakeRunner(self.project_root, command)
        self.runner_thread = RunnerThread(self.runner)
        self.runner_thread.line.connect(self._on_run_line)
        self.runner_thread.finished_with_code.connect(self._on_run_finished)
        self._run_mode = mode
        self._recovery_offered = False
        self._run_error_detected = False
        self._env_broken_detected = False
        self._mapping_checked = set()
        self._mapping_halt_decided = False
        self._saw_star_align = False
        self._stop_in_progress = False
        self._set_running_ui(True)
        if mode in ("run", "resume", "recover", "figures", "goi", "ppi", "term"):
            # Sub-runs launched from the Outputs / PPI tabs report progress here, so
            # bring the Run Monitor forward — otherwise the click looks like a no-op.
            if hasattr(self, "run_monitor_page"):
                self.tabs.setCurrentWidget(self.run_monitor_page)
            self.progress.setValue(0)
            self.progress.setStyleSheet("")
            status = {"figures": "Regenerating figures...",
                      "goi": "Generating genes-of-interest outputs...",
                      "ppi": "Rebuilding PPI network...",
                      "term": "Building enrichment-term heatmap..."}.get(mode, "Running...")
            self._set_run_status(status, "#2C6FB6")
            self.phase_label.setText("Current step: starting...")
            self._run_start = time.monotonic()
            # Wall-clock start for the timing report (only for an actual pipeline
            # run, not a figures/GOI regeneration).
            if mode in ("run", "resume", "recover"):
                self._run_start_wall = datetime.now().isoformat(timespec="seconds")
                self._run_finish_wall = None
            # Hook 1 (runtime calibration): stash the prediction for a fresh FULL run only, so
            # _on_run_finished can compare it against the actual wall time. resume/recover run
            # partial DAGs (wall undercounts), so they are excluded.
            self._active_estimate = None
            if mode == "run" and self.config is not None:
                try:
                    cores = int(self.config.resources.total_threads)
                    cf, n = calibration_factor(cores)
                    est = estimate_runtime(self.config, self.metadata_table.to_dataframe(),
                                           threads=self.config.resources.total_threads,
                                           memory_gb=self.config.resources.total_memory_gb,
                                           calibration_factor=cf, calibration_runs=n)
                    # Only learn hardware speed from a compute-heavy LOCAL alignment run:
                    # fastq/mixed with no network read download and a consistent (alignment-
                    # shaped) workload. sra/microarray carry network download variance, and
                    # count_matrix/deseq2_results are a different (tiny) shape — neither should
                    # feed the shared per-machine factor.
                    calibratable = self.config.input.type in ("fastq", "mixed")
                    self._active_estimate = {
                        "predicted_raw": est["raw_compute_minutes"],
                        "gbase": est["sequencing_gbase"], "aligner": est["aligner"],
                        "calibratable": calibratable, "cores": cores,
                    }
                except Exception:
                    self._active_estimate = None
            self.elapsed_timer.start(1000)
        else:
            self.phase_label.setText("")  # clear a stale phase from the previous run
            self._set_run_status("Running..." if mode == "dry-run" else "Unlocking...", "#2C6FB6")
        self.runner_thread.start()

    def _generate_reports(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        reports = self.project_root / "results" / "reports"
        # If a real pipeline run already produced reports, display those immediately.
        if (reports / "run_summary.txt").exists():
            self._display_reports()
            return
        if getattr(self, "_reports_worker", None) is not None and self._reports_worker.isRunning():
            return
        # Building the GUI-side summary probes WSL tool versions (subprocess calls
        # that can take many seconds on a cold WSL), so do it off the UI thread.
        self.report_text.setPlainText("Generating reports…")
        cfg = self.config
        root = self.project_root
        df = self.metadata_table.to_dataframe()
        started = getattr(self, "_run_start_wall", None)
        finished = getattr(self, "_run_finish_wall", None)
        use_wsl = getattr(self, "use_wsl", None) is not None and self.use_wsl.isChecked()

        def work():
            estimate = None
            if cfg:
                cf, n = calibration_factor(int(cfg.resources.total_threads))
                estimate = estimate_runtime(cfg, df, calibration_factor=cf, calibration_runs=n)
            write_timing_summary(root, estimate, run_started=started, run_finished=finished)
            write_run_summary(root, data_path("default_config.yaml"), use_wsl=use_wsl)
            return True

        worker = BackgroundWorker(work)
        worker.done.connect(lambda _=None: self._display_reports())
        worker.failed.connect(lambda exc: self.report_text.setPlainText(f"Could not generate reports: {exc}"))
        self._reports_worker = worker
        worker.start()

    def _display_reports(self) -> None:
        if getattr(self, "_closing", False) or self.project_root is None:
            return
        reports = self.project_root / "results" / "reports"
        sections = []
        for name in ("run_summary.txt", "timing_summary.txt"):
            path = reports / name
            if path.exists():
                sections.append(f"===== {name} =====\n{path.read_text(encoding='utf-8')}")
        sanity = self.project_root / "checks" / "sanity_checks.txt"
        if sanity.exists():
            sections.append(f"===== sanity_checks.txt =====\n{sanity.read_text(encoding='utf-8')}")
        self.report_text.setPlainText("\n\n".join(sections) if sections else "No reports generated yet.")

    def _disable_combo_items(self, combo: QComboBox, labels: set[str], suffix: str = "") -> None:
        # Show but disable scaffolded options so they can't be selected; append a
        # suffix to make the unavailability obvious in the dropdown.
        model = combo.model()
        for i in range(combo.count()):
            if combo.itemText(i) in labels:
                item = model.item(i) if hasattr(model, "item") else None
                if item is not None:
                    item.setEnabled(False)
                if suffix:
                    combo.setItemText(i, combo.itemText(i) + suffix)

    def _refresh_output_table_pick(self) -> None:
        # Mode-aware table list: alignment-only counts.txt is meaningless for
        # count-matrix/microarray runs, so only offer it for the fastq/sra route.
        if not hasattr(self, "output_table_pick"):
            return
        itype = self.config.input.type if self.config is not None else "sra"
        # limma-voom does not produce the DESeq2-specific equivalence (unchanged) table.
        voom = self.config is not None and getattr(self.config.workflow, "de_engine", "DESeq2") == "limma-voom"
        if itype == "deseq2_results":
            # No counts/normalized/unchanged/wilcoxon outputs in this mode.
            items = ["results/deseq2/deseq2_results.csv",
                     "results/deseq2/upregulated_genes.csv",
                     "results/deseq2/downregulated_genes.csv",
                     "results/enrichment/kegg_ora.csv", "results/enrichment/kegg_gsea.csv",
                     "results/stats/set_overlap.csv",
                     "results/networks/enrichment_emap_nodes.csv",
                     "results/networks/enrichment_genemap_nodes.csv",
                     "results/networks/string_ppi_nodes.csv", "results/networks/ppi_hub_genes.csv"]
        else:
            items = ["results/deseq2/deseq2_results.csv",
                     "results/deseq2/normalized_counts.csv"]
            if not voom:
                items.append("results/deseq2/unchanged_genes.csv")
            if itype in ("sra", "fastq"):
                items.insert(0, "results/counts/counts.txt")
            items += ["results/enrichment/kegg_ora.csv", "results/enrichment/kegg_gsea.csv",
                      "results/stats/wilcoxon_results.csv", "results/stats/set_overlap.csv",
                      "results/networks/enrichment_emap_nodes.csv",
                      "results/networks/enrichment_genemap_nodes.csv",
                      "results/networks/string_ppi_nodes.csv", "results/networks/ppi_hub_genes.csv"]
        # Extra entries whose display name differs from the project-relative path go
        # through userData: (display, relative-path). Plain strings above resolve via
        # currentText() as before.
        extra: list[tuple[str, str]] = []
        # Keep any extracted per-term gene tables reachable across a picker rebuild.
        if self.project_root is not None:
            terms_dir = self.project_root / "results" / "enrichment" / "terms"
            if terms_dir.exists():
                items += [f"results/enrichment/terms/{p.name}"
                          for p in sorted(terms_dir.glob("*_genes.csv"))]
            # Genes-of-interest DESeq2 subset (present only when a GOI list was supplied).
            if (self.project_root / "results" / "genes_of_interest" / "goi_deseq2_results.csv").exists():
                items.append("results/genes_of_interest/goi_deseq2_results.csv")
            # Multi-study meta-analysis tables (present only after a multi-dataset run).
            for _mt in ("meta_convergent_genes.csv", "meta_study_summary.csv",
                        "meta_analysis_results.csv", "meta_enrichment_ora.csv"):
                if (self.project_root / "results" / "meta" / _mt).exists():
                    items.append(f"results/meta/{_mt}")
            # Per-study tables, namespaced by study id (e.g. "PRJNA123 / volcano").
            # Gated on the manifest so single-study runs are unaffected.
            manifest = self.project_root / "results" / "meta" / "per_study" / "manifest.json"
            if manifest.exists():
                per_study = self.project_root / "results" / "meta" / "per_study"
                # tables/ plus the opt-in enrichment/ go_ora_*.csv (same <study>/<sub>/<file> layout).
                for sub in ("tables", "enrichment"):
                    for p in sorted(per_study.glob(f"*/{sub}/*.csv")):
                        study = p.parent.parent.name
                        rel = p.relative_to(self.project_root).as_posix()
                        extra.append((f"{study} / {p.stem}", rel))
        current = self.output_table_pick.currentText()
        self.output_table_pick.blockSignals(True)
        self.output_table_pick.clear()
        self.output_table_pick.addItems(items)
        for display, rel in extra:
            self.output_table_pick.addItem(display, rel)
        idx = self.output_table_pick.findText(current)
        self.output_table_pick.setCurrentIndex(idx if idx >= 0 else 0)
        self.output_table_pick.blockSignals(False)

    def _update_enrichment_warning(self) -> None:
        if not hasattr(self, "enrichment_warn"):
            return
        enr = self.config.enrichment if self.config is not None else None
        show = (self.enrichment.isChecked() and enr is not None
                and not enr.kegg_organism and not enr.orgdb)
        self.enrichment_warn.setVisible(show)

    def _update_organism_label(self) -> None:
        if not hasattr(self, "current_organism_label"):
            return
        name = self.config.reference.organism_name if self.config is not None else None
        self.current_organism_label.setText(f"Selected organism: {name or '— none —'}")

    def _enrichment_config_messages(self) -> list[dict[str, str]]:
        # Surface the silent count-matrix/microarray enrichment trap: enrichment is
        # enabled but no organism id is set, so GO/KEGG/PPI would be skipped.
        if self.config is None or not self.config.workflow.enrichment:
            return []
        enr = self.config.enrichment
        if not enr.kegg_organism and not enr.orgdb:
            return [{"status": "REVIEW_REQUIRED",
                     "message": "Enrichment is enabled but no organism is configured "
                                "(no KEGG code or OrgDb). GO/KEGG enrichment and the STRING "
                                "PPI network will be skipped. Select your organism on the "
                                "Reference Manager tab, or disable Enrichment."}]
        return []

    def _confirm_enrichment_config(self) -> bool:
        # Enrichment on with no organism id silently produces nothing; confirm rather
        # than let the user discover the empty result only after the run finishes.
        if self.config is None or not self.config.workflow.enrichment:
            return True
        enr = self.config.enrichment
        if enr.kegg_organism or enr.orgdb:
            return True
        reply = QMessageBox.question(
            self, APP_NAME,
            "Enrichment is enabled but no organism is configured, so GO/KEGG enrichment "
            "and the STRING PPI network will be skipped.\n\nSelect your organism on the "
            "Reference Manager tab first, or continue without enrichment?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    def _require_project(self) -> bool:
        if self.project_root is None:
            QMessageBox.warning(self, APP_NAME, "Create or open a project first.")
            return False
        return True

    @staticmethod
    def _format_messages(messages: list[dict[str, str]]) -> str:
        return "\n".join(f"{m.get('status')}: {m.get('message')}" for m in messages)

    @staticmethod
    def _format_workdir_messages(messages: list[dict[str, str]]) -> str:
        # Surface FAIL/WARNING/REVIEW_REQUIRED (incl. the /mnt/c WSL note) above
        # PASS lines so the user sees actionable guidance first.
        order = {"FAIL": 0, "REVIEW_REQUIRED": 1, "WARNING": 2, "PASS": 3}
        ordered = sorted(messages, key=lambda m: order.get(m.get("status", ""), 4))
        return "\n".join(f"{m.get('status')}: {m.get('message')}" for m in ordered)
