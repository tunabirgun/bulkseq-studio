from __future__ import annotations

import shutil
import os
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.constants import APP_NAME
from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.config_models import AppConfig
from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import dataframe_from_rows, load_metadata, save_metadata, validate_metadata
from app.core.project import ProjectManager, validate_working_directory
from app.core.provenance import write_run_summary
from app.core.reference_manager import load_reference_catalog
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
        pick_fastq = QPushButton("Select FASTQ Files")
        pick_fastq.clicked.connect(self._select_fastqs)
        self.input_preview = QTextEdit()
        self.input_preview.setReadOnly(True)
        layout.addWidget(QLabel("SRA accessions"))
        layout.addWidget(self.sra_box)
        layout.addWidget(pick_fastq)
        layout.addWidget(self.input_preview)
        self.tabs.addTab(page, "Input Data")

    def _build_metadata_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for text, slot in [
            ("Add row", self.metadata_add_row),
            ("Delete rows", self.metadata_delete_rows),
            ("Duplicate rows", self.metadata_duplicate_rows),
            ("Autofill replicates", self.metadata_autofill),
            ("Import TSV/CSV/XLSX", self._import_metadata),
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
        self.reference_details = QTextEdit()
        self.reference_details.setReadOnly(True)
        layout.addWidget(self.reference_list)
        layout.addWidget(choose)
        layout.addWidget(self.reference_details)
        self.tabs.addTab(page, "Reference Manager")

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
        self.design = QLineEdit("~ condition")
        save = QPushButton("Save Workflow Settings")
        save.clicked.connect(self._save_workflow_settings)
        layout.addRow("Aligner", self.aligner)
        layout.addRow("Quantifier", self.quantifier)
        layout.addRow("fastp trimming", self.trim)
        layout.addRow("rRNA filtering", self.rrna)
        layout.addRow("Enrichment", self.enrichment)
        layout.addRow("Figures", self.figures)
        layout.addRow("DESeq2 design", self.design)
        layout.addRow(save)
        self.tabs.addTab(page, "Workflow Settings")

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
        run = QPushButton("Run Project and Metadata Checks")
        run.clicked.connect(self._run_sanity_checks)
        self.sanity_text = QTextEdit()
        self.sanity_text.setReadOnly(True)
        layout.addWidget(run)
        layout.addWidget(self.sanity_text)
        self.tabs.addTab(page, "Sanity Checks")

    def _build_run_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for text, mode in [("Validate Project", "dry-run"), ("Dry Run", "dry-run"), ("Start Run", "run"), ("Resume", "resume"), ("Unlock", "unlock")]:
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, m=mode: self._start_snakemake(m))
            buttons.addWidget(button)
        self.use_wsl = QCheckBox("Use WSL2")
        buttons.addWidget(self.use_wsl)
        self.command_text = QLineEdit()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Command"))
        layout.addWidget(self.command_text)
        layout.addWidget(self.log_text)
        self.tabs.addTab(page, "Run Monitor")

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
        self.cores.setValue(self.config.resources.total_threads)
        self.ram.setValue(self.config.resources.total_memory_gb)
        samples = root / "config" / "samples.tsv"
        if samples.exists():
            self.metadata_table.load_tsv(samples)
        self.project_status.setPlainText(f"Open project: {root}")

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

    def _save_metadata(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        save_metadata(self.metadata_table.to_dataframe(), self.project_root / "config" / "samples.tsv")
        self.metadata_messages.setPlainText("Saved config/samples.tsv")

    def _validate_metadata(self) -> None:
        allow_pending_sra = self.config is not None and self.config.input.type == "sra"
        messages = validate_metadata(self.metadata_table.to_dataframe(), allow_pending_sra=allow_pending_sra)
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
        self.config.deseq2.design_formula = self.design.text()
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
        messages = validate_metadata(self.metadata_table.to_dataframe(), allow_pending_sra=allow_pending_sra)
        write_check(self.project_root, "01_input_validation", messages)
        text = self._format_messages(messages)
        self.sanity_text.setPlainText(text)

    def _start_snakemake(self, mode: str) -> None:
        if self.config is None or self.project_root is None:
            return
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
        self.runner = SnakemakeRunner(self.project_root, command)
        self.runner_thread = RunnerThread(self.runner)
        self.runner_thread.line.connect(self.log_text.append)
        self.runner_thread.finished_with_code.connect(lambda code: self.log_text.append(f"Process finished with exit code {code}"))
        self.runner_thread.start()

    def _generate_reports(self) -> None:
        if self.project_root is None:
            return
        estimate = estimate_runtime(self.config, self.metadata_table.to_dataframe()) if self.config else None
        write_timing_summary(self.project_root, estimate)
        write_run_summary(self.project_root, data_path("default_config.yaml"))
        reports = self.project_root / "results" / "reports"
        self.report_text.setPlainText(f"Wrote:\n{reports / 'run_summary.txt'}\n{reports / 'timing_summary.txt'}\n{reports / 'sanity_checks.txt'}")

    def _require_project(self) -> bool:
        if self.project_root is None:
            QMessageBox.warning(self, APP_NAME, "Create or open a project first.")
            return False
        return True

    @staticmethod
    def _format_messages(messages: list[dict[str, str]]) -> str:
        return "\n".join(f"{m.get('status')}: {m.get('message')}" for m in messages)
