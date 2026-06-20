from __future__ import annotations

import shutil
import os
import re
from datetime import date
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
from PySide6.QtGui import QDesktopServices, QFontDatabase, QPixmap
from PySide6.QtCore import Qt, QUrl

from app.constants import APP_NAME
from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.config_models import AppConfig
from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import dataframe_from_rows, load_metadata, save_metadata, validate_metadata
from app.core.project import ProjectManager, validate_working_directory
from app.core.provenance import write_run_summary
from app.core.reference_manager import load_reference_catalog, md5sum, validate_reference
from app.core.resources import detect_system, recommend_profile
from app.core.sra_metadata import fetch_ena_metadata, metadata_to_samples
from app.core.runtime_estimator import estimate_runtime
from app.core.sanity_checks import write_check
from app.core.snakemake_runner import (
    SnakemakeRunner,
    _new_run_tag,
    build_snakemake_command,
)
from app.core.timing import write_timing_summary
from app.core.paths import data_path
from app.ui.image_viewer import SVG_AVAILABLE, ImageViewer
from app.ui.metadata_editor import MetadataTable
from app.ui.readiness_dialog import ReadinessDialog
from app.ui.theme import IMAGEVIEWER_BG, apply_theme


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
        self.run_action_buttons: dict[str, QPushButton] = {}
        self.stop_button: QPushButton | None = None

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        # The window owns its minimum so the size contract holds even under direct
        # construction (tests), and the restore-geometry size guard has a real bound.
        self.setMinimumSize(900, 640)
        # Light/dark mode toggle: a labelled button in the top-right corner.
        self.theme_toggle = QPushButton()
        self.theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_toggle.setMinimumWidth(110)  # stable width across Dark/Light label swap
        self.theme_toggle.setFlat(False)
        self.theme_toggle.clicked.connect(self._toggle_theme)
        self._sync_theme_toggle(str(QSettings().value("theme_mode", "light")))
        self.tabs.setCornerWidget(self.theme_toggle, Qt.Corner.TopRightCorner)
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
        self._save_geometry_state()
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
        self.workdir = QLineEdit(str(Path.home() / "BulkSeqProjects"))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_workdir)
        workdir_row = QHBoxLayout()
        workdir_row.addWidget(self.workdir)
        workdir_row.addWidget(browse)
        create = QPushButton("New Project")
        create.clicked.connect(self._create_project)
        benchmark = QPushButton("Create Benchmark Project")
        benchmark.clicked.connect(self._create_benchmark_project)
        open_existing = QPushButton("Open Existing Project")
        open_existing.clicked.connect(self._open_project)
        readiness = QPushButton("Check Environment")
        readiness.clicked.connect(self.show_readiness_dialog)
        self.project_status = QTextEdit()
        self.project_status.setReadOnly(True)
        layout.addRow("Project name", self.project_name)
        layout.addRow("Working directory", workdir_row)
        layout.addRow(create, open_existing)
        layout.addRow(benchmark, readiness)
        layout.addRow("Status", self.project_status)
        self.tabs.addTab(self._scrollable(page), "Project")

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
        layout.addWidget(QLabel("SRA / ENA accessions"))
        layout.addWidget(self.sra_box)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Fetched from the ENA Portal API: layout, FASTQ URLs, read counts. Condition is set to 'unknown' for you to edit in the Metadata tab."))
        layout.addWidget(self.input_preview)
        self.tabs.addTab(self._scrollable(page), "Input Data")

    def _fetch_sra_metadata(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        accessions = [line.strip() for line in self.sra_box.toPlainText().splitlines() if line.strip()]
        if not accessions:
            QMessageBox.warning(self, APP_NAME, "Paste at least one accession first.")
            return
        self.input_preview.setPlainText("Querying ENA…")
        QApplication.processEvents()
        try:
            meta = fetch_ena_metadata(accessions)
        except Exception as exc:  # network/parse errors
            QMessageBox.warning(self, APP_NAME, f"ENA query failed: {exc}")
            return
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
            ]),
        ]
        top_row = QHBoxLayout()
        for title, specs in groups:
            box = QGroupBox(title)
            box_layout = QHBoxLayout(box)
            for text, slot in specs:
                btn = QPushButton(text)
                btn.clicked.connect(slot)
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
        choose.clicked.connect(self._select_reference)
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
        self.config.reference.genome_fasta = str(genome)
        self.config.reference.annotation_file = str(annotation)
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
        layout = QFormLayout(page)
        self.aligner = QComboBox()
        self.aligner.addItems(["STAR", "HISAT2", "Salmon"])
        self.quantifier = QComboBox()
        self.quantifier.addItems(["featureCounts", "STAR_GeneCounts", "Salmon_tximport"])
        self.trim = QCheckBox()
        self.trim.setChecked(True)
        self.rrna = QCheckBox()
        self.enrichment = QCheckBox()
        self.enrichment.setChecked(True)
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
        save.clicked.connect(self._save_workflow_settings)
        layout.addRow(self._info_label("Aligner", "Read aligner. STAR is the fully implemented route; HISAT2/Salmon are scaffolded."), self.aligner)
        layout.addRow(self._info_label("Quantifier", "How aligned reads are summarised to gene counts. featureCounts is the implemented route."), self.quantifier)
        layout.addRow("fastp trimming", self.trim)
        layout.addRow(self._info_label("fastp quality (-q)", "Minimum acceptable per-base Phred quality. Bases below this count as low quality. fastp default 15."), self.fastp_q)
        layout.addRow(self._info_label("fastp min length (-l)", "Reads shorter than this (after trimming) are discarded. Protocol default 36."), self.fastp_len)
        layout.addRow(self._info_label("fastp poly-G (-g)", "Trim poly-G tails, an artefact of 2-colour chemistry (NextSeq/NovaSeq). Leave off for HiSeq/MiSeq."), self.trim_poly_g)
        layout.addRow("rRNA filtering", self.rrna)
        layout.addRow("Enrichment", self.enrichment)
        layout.addRow("Figures", self.figures)
        layout.addRow(self._info_label("DESeq2 design", "R model formula. The last term is the effect of interest; put known batch effects before it, e.g. '~ batch + condition'."), self.design)
        layout.addRow(refresh)
        layout.addRow(self._info_label("Contrast factor", "The metadata column compared in the differential test (usually 'condition')."), self.contrast_factor)
        layout.addRow(self._info_label("Numerator (treated)", "The group whose change is measured. log2 fold change is numerator relative to denominator."), self.numerator)
        layout.addRow(self._info_label("Denominator (reference)", "The baseline group. Positive log2 fold change = higher in the numerator than this."), self.denominator)
        layout.addRow(self._info_label("Reference level", "The factor's baseline level (normally the same as the denominator); DESeq2 releveled to this."), self.reference_level)
        layout.addRow(self._info_label("Alpha (padj/FDR)", "Significance threshold on the Benjamini-Hochberg adjusted p-value (false discovery rate). Default 0.05."), self.alpha)
        layout.addRow(self._info_label("log2FC threshold", "Minimum absolute log2 fold change for a gene to count as up/down-regulated. |log2FC| >= this AND padj < alpha. Default 1.0 (a 2-fold change)."), self.lfc_threshold)
        layout.addRow(QLabel("featureCounts strandedness is auto-inferred per protocol."))
        layout.addRow(save)
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
        save.setProperty("primary", True)
        save.clicked.connect(self._save_resources)
        manual_form.addRow(save)
        layout.addWidget(manual_group)

        layout.addStretch(1)
        self.tabs.addTab(self._scrollable(page), "Resources")

    def _build_runtime_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        estimate = QPushButton("Estimate Runtime")
        estimate.clicked.connect(self._estimate_runtime)
        self.runtime_busy = self._busy_bar()
        self.runtime_text = QTextEdit()
        self.runtime_text.setReadOnly(True)
        layout.addWidget(estimate)
        layout.addWidget(self.runtime_busy)
        layout.addWidget(self.runtime_text)
        self.tabs.addTab(self._scrollable(page), "Runtime Estimate")

    def _build_sanity_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        run = QPushButton("Run Project and Metadata Checks")
        run.clicked.connect(self._run_sanity_checks)
        refresh = QPushButton("Refresh Phase Checks")
        refresh.clicked.connect(self._refresh_phase_checks)
        buttons.addWidget(run)
        buttons.addWidget(refresh)
        self.approve_review = QCheckBox("I have reviewed and approve REVIEW_REQUIRED items")
        self.sanity_busy = self._busy_bar()
        self.sanity_text = QTextEdit()
        self.sanity_text.setReadOnly(True)
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
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress)
        progress_row.addWidget(self.elapsed_label)
        layout.addLayout(buttons)
        layout.addLayout(actions)
        layout.addLayout(progress_row)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Command"))
        layout.addWidget(self.command_text)
        layout.addWidget(self.log_text)
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

    def _on_run_line(self, line: str) -> None:
        self.log_text.append(line)
        match = re.search(r"(\d+)\s+of\s+(\d+)\s+steps", line)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            if total:
                self.progress.setValue(int(done / total * 100))
        # Detect a stale lock / incomplete-output state and offer auto-recovery
        # once per run so a killed-WSL orphan does not wedge every later start.
        if not self._recovery_offered and re.search(
            r"LockException|IncompleteFilesException|Directory cannot be locked|incomplete", line
        ):
            self._recovery_offered = True
            QTimer.singleShot(0, self._offer_auto_recovery)

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
        # Ensure the wedged run is fully gone before unlocking/resuming.
        self._stop_run(announce=False)
        if self.runner is not None and self.config is not None:
            self.runner.unlock(self.config)
        self.log_text.append("Auto-recovery: unlocked directory, resuming with --rerun-incomplete.")
        QTimer.singleShot(200, lambda: self._start_snakemake("recover"))

    def _on_run_finished(self, code: int) -> None:
        self.elapsed_timer.stop()
        was_stop = self._stop_in_progress
        self._set_running_ui(False)
        self._stop_in_progress = False
        self._run_mode = None
        if was_stop:
            self.progress.setStyleSheet("")
            self._set_run_status("Stopped", "#B26A00")
            self.log_text.append("Run stopped.")
            return
        if code == 0:
            self.progress.setValue(100)
            self.progress.setStyleSheet("")
            self._set_run_status("Completed", "#2E7D32")
        else:
            # Non-zero: do not imply success. Red bar, red status, keep partial %.
            self.progress.setStyleSheet("QProgressBar::chunk { background-color: #C0392B; }")
            self._set_run_status(f"Failed (exit code {code})", "#C0392B")
        self.log_text.append(f"Process finished with exit code {code}")

    def _stop_run(self, _checked: bool = False, announce: bool = True) -> None:
        if self.runner is None or self._stop_in_progress:
            return
        self._stop_in_progress = True
        if self.stop_button is not None:
            self.stop_button.setEnabled(False)
        if announce:
            self.log_text.append("Stopping run and releasing WSL processes...")
            self._set_run_status("Stopping...", "#B26A00")
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
        generate.clicked.connect(self._generate_reports)
        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        layout.addWidget(generate)
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
            ["results/counts/counts.txt", "results/deseq2/deseq2_results.csv", "results/deseq2/normalized_counts.csv"]
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
        v.addWidget(self.goi_box)
        v.addWidget(save)
        return group

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
        self.fig_palette.addItems(["Blue-Red", "Viridis", "Greyscale"])
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
        form.addRow(self._info_label("Width (in)", "Saved figure width in inches (PNG and SVG)."), self.fig_width)
        form.addRow(self._info_label("Height (in)", "Saved figure height in inches (PNG and SVG)."), self.fig_height)
        form.addRow(self._info_label("DPI (PNG)", "Resolution for the raster PNG export. SVG is vector and unaffected. 300 is publication quality."), self.fig_dpi)
        form.addRow(save_style)
        return group

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
        style.width_in = self.fig_width.value()
        style.height_in = self.fig_height.value()
        style.dpi = self.fig_dpi.value()
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
        df = pd.read_csv(path, sep=sep, comment="#", dtype=str, nrows=200).fillna("")
        self.output_table.setColumnCount(len(df.columns))
        self.output_table.setHorizontalHeaderLabels([str(c) for c in df.columns])
        self.output_table.setRowCount(len(df))
        for r in range(len(df)):
            for c in range(len(df.columns)):
                self.output_table.setItem(r, c, QTableWidgetItem(str(df.iat[r, c])))

    def _refresh_gallery(self) -> None:
        self.figure_pick.blockSignals(True)
        self.figure_pick.clear()
        figures = []
        if self.project_root is not None:
            figures = sorted((self.project_root / "results" / "figures").glob("*.png"))
        self.figure_pick.addItems([f.name for f in figures])
        self.figure_pick.blockSignals(False)
        if figures:
            self.figure_pick.setCurrentIndex(0)
            self._show_selected_figure(figures[0].name)
        else:
            self.figure_viewer.clear()

    def _show_selected_figure(self, name: str) -> None:
        if not name or self.project_root is None:
            return
        path = self.project_root / "results" / "figures" / name
        # When the vector toggle is on, prefer the matching .svg (crisp at any zoom).
        if getattr(self, "svg_toggle", None) is not None and self.svg_toggle.isChecked():
            svg = path.with_suffix(".svg")
            if svg.exists():
                path = svg
        if path.exists():
            self.figure_viewer.set_image(path)

    def _browse_workdir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Working directory", self.workdir.text())
        if directory:
            self.workdir.setText(directory)

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

    def _create_benchmark_project(self) -> None:
        workdir = Path(self.workdir.text().strip() or str(Path.home() / "BulkSeqProjects"))
        messages = validate_working_directory(workdir, use_wsl=self.use_wsl.isChecked())
        if any(m.get("status") == "FAIL" for m in messages):
            self.project_status.setPlainText(
                "Cannot create benchmark project here:\n" + self._format_workdir_messages(messages)
            )
            QMessageBox.warning(self, APP_NAME, self._format_workdir_messages(messages))
            return
        catalog = load_benchmark_catalog()
        benchmark_id = str(catalog[0]["id"])
        try:
            root = create_benchmark_project(benchmark_id, workdir, self.project_name.text() or benchmark_id)
        except (OSError, ValueError) as exc:
            self.project_status.setPlainText(f"Benchmark project creation failed: {exc}")
            QMessageBox.critical(self, APP_NAME, f"Benchmark project creation failed:\n{exc}")
            return
        self._load_project(root)
        self.project_status.setPlainText(
            f"Created benchmark project: {root}\n"
            f"Dataset: {catalog[0]['name']}\n"
            f"Accessions: {', '.join(sample['original_accession'] for sample in catalog[0]['samples'])}\n"
            + self._format_workdir_messages(messages)
        )

    def _open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open project")
        if directory:
            self._load_project(Path(directory))

    def _load_project(self, root: Path) -> None:
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
        self._populate_widgets_from_config()
        samples = root / "config" / "samples.tsv"
        if samples.exists():
            self.metadata_table.load_tsv(samples)
        self.project_status.setPlainText(f"Open project: {root}")

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
        contrast = self.config.deseq2.contrasts[0] if self.config.deseq2.contrasts else None
        if contrast:
            self.contrast_factor.setText(contrast.factor)
            self.numerator.setCurrentText(contrast.numerator)
            self.denominator.setCurrentText(contrast.denominator)
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
        self.fig_width.setValue(fig.width_in)
        self.fig_height.setValue(fig.height_in)
        self.fig_dpi.setValue(fig.dpi)
        goi_path = self.config.gene_sets.custom_gene_list
        if goi_path and self.project_root is not None and (self.project_root / goi_path).exists():
            self.goi_box.setPlainText((self.project_root / goi_path).read_text(encoding="utf-8").strip())
        else:
            self.goi_box.clear()
        organism = self.config.reference.organism_name
        for i in range(self.reference_list.count()):
            if self.reference_list.item(i).text().startswith(f"{organism} "):
                self.reference_list.setCurrentRow(i)
                break

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
        self.metadata_table.load_dataframe(df)
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
        allow_pending_sra = self.config is not None and self.config.input.type == "sra"
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

    def _save_workflow_settings(self) -> None:
        if self.config is None or self.project_root is None:
            return
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

    def _on_detect_done(self, result: object) -> None:
        self.resources_busy.setVisible(False)
        system, rec = result
        self.cores.setValue(int(rec["total_threads"]))
        self.ram.setValue(int(rec["total_memory_gb"]))
        self.system_info_label.setText(
            f"{system.cpu_model} — {system.physical_cores} cores "
            f"({system.logical_threads} threads), {system.total_ram_gb:.0f} GB RAM, "
            f"{system.disk_free_gb:.0f} GB free disk."
        )
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
            allow_pending_sra = self.config is not None and self.config.input.type == "sra"
            messages = validate_metadata(
                self.metadata_table.to_dataframe(),
                allow_pending_sra=allow_pending_sra,
                design_variables=self._design_variables(),
            )
            write_check(self.project_root, "01_input_validation", messages)
            text = self._format_messages(messages)
            self.sanity_text.setPlainText(text)
            self._refresh_phase_checks()
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
            ref = self.config.reference
            has_url = bool(ref.genome_fasta_url and ref.annotation_gtf_url)
            has_local = bool(ref.genome_fasta and ref.annotation_file)
            if not (has_url or has_local):
                QMessageBox.warning(
                    self, APP_NAME,
                    "No reference is set, so the run cannot start.\n\n"
                    "Open the Reference Manager tab and either select a preset organism "
                    "and click 'Use Selected Preset', or import a custom genome FASTA + "
                    "annotation. Then start the run again.",
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
        if mode in ("run", "resume", "recover") and not self._run_gate_ok():
            return
        # Persist the in-memory metadata table so the run uses current edits;
        # Snakemake reads config/samples.tsv from disk, not the GUI table.
        save_metadata(self.metadata_table.to_dataframe(), self.project_root / "config" / "samples.tsv")
        self._save_workflow_settings()
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
        self._stop_in_progress = False
        self._set_running_ui(True)
        if mode in ("run", "resume", "recover", "figures"):
            self.progress.setValue(0)
            self.progress.setStyleSheet("")
            self._set_run_status("Regenerating figures..." if mode == "figures" else "Running...", "#2C6FB6")
            self._run_start = time.monotonic()
            self.elapsed_timer.start(1000)
        else:
            self._set_run_status("Running..." if mode == "dry-run" else "Unlocking...", "#2C6FB6")
        self.runner_thread.start()

    def _generate_reports(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        reports = self.project_root / "results" / "reports"
        # If a real pipeline run already produced reports, display those; otherwise
        # generate the lightweight GUI-side summaries.
        if not (reports / "run_summary.txt").exists():
            estimate = estimate_runtime(self.config, self.metadata_table.to_dataframe()) if self.config else None
            write_timing_summary(self.project_root, estimate)
            write_run_summary(self.project_root, data_path("default_config.yaml"))
        sections = []
        for name in ("run_summary.txt", "timing_summary.txt"):
            path = reports / name
            if path.exists():
                sections.append(f"===== {name} =====\n{path.read_text(encoding='utf-8')}")
        sanity = self.project_root / "checks" / "sanity_checks.txt"
        if sanity.exists():
            sections.append(f"===== sanity_checks.txt =====\n{sanity.read_text(encoding='utf-8')}")
        self.report_text.setPlainText("\n\n".join(sections) if sections else "No reports generated yet.")

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
