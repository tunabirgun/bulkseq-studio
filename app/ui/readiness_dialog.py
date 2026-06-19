from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout

from app.core.paths import app_root
from app.core.readiness import check_readiness, has_wsl_core_environment, install_python_packages, missing_python_packages, next_readiness_actions, readiness_summary
from app.core.setup_installer import launch_wsl_admin_install, launch_wsl_bioenv_install


class PipInstallThread(QThread):
    line = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, requirements_path: Path) -> None:
        super().__init__()
        self.requirements_path = requirements_path

    def run(self) -> None:
        process = install_python_packages(self.requirements_path)
        assert process.stdout is not None
        for line in process.stdout:
            self.line.emit(line.rstrip())
        self.finished_with_code.emit(process.wait())


class WslBioenvInstallThread(QThread):
    line = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, profile: str = "core") -> None:
        super().__init__()
        self.profile = profile
        self.process = None

    def run(self) -> None:
        self.process = launch_wsl_bioenv_install(profile=self.profile)
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.line.emit(line.rstrip())
        self.finished_with_code.emit(self.process.wait())

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


class ReadinessDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BulkSeq Studio Readiness")
        self.resize(760, 520)
        self.install_thread: PipInstallThread | None = None
        self.wsl_install_thread: WslBioenvInstallThread | None = None

        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Re-check")
        self.refresh_button.clicked.connect(self.refresh)
        self.install_button = QPushButton("Install Missing Python Packages")
        self.install_button.clicked.connect(self.install_python_dependencies)
        self.install_wsl_button = QPushButton("Install/Enable WSL")
        self.install_wsl_button.clicked.connect(self.install_wsl)
        self.install_bioenv_button = QPushButton("Install/Repair Core WSL Env")
        self.install_bioenv_button.clicked.connect(self.install_wsl_bioenv)
        self.install_full_button = QPushButton("Install Full R/DESeq2 Stack")
        self.install_full_button.clicked.connect(self.install_full_wsl_bioenv)
        self.stop_install_button = QPushButton("Stop Install")
        self.stop_install_button.clicked.connect(self.stop_wsl_install)
        self.stop_install_button.setEnabled(False)
        self.log_button = QPushButton("Show Setup Log")
        self.log_button.clicked.connect(self.show_setup_log)
        self.close_button = QPushButton("Continue")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.install_button)
        buttons.addWidget(self.install_wsl_button)
        buttons.addWidget(self.install_bioenv_button)
        buttons.addWidget(self.install_full_button)
        buttons.addWidget(self.stop_install_button)
        buttons.addWidget(self.log_button)
        buttons.addWidget(self.close_button)
        layout.addWidget(self.text)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        items = check_readiness()
        missing = missing_python_packages()
        guidance = [
            readiness_summary(items),
            "",
            "Next Step",
            "---------",
            "\n".join(next_readiness_actions(items)),
            "",
            "Notes",
            "-----",
            "Windows PATH checks and WSL environment checks are shown separately.",
            "It is normal for Windows PATH tools to remain REVIEW_REQUIRED if the tools are installed inside WSL.",
            "Python packages can be installed from this dialog.",
            "WSL2 installation uses a separate Administrator PowerShell window because Windows requires elevation.",
            "WSL package setup is staged: first micromamba, then the core bulkseq environment.",
            "The core environment installs Snakemake/SRA tools/FastQC/MultiQC/fastp/STAR/HISAT2/Salmon/featureCounts/samtools.",
            "The full stack adds R/DESeq2/enrichment packages and can take much longer.",
            "WSL environment install logs are written to scripts/logs/wsl_bioenv_install.log.",
            "The GUI can still create projects, edit metadata, estimate runtime, and generate configs before those tools are installed.",
        ]
        if missing:
            guidance.insert(0, f"Missing Python packages: {', '.join(missing)}\n")
        else:
            guidance.insert(0, "Python GUI/core packages are installed.\n")
        self.text.setPlainText("\n".join(guidance))
        self.install_button.setEnabled(bool(missing))
        self.install_full_button.setEnabled(has_wsl_core_environment(items))

    def install_python_dependencies(self) -> None:
        requirements = app_root() / "requirements.txt"
        self.install_button.setEnabled(False)
        self.text.append(f"\nInstalling Python dependencies from {requirements}...\n")
        self.install_thread = PipInstallThread(requirements)
        self.install_thread.line.connect(self.text.append)
        self.install_thread.finished_with_code.connect(self._install_finished)
        self.install_thread.start()

    def _install_finished(self, code: int) -> None:
        self.text.append(f"\nPython dependency installer finished with exit code {code}.\n")
        self.refresh()

    def install_wsl(self) -> None:
        self.text.append("\nOpening Administrator PowerShell to install/enable WSL. Approve the Windows UAC prompt if you want to continue.\n")
        launch_wsl_admin_install()

    def install_wsl_bioenv(self) -> None:
        self._install_wsl_bioenv("core")

    def install_full_wsl_bioenv(self) -> None:
        self._install_wsl_bioenv("full")

    def _install_wsl_bioenv(self, profile: str) -> None:
        self.install_bioenv_button.setEnabled(False)
        self.install_full_button.setEnabled(False)
        self.stop_install_button.setEnabled(True)
        self.text.append(f"\nInstalling WSL bioinformatics environment profile: {profile}. This can take a long time and may ask for your WSL sudo password.\n")
        self.wsl_install_thread = WslBioenvInstallThread(profile)
        self.wsl_install_thread.line.connect(self.text.append)
        self.wsl_install_thread.finished_with_code.connect(self._wsl_bioenv_finished)
        self.wsl_install_thread.start()

    def _wsl_bioenv_finished(self, code: int) -> None:
        self.text.append(f"\nWSL bioinformatics installer finished with exit code {code}.\n")
        self.install_bioenv_button.setEnabled(True)
        self.install_full_button.setEnabled(True)
        self.stop_install_button.setEnabled(False)
        self.refresh()

    def stop_wsl_install(self) -> None:
        if self.wsl_install_thread is not None:
            self.text.append("\nStopping WSL bioinformatics installer...\n")
            self.wsl_install_thread.stop()

    def show_setup_log(self) -> None:
        log_path = app_root() / "scripts" / "logs" / "wsl_bioenv_install.log"
        if not log_path.exists():
            self.text.append(f"\nNo WSL bioinformatics setup log found yet: {log_path}\n")
            return
        self.text.setPlainText(log_path.read_text(encoding="utf-8", errors="replace"))
