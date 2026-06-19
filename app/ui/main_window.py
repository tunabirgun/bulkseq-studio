from __future__ import annotations

import shutil
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QDesktopServices, QPixmap
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
from app.core.runtime_estimator import estimate_runtime
from app.core.sanity_checks import write_check
from app.core.snakemake_runner import SnakemakeRunner, build_snakemake_command
from app.core.timing import write_timing_summary
from app.core.paths import data_path
from app.ui.metadata_editor import MetadataTable
from app.ui.readiness_dialog import ReadinessDialog


class RunnerThread(QThread):
    line = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, runner: SnakemakeRunner) -> None:
        super().__init__()
        self.runner = runner

    def run(self) -> None:
        process = self.runner.start()
        assert process.stdout is not None
        for line in process.stdout:
            self.line.emit(line.rstrip())
        self.finished_with_code.emit(process.wait())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.manager = ProjectManager()
        self.project_root: Path | None = None
        self.config: AppConfig | None = None
        self.runner_thread: RunnerThread | None = None
        self.runner: SnakemakeRunner | None = None
        self.readiness_dialog: ReadinessDialog | None = None

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
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
        if os.environ.get("BULKSEQ_SKIP_READINESS_DIALOG") != "1":
            QTimer.singleShot(500, self.show_readiness_dialog)

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
        readiness = QPushButton("Check Setup")
        readiness.clicked.connect(self.show_readiness_dialog)
        self.project_status = QTextEdit()
        self.project_status.setReadOnly(True)
        layout.addRow("Project name", self.project_name)
        layout.addRow("Working directory", workdir_row)
        layout.addRow(create, open_existing)
        layout.addRow(benchmark, readiness)
        layout.addRow("Status", self.project_status)
        self.tabs.addTab(page, "Project")

    def _build_input_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.sra_box = QTextEdit()
        self.sra_box.setPlaceholderText("Paste SRR accessions, one per line")
        save_sra = QPushButton("Save SRA Accessions")
        save_sra.clicked.connect(self._save_sra)
        pick_fastq = QPushButton("Select FASTQ Files")
        pick_fastq.clicked.connect(self._select_fastqs)
        self.input_preview = QTextEdit()
        self.input_preview.setReadOnly(True)
        layout.addWidget(QLabel("SRA accessions"))
        layout.addWidget(self.sra_box)
        layout.addWidget(save_sra)
        layout.addWidget(pick_fastq)
        layout.addWidget(self.input_preview)
        self.tabs.addTab(page, "Input Data")

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
        buttons = QHBoxLayout()
        for text, slot in [
            ("Add row", self.metadata_add_row),
            ("Delete rows", self.metadata_delete_rows),
            ("Duplicate rows", self.metadata_duplicate_rows),
            ("Add column", self._add_column),
            ("Rename column", self._rename_column),
            ("Remove column", self._remove_column),
            ("Assign condition", self._assign_condition),
            ("Autofill replicates", self.metadata_autofill),
            ("Import TSV/CSV/XLSX", self._import_metadata),
            ("Export TSV", self._export_metadata),
            ("Restore auto-generated", self._restore_auto_metadata),
            ("Save samples.tsv", self._save_metadata),
            ("Validate", self._validate_metadata),
        ]:
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        self.metadata_table = MetadataTable()
        self.metadata_messages = QTextEdit()
        self.metadata_messages.setReadOnly(True)
        layout.addLayout(buttons)
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
        self.tabs.addTab(page, "Reference Manager")

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
        save = QPushButton("Save Workflow Settings")
        save.clicked.connect(self._save_workflow_settings)
        layout.addRow("Aligner", self.aligner)
        layout.addRow("Quantifier", self.quantifier)
        layout.addRow("fastp trimming", self.trim)
        layout.addRow("fastp quality (-q)", self.fastp_q)
        layout.addRow("fastp min length (-l)", self.fastp_len)
        layout.addRow("fastp poly-G (-g)", self.trim_poly_g)
        layout.addRow("rRNA filtering", self.rrna)
        layout.addRow("Enrichment", self.enrichment)
        layout.addRow("Figures", self.figures)
        layout.addRow("DESeq2 design", self.design)
        layout.addRow(refresh)
        layout.addRow("Contrast factor", self.contrast_factor)
        layout.addRow("Numerator (treated)", self.numerator)
        layout.addRow("Denominator (reference)", self.denominator)
        layout.addRow("Reference level", self.reference_level)
        layout.addRow("Alpha (FDR)", self.alpha)
        layout.addRow(QLabel("featureCounts strandedness is auto-inferred per protocol."))
        layout.addRow(save)
        self.tabs.addTab(page, "Workflow Settings")

    def _refresh_conditions(self) -> None:
        df = self.metadata_table.to_dataframe()
        if "condition" not in df.columns:
            return
        values = sorted({str(v) for v in df["condition"].tolist() if str(v) and str(v) != "unknown"})
        for combo in (self.numerator, self.denominator, self.reference_level):
            current = combo.currentText()
            combo.clear()
            combo.addItems(values)
            if current in values:
                combo.setCurrentText(current)

    def _build_resources_tab(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        self.resource_text = QTextEdit()
        self.resource_text.setReadOnly(True)
        self.profile = QComboBox()
        self.profile.addItems(["balanced", "low", "high", "custom"])
        detect = QPushButton("Detect and Recommend")
        detect.clicked.connect(self._detect_resources)
        self.cores = QSpinBox()
        self.cores.setRange(1, 256)
        self.ram = QSpinBox()
        self.ram.setRange(1, 2048)
        save = QPushButton("Save Resources")
        save.clicked.connect(self._save_resources)
        layout.addWidget(self.resource_text, 0, 0, 1, 3)
        layout.addWidget(QLabel("Profile"), 1, 0)
        layout.addWidget(self.profile, 1, 1)
        layout.addWidget(detect, 1, 2)
        layout.addWidget(QLabel("Snakemake cores"), 2, 0)
        layout.addWidget(self.cores, 2, 1)
        layout.addWidget(QLabel("RAM GB"), 3, 0)
        layout.addWidget(self.ram, 3, 1)
        layout.addWidget(save, 4, 1)
        self.tabs.addTab(page, "Resources")

    def _build_runtime_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        estimate = QPushButton("Estimate Runtime")
        estimate.clicked.connect(self._estimate_runtime)
        self.runtime_text = QTextEdit()
        self.runtime_text.setReadOnly(True)
        layout.addWidget(estimate)
        layout.addWidget(self.runtime_text)
        self.tabs.addTab(page, "Runtime Estimate")

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
        self.sanity_text = QTextEdit()
        self.sanity_text.setReadOnly(True)
        layout.addLayout(buttons)
        layout.addWidget(self.approve_review)
        layout.addWidget(self.sanity_text)
        self.tabs.addTab(page, "Sanity Checks")

    def _build_run_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for text, mode in [("Dry Run", "dry-run"), ("Start Run", "run"), ("Resume", "resume"), ("Unlock", "unlock")]:
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, m=mode: self._start_snakemake(m))
            buttons.addWidget(button)
        self.use_wsl = QCheckBox("Use WSL2")
        self.use_wsl.setChecked(True)
        buttons.addWidget(self.use_wsl)
        actions = QHBoxLayout()
        stop = QPushButton("Stop")
        stop.clicked.connect(self._stop_run)
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
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress)
        progress_row.addWidget(self.elapsed_label)
        layout.addLayout(buttons)
        layout.addLayout(actions)
        layout.addLayout(progress_row)
        layout.addWidget(QLabel("Command"))
        layout.addWidget(self.command_text)
        layout.addWidget(self.log_text)
        self.tabs.addTab(page, "Run Monitor")

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

    def _on_run_finished(self, code: int) -> None:
        self.elapsed_timer.stop()
        if code == 0:
            self.progress.setValue(100)
        self.log_text.append(f"Process finished with exit code {code}")

    def _stop_run(self) -> None:
        if self.runner is not None:
            self.runner.stop()
            self.log_text.append("Stop requested.")

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
        self.tabs.addTab(page, "Reports")

    def _build_outputs_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.output_table_pick = QComboBox()
        self.output_table_pick.addItems(
            ["results/counts/counts.txt", "results/deseq2/deseq2_results.csv", "results/deseq2/normalized_counts.csv"]
        )
        load = QPushButton("Load table preview")
        load.clicked.connect(self._load_output_table)
        gallery = QPushButton("Refresh figure gallery")
        gallery.clicked.connect(self._refresh_gallery)
        open_results = QPushButton("Open results folder")
        open_results.clicked.connect(lambda: self._open_subpath("results"))
        controls.addWidget(self.output_table_pick)
        controls.addWidget(load)
        controls.addWidget(gallery)
        controls.addWidget(open_results)
        self.output_table = QTableWidget()
        self.output_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.gallery_area = QScrollArea()
        self.gallery_area.setWidgetResizable(True)
        self.gallery_inner = QWidget()
        self.gallery_layout = QHBoxLayout(self.gallery_inner)
        self.gallery_area.setWidget(self.gallery_inner)
        self.gallery_area.setMinimumHeight(240)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Table preview (first 200 rows)"))
        layout.addWidget(self.output_table)
        layout.addWidget(QLabel("Figures"))
        layout.addWidget(self.gallery_area)
        self.tabs.addTab(page, "Outputs")

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
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.project_root is None:
            return
        figures = sorted((self.project_root / "results" / "figures").glob("*.png"))
        if not figures:
            self.gallery_layout.addWidget(QLabel("No figures yet. Run the pipeline."))
            return
        for fig in figures:
            label = QLabel()
            pixmap = QPixmap(str(fig)).scaledToHeight(200, Qt.SmoothTransformation)
            label.setPixmap(pixmap)
            label.setToolTip(fig.name)
            self.gallery_layout.addWidget(label)

    def _browse_workdir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Working directory", self.workdir.text())
        if directory:
            self.workdir.setText(directory)

    def show_readiness_dialog(self) -> None:
        self.readiness_dialog = ReadinessDialog(self)
        self.readiness_dialog.show()

    def _create_project(self) -> None:
        messages = validate_working_directory(Path(self.workdir.text()))
        root = self.manager.create_project(self.project_name.text(), Path(self.workdir.text()))
        self._load_project(root)
        self.project_status.setPlainText(f"Created {root}\n" + self._format_messages(messages))

    def _create_benchmark_project(self) -> None:
        messages = validate_working_directory(Path(self.workdir.text()))
        catalog = load_benchmark_catalog()
        benchmark_id = str(catalog[0]["id"])
        root = create_benchmark_project(benchmark_id, Path(self.workdir.text()), self.project_name.text() or benchmark_id)
        self._load_project(root)
        self.project_status.setPlainText(
            f"Created benchmark project: {root}\n"
            f"Dataset: {catalog[0]['name']}\n"
            f"Accessions: {', '.join(sample['original_accession'] for sample in catalog[0]['samples'])}\n"
            + self._format_messages(messages)
        )

    def _open_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open project")
        if directory:
            self._load_project(Path(directory))

    def _load_project(self, root: Path) -> None:
        self.project_root = root
        self.config = self.manager.load_config(root)
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
        organism = self.config.reference.organism_name
        for i in range(self.reference_list.count()):
            if self.reference_list.item(i).text().startswith(f"{organism} "):
                self.reference_list.setCurrentRow(i)
                break

    def _design_variables(self) -> list[str]:
        # Parse a DESeq2 design formula (e.g. "~ batch + condition") into the
        # metadata columns it references, so missing design columns are flagged.
        if self.config is None:
            return []
        formula = self.config.deseq2.design_formula.split("~", 1)[-1]
        tokens = re.split(r"[+*:]", formula)
        return [t.strip() for t in tokens if t.strip()]

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
        self.config.reference.mode = "preset"
        self.config.reference.organism_name = str(entry["organism_name"])
        self.config.reference.strain = str(entry.get("strain") or "")
        self.config.reference.genome_size_category = str(entry.get("genome_size_category") or "custom")
        self.manager.save_config(self.project_root, self.config)
        self.reference_details.setPlainText("\n".join(f"{k}: {v}" for k, v in entry.items()))

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
        root = self.project_root or Path(self.workdir.text())
        system = detect_system(root)
        rec = recommend_profile(system, self.profile.currentText())
        self.cores.setValue(int(rec["total_threads"]))
        self.ram.setValue(int(rec["total_memory_gb"]))
        self.resource_text.setPlainText("\n".join(f"{k}: {v}" for k, v in system.to_dict().items()) + "\n\nRecommendation\n" + "\n".join(f"{k}: {v}" for k, v in rec.items()))

    def _save_resources(self) -> None:
        if self.config is None or self.project_root is None:
            return
        self.config.resources.profile = self.profile.currentText()  # type: ignore[assignment]
        self.config.resources.total_threads = self.cores.value()
        self.config.resources.total_memory_gb = self.ram.value()
        self.manager.save_config(self.project_root, self.config)

    def _estimate_runtime(self) -> None:
        if self.config is None:
            return
        df = self.metadata_table.to_dataframe()
        estimate = estimate_runtime(self.config, df)
        self.runtime_text.setPlainText("\n".join(f"{k}: {v}" for k, v in estimate.items()))

    def _run_sanity_checks(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
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
        if self.config is None or self.project_root is None:
            return
        if mode in ("run", "resume") and not self._run_gate_ok():
            return
        self._save_workflow_settings()
        self._save_resources()
        command = build_snakemake_command(self.project_root, self.config, mode=mode, use_wsl=self.use_wsl.isChecked())
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
        if mode in ("run", "resume"):
            self.progress.setValue(0)
            self._run_start = time.monotonic()
            self.elapsed_timer.start(1000)
        self.runner_thread.start()

    def _generate_reports(self) -> None:
        if self.project_root is None:
            return
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
