from __future__ import annotations

import shutil
import os
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml
from PySide6.QtCore import QByteArray, QSettings, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

from app.constants import APP_NAME, MIN_UNIQUE_MAPPED_WARN_PCT
from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.config_models import AppConfig
from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import dataframe_from_rows, load_metadata, save_metadata, validate_metadata
from app.core.project import ProjectManager, validate_working_directory
from app.core.provenance import write_run_summary
from app.core.reference_manager import catalog_entry_for_organism, load_reference_catalog, md5sum, validate_reference
from app.core.resources import detect_system, recommend_profile
from app.core.sra_metadata import fetch_ena_metadata, metadata_to_samples
from app.core.geo_metadata import fetch_geo_series
from app.core.runtime_estimator import estimate_runtime
from app.core.sanity_checks import write_check
from app.core.snakemake_runner import (
    SnakemakeRunner,
    _new_run_tag,
    build_snakemake_command,
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
        self._mapping_checked: set[str] = set()
        self._mapping_halt_decided = False
        self._closing = False
        self.run_action_buttons: dict[str, QPushButton] = {}
        self.stop_button: QPushButton | None = None

        self.tabs = QTabWidget()
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
        if shutil.which("wsl") is None:
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
        workdir_row = QHBoxLayout()
        workdir_row.addWidget(self.workdir)
        workdir_row.addWidget(browse)
        workdir_row.addWidget(wsl_fs)
        workdir_hint = QLabel(
            "Recommended for WSL2: keep the project on the Linux filesystem "
            "(\\\\wsl.localhost\\...). A Windows folder (C:\\...) also works but is slower for "
            "large genomics files."
        )
        workdir_hint.setWordWrap(True)
        create = QPushButton("New Project")
        create.setProperty("primary", True)
        create.clicked.connect(self._create_project)
        benchmark = QPushButton("Create Benchmark Project")
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
        layout.addWidget(QLabel("Fetched from the ENA Portal API: layout, FASTQ URLs, read counts. Condition is set to 'unknown' for you to edit in the Metadata tab."))
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
        geo_btn = QPushButton("Fetch a GEO microarray series (GSE)")
        geo_btn.setToolTip("Load a GEO/GSE microarray dataset. The pipeline ingests the normalized "
                           "intensities (GEOquery/affy), runs limma differential expression, then the "
                           "same figures and enrichment. RNA-seq GSEs are redirected to the SRA box.")
        geo_btn.clicked.connect(self._fetch_geo_series)
        geo_row.addWidget(QLabel("Microarray?"))
        geo_row.addWidget(self.gse_box)
        geo_row.addWidget(geo_btn)
        geo_row.addStretch(1)
        layout.addLayout(geo_row)
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
        self.config.microarray.source = "geo_series_matrix"
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
        if not genome.exists() or not annotation.exists():
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

    def _build_workflow_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.aligner = QComboBox()
        self.aligner.addItems(["STAR", "HISAT2", "Salmon"])
        # Only STAR is implemented; show HISAT2/Salmon but make them unselectable so
        # a run can't be started on an aligner that dead-ends mid-pipeline.
        self._disable_combo_items(self.aligner, {"HISAT2", "Salmon"}, " (not yet available)")
        self.quantifier = QComboBox()
        self.quantifier.addItems(["featureCounts", "STAR_GeneCounts", "Salmon_tximport"])
        # Only featureCounts is wired (no rule reads config.workflow.quantifier), so
        # disable the others rather than letting them silently no-op.
        self._disable_combo_items(self.quantifier, {"STAR_GeneCounts", "Salmon_tximport"}, " (not yet available)")
        self.trim = QCheckBox()
        self.trim.setChecked(True)
        self.rrna = QCheckBox()
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
        save = QPushButton("Save Workflow Settings")
        save.setProperty("primary", True)
        save.clicked.connect(self._save_workflow_settings)

        # Group the 14 settings into three labelled cards (Alignment & read
        # processing / Differential expression / Outputs) so the tab reads as
        # sections rather than one flat field list.
        align_group = QGroupBox("Alignment and read processing")
        align_form = QFormLayout(align_group)
        align_form.addRow(self._info_label("Aligner", "Read aligner. STAR is the fully implemented route; HISAT2/Salmon are scaffolded."), self.aligner)
        align_form.addRow(self._info_label("Quantifier", "How aligned reads are summarised to gene counts. featureCounts is the implemented route."), self.quantifier)
        align_form.addRow("fastp trimming", self.trim)
        align_form.addRow(self._info_label("fastp quality (-q)", "Minimum acceptable per-base Phred quality. Bases below this count as low quality. fastp default 15."), self.fastp_q)
        align_form.addRow(self._info_label("fastp min length (-l)", "Reads shorter than this (after trimming) are discarded. Protocol default 36."), self.fastp_len)
        align_form.addRow(self._info_label("fastp poly-G (-g)", "Trim poly-G tails, an artefact of 2-colour chemistry (NextSeq/NovaSeq). Leave off for HiSeq/MiSeq."), self.trim_poly_g)
        align_form.addRow("rRNA filtering", self.rrna)
        layout.addWidget(align_group)

        de_group = QGroupBox("Differential expression")
        de_form = QFormLayout(de_group)
        de_form.addRow(self._info_label("DESeq2 design", "R model formula. The last term is the effect of interest; put known batch effects before it, e.g. '~ batch + condition'."), self.design)
        de_form.addRow(refresh)
        de_form.addRow(self._info_label("Contrast factor", "The metadata column compared in the differential test (usually 'condition')."), self.contrast_factor)
        de_form.addRow(self._info_label("Numerator (treated)", "The group whose change is measured. log2 fold change is numerator relative to denominator."), self.numerator)
        de_form.addRow(self._info_label("Denominator (reference)", "The baseline group. Positive log2 fold change = higher in the numerator than this."), self.denominator)
        de_form.addRow(self._info_label("Reference level", "The factor's baseline level (normally the same as the denominator); DESeq2 releveled to this."), self.reference_level)
        de_form.addRow("", self.contrast_info)
        de_form.addRow(self._info_label("Alpha (padj/FDR)", "Significance threshold on the Benjamini-Hochberg adjusted p-value (false discovery rate). Default 0.05."), self.alpha)
        de_form.addRow(self._info_label("log2FC threshold", "Minimum absolute log2 fold change for a gene to count as up/down-regulated. |log2FC| >= this AND padj < alpha. Default 1.0 (a 2-fold change)."), self.lfc_threshold)
        de_form.addRow(QLabel("featureCounts strandedness is auto-inferred per protocol."))
        layout.addWidget(de_group)

        out_group = QGroupBox("Outputs")
        out_form = QFormLayout(out_group)
        out_form.addRow("Enrichment", self.enrichment)
        out_form.addRow("", self.enrichment_warn)
        out_form.addRow("Figures", self.figures)
        layout.addWidget(out_group)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        layout.addStretch(1)
        self.tabs.addTab(self._scrollable(page), "Workflow Settings")

    def _refresh_conditions(self) -> None:
        df = self.metadata_table.to_dataframe()
        if "condition" not in df.columns:
            return
        values = sorted({str(v) for v in df["condition"].tolist() if str(v) and str(v) != "unknown"})
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
        save.clicked.connect(self._save_resources)
        manual_form.addRow(save)
        layout.addWidget(manual_group)

        layout.addStretch(1)
        self.tabs.addTab(self._scrollable(page), "Resources")

    def _build_runtime_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        estimate = QPushButton("Estimate Runtime")
        estimate.setProperty("primary", True)
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
        run.clicked.connect(self._run_sanity_checks)
        refresh = QPushButton("Refresh Phase Checks")
        refresh.clicked.connect(self._refresh_phase_checks)
        buttons.addWidget(run)
        buttons.addWidget(refresh)
        self.approve_review = QCheckBox("I have reviewed and approve REVIEW_REQUIRED items")
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
        self.use_wsl.setChecked(True)
        buttons.addWidget(self.use_wsl)
        actions = QHBoxLayout()
        stop = QPushButton("Stop")
        stop.setEnabled(False)
        stop.clicked.connect(self._stop_run)
        self.stop_button = stop
        open_folder = QPushButton("Open Project Folder")
        open_folder.clicked.connect(self._open_folder)
        open_report = QPushButton("Open MultiQC Report")
        open_report.clicked.connect(self._open_report)
        for w in (stop, open_folder, open_report):
            actions.addWidget(w)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.elapsed_label = QLabel("Elapsed: 00:00:00")
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)
        self._run_start = 0.0
        self.command_text = QLineEdit()
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
        ("repair", "Repairing read pairs"),
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
        if code == 0:
            self.progress.setValue(100)
            self.progress.setStyleSheet("")
            self._set_run_status("Completed", "#2E7D32")
            self.phase_label.setText("Finished")
            # A completed run / "Regenerate figures" writes new PNGs into
            # results/figures; re-scan so the Outputs figure picker shows them
            # without the user having to click "Refresh figures" first. A PPI-only
            # rebuild or a dry-run/unlock writes no such figures, so skip those.
            if (self.project_root is not None and hasattr(self, "figure_pick")
                    and was_mode in ("run", "resume", "recover", "figures", "goi")):
                self._refresh_gallery()
            if was_mode in ("run", "resume", "recover"):
                self.statusBar().showMessage(
                    "Run complete. View figures and tables on the Outputs tab, and the "
                    "interactive network on the PPI Network tab.", 20000)
            # A "Rebuild from STRING" produces a new network; reload it into the
            # interactive viewer so it reflects the rebuild instead of the old graph.
            if was_mode == "ppi" and self.project_root is not None:
                self._load_ppi_network()
        else:
            # Non-zero: do not imply success. Red bar, red status, keep partial %.
            self.progress.setStyleSheet("QProgressBar::chunk { background-color: #C0392B; }")
            self._set_run_status(f"Failed (exit code {code})", "#C0392B")
            self.phase_label.setText("")
        self.log_text.append(f"Process finished with exit code {code}")

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

        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        results_splitter.setChildrenCollapsible(False)
        results_splitter.setHandleWidth(6)
        results_splitter.addWidget(figure_panel)
        results_splitter.addWidget(control_panel)
        results_splitter.setStretchFactor(0, 3)
        results_splitter.setStretchFactor(1, 1)
        results_splitter.setSizes([640, 340])
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
        self.ppi_size_pick = QComboBox()
        for label, val in [("Node degree", "degree"), ("Mean expression", "meanExpr"),
                           ("−log₁₀ adj. p", "neglog10padj")]:
            self.ppi_size_pick.addItem(label, val)
        self.ppi_size_pick.currentIndexChanged.connect(
            lambda _i: self.ppi_viewer.set_size_by(self.ppi_size_pick.currentData()))
        self.ppi_labels_cb = QCheckBox("Labels")
        self.ppi_labels_cb.setChecked(True)
        self.ppi_labels_cb.toggled.connect(lambda on: self.ppi_viewer.set_labels(on))
        row1.addWidget(load_btn)
        row1.addWidget(QLabel("Layout:"))
        row1.addWidget(self.ppi_layout_pick)
        row1.addWidget(QLabel("Colour:"))
        row1.addWidget(self.ppi_color_pick)
        row1.addWidget(QLabel("Size:"))
        row1.addWidget(self.ppi_size_pick)
        row1.addWidget(self.ppi_labels_cb)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Confidence ≥"))
        self.ppi_conf = QSlider(Qt.Orientation.Horizontal)
        self.ppi_conf.setRange(0, 100)
        self.ppi_conf.setValue(0)
        self.ppi_conf.setMaximumWidth(180)
        self.ppi_conf.setToolTip("Hide interactions below this STRING confidence (combined score). "
                                 "The minimum is the build threshold; lowering it further requires "
                                 "rebuilding from STRING with a lower score threshold.")
        self.ppi_conf.valueChanged.connect(self._ppi_confidence_changed)
        self.ppi_conf_lbl = QLabel("0.00")
        rebuild_btn = QPushButton("Rebuild from STRING…")
        rebuild_btn.setToolTip("Re-run the STRING network with the current score threshold "
                               "(Outputs → Figure Style). Re-contacts string-db.org.")
        rebuild_btn.clicked.connect(self._regenerate_ppi)
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
        row2.addWidget(self.ppi_conf)
        row2.addWidget(self.ppi_conf_lbl)
        row2.addStretch(1)
        row2.addWidget(rebuild_btn)
        row2.addWidget(QLabel("Export bg:"))
        row2.addWidget(self.ppi_export_bg)
        row2.addWidget(export_png)
        row2.addWidget(export_svg)
        row2.addWidget(save_cyto)
        layout.addLayout(row2)

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
        help_label = QLabel("Paste gene IDs (one per line) matching the count matrix (e.g. FBgn..., RefSeq locus tags, or symbols present in the GTF). On the next run, a focused z-scored heatmap and per-condition expression plots are produced.")
        help_label.setWordWrap(True)  # without this the long label forces a huge min width
        v.addWidget(help_label)
        self.goi_box = QTextEdit()
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
        self.config.ppi.score_threshold = int(self.ppi_score.value())
        self.config.ppi.hub_label_count = int(self.ppi_hub_labels.value())
        self.manager.save_config(self.project_root, self.config)
        self._start_snakemake("ppi")

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
        self.fig_palette.addItems(["Blue-Red", "Viridis", "Magma", "Plasma", "Cividis",
                                   "Spectral", "Red-Yellow-Blue", "Greyscale"])
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
        self.fig_title_bold = QCheckBox("Bold axis titles")
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
        self.fig_volcano_ycap = QDoubleSpinBox()
        self.fig_volcano_ycap.setRange(0.0, 400.0)
        self.fig_volcano_ycap.setSingleStep(5.0)
        self.fig_volcano_ycap.setDecimals(1)
        self.fig_volcano_ycap.setValue(0.0)
        self.fig_volcano_ycap.setSpecialValueText("auto")  # 0 = auto (quantile)
        self.fig_volcano_alpha = QDoubleSpinBox()
        self.fig_volcano_alpha.setRange(0.05, 1.0)
        self.fig_volcano_alpha.setSingleStep(0.05)
        self.fig_volcano_alpha.setDecimals(2)
        self.fig_volcano_alpha.setValue(0.55)
        self.fig_pca_fixed_aspect = QCheckBox("Fix PCA aspect ratio")
        self.fig_pca_fixed_aspect.setChecked(False)
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
        form.addRow(self._info_label("Point size", "Dot size in PCA/volcano scatter plots (ggplot2 size units)."), self.fig_point_size)
        form.addRow(self._info_label("Base font size", "Base text size for all figures (ggplot2 theme base_size, points)."), self.fig_base_font)
        form.addRow(self._info_label("Font family", "Font for figure text. Leave as default unless the font is also available in the WSL R environment."), self.fig_font_family)
        form.addRow(self.fig_label_bold)
        form.addRow(self.fig_title_bold)
        form.addRow(self._info_label("Volcano top-N labels", "How many of the most significant genes to label on the volcano plot. 0 = none."), self.fig_volcano_top)
        form.addRow(self._info_label("Heatmap top-N genes", "Number of top genes (by adjusted p) shown in the top-DEG heatmap."), self.fig_heatmap_top)
        form.addRow(self._info_label("PCA n-top genes", "Number of most-variable genes used to compute the PCA. Protocol default 500."), self.fig_pca_ntop)
        form.addRow(self._info_label("Size units", "Units for the width/height below. Pixels (px) are converted using the DPI."), self.fig_dim_unit)
        form.addRow(self._info_label("Width", "Saved figure width (PNG and SVG), in the units selected above."), self.fig_width)
        form.addRow(self._info_label("Height", "Saved figure height (PNG and SVG), in the units selected above."), self.fig_height)
        form.addRow(self._info_label("DPI (PNG)", "Resolution for the raster PNG export. SVG is vector and unaffected. 300 is publication quality. Also converts px width/height to inches."), self.fig_dpi)
        form.addRow(self._info_label("Volcano y cap", "Upper limit for the volcano -log10(adjusted p) axis. 'auto' (0) caps at the 99.5th percentile so a few hyper-significant genes do not squash the rest."), self.fig_volcano_ycap)
        form.addRow(self._info_label("Volcano point alpha", "Opacity of the significant points in the volcano plot (0-1). Lower values reveal density in the dense core."), self.fig_volcano_alpha)
        form.addRow(self.fig_pca_fixed_aspect)
        form.addRow(self._info_label("Heatmap z limit", "Symmetric cap on the row z-scores in the top-DEG heatmap; values beyond +/- this map to the extreme colours."), self.fig_heatmap_zlim)
        form.addRow(self._info_label("Enrichment categories shown", "Number of terms shown in the enrichment dot/ridge/KEGG plots."), self.fig_enrich_show)
        form.addRow(self._info_label("PPI layout", "Graph layout algorithm for the PPI network figure (graphlayouts). 'fr' (Fruchterman-Reingold) is force-directed and the default; 'stress' is a compact alternative."), self.fig_ppi_layout)
        form.addRow(save_style)
        # --- PPI network (STRING) controls: customise + regenerate in-app ---
        self.ppi_score = QSpinBox()
        self.ppi_score.setRange(0, 1000)
        self.ppi_score.setValue(400)
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
        style.point_size = self.fig_point_size.value()
        style.base_font_size = self.fig_base_font.value()
        font = self.fig_font_family.currentText().strip()
        style.font_family = "" if font == self.FONT_DEFAULT_LABEL else font
        style.label_bold = self.fig_label_bold.isChecked()
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
        style.volcano_y_cap = self.fig_volcano_ycap.value()
        style.volcano_point_alpha = self.fig_volcano_alpha.value()
        style.pca_fixed_aspect = self.fig_pca_fixed_aspect.isChecked()
        style.heatmap_zlim = self.fig_heatmap_zlim.value()
        style.enrich_show_category = self.fig_enrich_show.value()
        style.ppi_layout = self.fig_ppi_layout.currentText().strip() or "fr"
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
        path = self.project_root / self.output_table_pick.currentText()
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
        self.output_table.setColumnCount(len(df.columns))
        self.output_table.setHorizontalHeaderLabels([str(c) for c in df.columns])
        self.output_table.setRowCount(len(df))
        for r in range(len(df)):
            for c in range(len(df.columns)):
                self.output_table.setItem(r, c, QTableWidgetItem(str(df.iat[r, c])))

    def _refresh_gallery(self) -> None:
        prev = self.figure_pick.currentText()
        self.figure_pick.blockSignals(True)
        self.figure_pick.clear()
        figures = []
        if self.project_root is not None:
            figures = sorted((self.project_root / "results" / "figures").glob("*.png"))
        if figures:
            self.figure_pick.setEnabled(True)
            self.figure_pick.addItems([f.name for f in figures])
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
        path = self.project_root / "results" / "figures" / name
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
            f"Accessions: {', '.join(sample['original_accession'] for sample in benchmark['samples'])}\n"
            + self._format_workdir_messages(messages)
        )

    def _open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open project")
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
        # Drop the previous project's transient state (log, status, figures,
        # network) before showing the new one.
        self._clear_transient_ui()
        self._populate_widgets_from_config()
        samples = root / "config" / "samples.tsv"
        if samples.exists():
            self.metadata_table.load_tsv(samples)
        self._refresh_gallery()
        self._remember_recent_project(root)
        self.project_status.setPlainText(f"Open project: {root}")

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
        self.quantifier.setCurrentText(wf.quantifier)
        self.trim.setChecked(wf.trimming)
        self.rrna.setChecked(wf.rrna_filtering)
        self.enrichment.setChecked(wf.enrichment)
        self.figures.setChecked(wf.figures)
        self.fastp_q.setValue(self.config.fastp.qualified_quality_phred)
        self.fastp_len.setValue(self.config.fastp.length_required)
        self.trim_poly_g.setChecked(self.config.fastp.trim_poly_g)
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
        self.fig_point_size.setValue(fig.point_size)
        self.fig_base_font.setValue(fig.base_font_size)
        self.fig_font_family.setCurrentText(fig.font_family or self.FONT_DEFAULT_LABEL)
        self.fig_label_bold.setChecked(fig.label_bold)
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
        self.fig_volcano_ycap.setValue(fig.volcano_y_cap)
        self.fig_volcano_alpha.setValue(fig.volcano_point_alpha)
        self.fig_pca_fixed_aspect.setChecked(fig.pca_fixed_aspect)
        self.fig_heatmap_zlim.setValue(fig.heatmap_zlim)
        self.fig_enrich_show.setValue(fig.enrich_show_category)
        self.fig_ppi_layout.setCurrentText(fig.ppi_layout or "fr")
        self.ppi_score.setValue(self.config.ppi.score_threshold)
        self.ppi_hub_labels.setValue(self.config.ppi.hub_label_count)
        goi_path = self.config.gene_sets.custom_gene_list
        if goi_path and self.project_root is not None and (self.project_root / goi_path).exists():
            self.goi_box.setPlainText((self.project_root / goi_path).read_text(encoding="utf-8").strip())
        else:
            self.goi_box.clear()
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
        formula = self.config.deseq2.design_formula.split("~", 1)[-1]
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
        if p.suffix.lower() == ".xlsx":
            df = pd.read_excel(p, dtype=str).fillna("")
        else:
            df = pd.read_csv(p, sep="\t" if p.suffix.lower() == ".tsv" else ",", dtype=str).fillna("")
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
            self.metadata_table.add_column(name.strip())

    def _rename_column(self) -> None:
        col = self.metadata_table.currentColumn()
        if col < 0:
            return
        current = self.metadata_table.column_names()[col]
        name, ok = QInputDialog.getText(self, APP_NAME, "Rename column:", text=current)
        if ok and name.strip():
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

    def _validate_metadata(self) -> None:
        allow_pending_sra = self.config is not None and self.config.input.type in (
            "sra", "count_matrix", "microarray", "deseq2_results")
        messages = validate_metadata(
            self.metadata_table.to_dataframe(),
            allow_pending_sra=allow_pending_sra,
            design_variables=self._design_variables(),
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

    def _save_workflow_settings(self, validate: bool = True) -> bool:
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
        self.config.workflow.rrna_filtering = self.rrna.isChecked()
        self.config.workflow.enrichment = self.enrichment.isChecked()
        self.config.workflow.figures = self.figures.isChecked()
        self.config.fastp.qualified_quality_phred = self.fastp_q.value()
        self.config.fastp.length_required = self.fastp_len.value()
        self.config.fastp.trim_poly_g = self.trim_poly_g.isChecked()
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
        self.runtime_busy.setVisible(True)
        QApplication.processEvents()
        try:
            df = self.metadata_table.to_dataframe()
            estimate = estimate_runtime(self.config, df)
            self.runtime_text.setPlainText("\n".join(f"{k}: {v}" for k, v in estimate.items()))
        finally:
            self.runtime_busy.setVisible(False)

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
            # Single-end FASTQ is not yet supported on the alignment route; block
            # early instead of letting the run dead-end at the trim step.
            if not no_reference_mode and self.project_root is not None:
                samples_path = self.project_root / "config" / "samples.tsv"
                if samples_path.exists():
                    try:
                        sdf = pd.read_csv(samples_path, sep="\t", dtype=str).fillna("")
                        se = (
                            sdf.loc[sdf["layout"].str.lower() == "single", "sample_id"].tolist()
                            if "layout" in sdf.columns else []
                        )
                    except Exception:
                        se = []
                    if se:
                        QMessageBox.warning(
                            self, APP_NAME,
                            "Single-end FASTQ input is not yet supported (paired-end only).\n\n"
                            f"Single-end sample(s): {', '.join(se)}.\n"
                            "Provide paired-end reads, or use the count-matrix or GEO "
                            "microarray input mode.",
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
            self._set_running_ui(False)
            QMessageBox.critical(self, APP_NAME, f"Failed to start the run:\n\n{exc}")

    def _start_snakemake_impl(self, mode: str) -> None:
        if self.config is None or self.project_root is None:
            return
        # Guard double-starts: one snakemake per directory at a time.
        if self._run_active or (self.runner is not None and self.runner.is_running()):
            self.log_text.append("A run is already active. Stop it before starting another.")
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
            return
        # Backstop the enrichment trap directly at run start (the sanity-check gate
        # only fires if the user ran checks first). Not for recovery.
        if mode in ("run", "resume") and not self._confirm_enrichment_config():
            return
        # Persist the in-memory metadata table so the run uses current edits;
        # Snakemake reads config/samples.tsv from disk, not the GUI table.
        save_metadata(self.metadata_table.to_dataframe(), self.project_root / "config" / "samples.tsv")
        # Validate the contrast only for the differential-expression modes; unlock,
        # dry-run and the figure/ppi/goi regenerations reuse existing DE results.
        if not self._save_workflow_settings(validate=mode in ("run", "resume", "recover")):
            return  # invalid contrast; the user was warned
        self._save_resources()
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
            return
        if self.use_wsl.isChecked() and shutil.which("wsl") is None:
            self.log_text.append("WSL is not available on PATH. Command was constructed but not started.")
            self.log_text.append(command.display)
            return
        import time

        self.runner = SnakemakeRunner(self.project_root, command)
        self.runner_thread = RunnerThread(self.runner)
        self.runner_thread.line.connect(self._on_run_line)
        self.runner_thread.finished_with_code.connect(self._on_run_finished)
        self._run_mode = mode
        self._recovery_offered = False
        self._mapping_checked = set()
        self._mapping_halt_decided = False
        self._saw_star_align = False
        self._stop_in_progress = False
        self._set_running_ui(True)
        if mode in ("run", "resume", "recover", "figures", "goi", "ppi"):
            # Sub-runs launched from the Outputs / PPI tabs report progress here, so
            # bring the Run Monitor forward — otherwise the click looks like a no-op.
            if hasattr(self, "run_monitor_page"):
                self.tabs.setCurrentWidget(self.run_monitor_page)
            self.progress.setValue(0)
            self.progress.setStyleSheet("")
            status = {"figures": "Regenerating figures...",
                      "goi": "Generating genes-of-interest outputs...",
                      "ppi": "Rebuilding PPI network..."}.get(mode, "Running...")
            self._set_run_status(status, "#2C6FB6")
            self.phase_label.setText("Current step: starting...")
            self._run_start = time.monotonic()
            # Wall-clock start for the timing report (only for an actual pipeline
            # run, not a figures/GOI regeneration).
            if mode in ("run", "resume", "recover"):
                self._run_start_wall = datetime.now().isoformat(timespec="seconds")
                self._run_finish_wall = None
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
            estimate = estimate_runtime(cfg, df) if cfg else None
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
                     "results/deseq2/normalized_counts.csv",
                     "results/deseq2/unchanged_genes.csv"]
            if itype in ("sra", "fastq"):
                items.insert(0, "results/counts/counts.txt")
            items += ["results/enrichment/kegg_ora.csv", "results/enrichment/kegg_gsea.csv",
                      "results/stats/wilcoxon_results.csv", "results/stats/set_overlap.csv",
                      "results/networks/enrichment_emap_nodes.csv",
                      "results/networks/enrichment_genemap_nodes.csv",
                      "results/networks/string_ppi_nodes.csv", "results/networks/ppi_hub_genes.csv"]
        current = self.output_table_pick.currentText()
        self.output_table_pick.blockSignals(True)
        self.output_table_pick.clear()
        self.output_table_pick.addItems(items)
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
