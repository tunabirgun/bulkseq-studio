from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.constants import APP_NAME
from app.core.paths import app_root
from app.core.readiness import (
    ReadinessItem,
    check_readiness,
    has_wsl_core_environment,
    install_python_packages,
    missing_python_packages,
    next_readiness_actions,
    readiness_summary,
)
from app.core.setup_installer import launch_wsl_admin_install, launch_wsl_bioenv_install

# ---------------------------------------------------------------------------
# Shared design language (mirrors app.ui.theme when present; defined locally so
# this dialog renders correctly even before a central theme module exists).
# ---------------------------------------------------------------------------

PRIMARY = "#2C6FB6"
PRIMARY_HOVER = "#2560A0"
PRIMARY_PRESSED = "#1E4F86"
BACKGROUND = "#F5F7FA"
SURFACE = "#FFFFFF"
BORDER = "#D7DEE6"
TEXT = "#1F2933"
MUTED = "#6B7785"
SUCCESS = "#2E7D32"
WARNING = "#B26A00"
ERROR = "#C0392B"
REVIEW = "#6A1B9A"
BASE_FONT = "'Segoe UI', 'Segoe UI Variable', sans-serif"

# Card states used by the UI, independent of the raw ReadinessItem status.
STATE_READY = "ready"
STATE_ACTION = "action"
STATE_OPTIONAL = "optional"
STATE_CHECKING = "checking"

_PILL_TEXT = {
    STATE_READY: "Ready",
    STATE_ACTION: "Action needed",
    STATE_OPTIONAL: "Optional",
    STATE_CHECKING: "Checking…",
}

_PILL_COLOR = {
    STATE_READY: SUCCESS,
    STATE_ACTION: WARNING,
    STATE_OPTIONAL: REVIEW,
    STATE_CHECKING: MUTED,
}


def _make_status_pill(state: str) -> QLabel:
    """Build a colored status pill.

    Prefers a shared factory from app.ui.theme when it exists so the whole app
    stays visually consistent; falls back to a self-contained stylesheet pill.
    """
    try:
        from app.ui import theme  # type: ignore

        factory = getattr(theme, "status_pill", None) or getattr(theme, "StatusPill", None)
        if factory is not None:
            pill = factory(_PILL_TEXT.get(state, state))  # type: ignore[misc]
            if isinstance(pill, QLabel):
                return pill
    except Exception:
        pass
    return _StatusPill(state)


class _StatusPill(QLabel):
    """Self-contained colored status pill following the shared palette."""

    def __init__(self, state: str = STATE_CHECKING) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        color = _PILL_COLOR.get(state, MUTED)
        self.setText(_PILL_TEXT.get(state, state))
        self.setStyleSheet(
            "QLabel {"
            f" color: {color};"
            f" border: 1px solid {color};"
            " border-radius: 9px;"
            " padding: 2px 12px;"
            " font-weight: 600;"
            " font-size: 9pt;"
            " background: transparent;"
            "}"
        )


# ---------------------------------------------------------------------------
# Install worker threads (unchanged behavior, preserved public surface).
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Status card widget.
# ---------------------------------------------------------------------------


class _StatusCard(QFrame):
    """One requirement group: title, status pill, explanation, optional action."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("statusCard")
        self.setStyleSheet(
            "#statusCard {"
            f" background: {SURFACE};"
            f" border: 1px solid {BORDER};"
            " border-radius: 6px;"
            "}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {TEXT}; font-size: 11pt; font-weight: 600; background: transparent;"
        )
        self.pill = _make_status_pill(STATE_CHECKING)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.pill)
        outer.addLayout(header)

        self.detail_label = QLabel("Checking…")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(
            f"color: {MUTED}; font-size: 9.5pt; background: transparent;"
        )
        outer.addWidget(self.detail_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        self.action_button = QPushButton()
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.setStyleSheet(_primary_button_style())
        self.action_button.setVisible(False)
        action_row.addWidget(self.action_button)
        action_row.addStretch(1)
        outer.addLayout(action_row)

        self._handler = None

    def update_state(
        self,
        state: str,
        detail: str,
        action_label: str | None = None,
        action_handler=None,
        action_enabled: bool = True,
    ) -> None:
        if isinstance(self.pill, _StatusPill):
            self.pill.set_state(state)
        else:
            # Replace a theme-provided pill that cannot be re-stated in place.
            new_pill = _make_status_pill(state)
            self.pill.parentWidget().layout().replaceWidget(self.pill, new_pill)  # type: ignore[union-attr]
            self.pill.deleteLater()
            self.pill = new_pill
        self.detail_label.setText(detail)
        if action_label:
            self.action_button.setText(action_label)
            self.action_button.setEnabled(action_enabled)
            self.action_button.setVisible(True)
            try:
                self.action_button.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            if action_handler is not None:
                self.action_button.clicked.connect(action_handler)
                self._handler = action_handler
        else:
            self.action_button.setVisible(False)

    def set_action_enabled(self, enabled: bool) -> None:
        if self.action_button.isVisible():
            self.action_button.setEnabled(enabled)


def _primary_button_style() -> str:
    return (
        "QPushButton {"
        f" background: {PRIMARY};"
        " color: #FFFFFF;"
        " border: none;"
        " border-radius: 6px;"
        " padding: 7px 16px;"
        " font-size: 9.5pt;"
        " font-weight: 600;"
        "}"
        f"QPushButton:hover {{ background: {PRIMARY_HOVER}; }}"
        f"QPushButton:pressed {{ background: {PRIMARY_PRESSED}; }}"
        "QPushButton:disabled { background: #A9BDD4; color: #EEF2F6; }"
    )


def _secondary_button_style() -> str:
    return (
        "QPushButton {"
        f" background: {SURFACE};"
        f" color: {PRIMARY};"
        f" border: 1px solid {BORDER};"
        " border-radius: 6px;"
        " padding: 6px 14px;"
        " font-size: 9.5pt;"
        "}"
        f"QPushButton:hover {{ border-color: {PRIMARY}; }}"
        "QPushButton:disabled { color: #A9BDD4; }"
    )


# ---------------------------------------------------------------------------
# Dialog.
# ---------------------------------------------------------------------------


class ReadinessDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} Setup")
        self.resize(720, 640)
        self.setStyleSheet(f"QDialog {{ background: {BACKGROUND}; font-family: {BASE_FONT}; }}")

        self.install_thread: PipInstallThread | None = None
        self.wsl_install_thread: WslBioenvInstallThread | None = None
        self._installing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # Header: title + summary line + Re-check.
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        heading = QLabel("Environment setup")
        heading.setStyleSheet(
            f"color: {TEXT}; font-size: 15pt; font-weight: 600; background: transparent;"
        )
        self.summary_label = QLabel("Checking requirements…")
        self.summary_label.setStyleSheet(
            f"color: {MUTED}; font-size: 10pt; background: transparent;"
        )
        title_box.addWidget(heading)
        title_box.addWidget(self.summary_label)
        header.addLayout(title_box)
        header.addStretch(1)
        self.refresh_button = QPushButton("Re-check")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setStyleSheet(_secondary_button_style())
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        # Scrollable card column.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        card_host = QWidget()
        card_host.setStyleSheet("background: transparent;")
        cards_layout = QVBoxLayout(card_host)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)

        self.card_python = _StatusCard("Python GUI / core packages")
        self.card_wsl = _StatusCard("WSL2")
        self.card_core = _StatusCard("Core bioinformatics environment (bulkseq)")
        self.card_r = _StatusCard("R / DESeq2 stack")
        for card in (self.card_python, self.card_wsl, self.card_core, self.card_r):
            cards_layout.addWidget(card)
        cards_layout.addStretch(1)
        scroll.setWidget(card_host)
        root.addWidget(scroll, stretch=1)

        # Collapsible details / log section.
        details_row = QHBoxLayout()
        self.details_button = QPushButton("▸ Show details / log")
        self.details_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_button.setStyleSheet(
            "QPushButton {"
            f" color: {PRIMARY}; background: transparent; border: none;"
            " padding: 2px 0; font-size: 9.5pt; text-align: left;"
            "}"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self.details_button.clicked.connect(self._toggle_details)
        details_row.addWidget(self.details_button)
        details_row.addStretch(1)
        self.stop_install_button = QPushButton("Stop install")
        self.stop_install_button.setStyleSheet(_secondary_button_style())
        self.stop_install_button.clicked.connect(self.stop_wsl_install)
        self.stop_install_button.setVisible(False)
        self.log_button = QPushButton("Load setup log")
        self.log_button.setStyleSheet(_secondary_button_style())
        self.log_button.clicked.connect(self.show_setup_log)
        self.log_button.setVisible(False)
        details_row.addWidget(self.stop_install_button)
        details_row.addWidget(self.log_button)
        root.addLayout(details_row)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setVisible(False)
        self.text.setMinimumHeight(160)
        self.text.setStyleSheet(
            "QTextEdit {"
            f" background: {SURFACE}; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px;"
            " padding: 8px; font-family: Consolas, 'Cascadia Mono', monospace;"
            " font-size: 9pt;"
            "}"
        )
        root.addWidget(self.text)

        # Footer.
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = QPushButton("Continue")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setStyleSheet(_primary_button_style())
        self.close_button.clicked.connect(self.accept)
        footer.addWidget(self.close_button)
        root.addLayout(footer)

        self._details_visible = False
        self.refresh()

    # -- helpers -----------------------------------------------------------

    def _toggle_details(self) -> None:
        self._details_visible = not self._details_visible
        self.text.setVisible(self._details_visible)
        self.log_button.setVisible(self._details_visible)
        self.details_button.setText(
            "▾ Hide details / log" if self._details_visible else "▸ Show details / log"
        )

    def _log(self, message: str) -> None:
        self.text.append(message)
        if not self._details_visible:
            self._toggle_details()

    @staticmethod
    def _status_of(items: list[ReadinessItem], name: str) -> str:
        for item in items:
            if item.name == name:
                return item.status
        return "REVIEW_REQUIRED"

    @staticmethod
    def _detail_of(items: list[ReadinessItem], name: str) -> str:
        for item in items:
            if item.name == name:
                return item.detail
        return ""

    # -- refresh / state mapping ------------------------------------------

    def refresh(self) -> None:
        items = check_readiness()
        self._update_python_card()
        self._update_wsl_card(items)
        self._update_core_card(items)
        self._update_r_card(items)
        self._update_summary()
        # Keep the full machine-readable summary available in the log area.
        if self._details_visible:
            self.text.setPlainText(self._compose_details(items))

    def _ready_count(self) -> int:
        ready = 0
        for card in (self.card_python, self.card_wsl, self.card_core, self.card_r):
            if isinstance(card.pill, _StatusPill) and card.pill.text() == _PILL_TEXT[STATE_READY]:
                ready += 1
        return ready

    def _update_summary(self) -> None:
        ready = self._ready_count()
        if ready == 4:
            self.summary_label.setText("4 of 4 ready — setup complete")
            self.summary_label.setStyleSheet(
                f"color: {SUCCESS}; font-size: 10pt; font-weight: 600; background: transparent;"
            )
        else:
            self.summary_label.setText(f"{ready} of 4 ready")
            self.summary_label.setStyleSheet(
                f"color: {MUTED}; font-size: 10pt; background: transparent;"
            )

    def _update_python_card(self) -> None:
        missing = missing_python_packages()
        if missing:
            self.card_python.update_state(
                STATE_ACTION,
                "Missing GUI/core packages: " + ", ".join(missing) + ".",
                action_label="Install Python packages",
                action_handler=self.install_python_dependencies,
                action_enabled=not self._installing,
            )
        else:
            self.card_python.update_state(
                STATE_READY,
                "All Python GUI and core packages are installed.",
            )

    def _update_wsl_card(self, items: list[ReadinessItem]) -> None:
        wsl_ok = self._status_of(items, "wsl") == "PASS"
        micromamba_ok = self._status_of(items, "WSL micromamba") == "PASS"
        if not wsl_ok:
            self.card_wsl.update_state(
                STATE_ACTION,
                "WSL2 is not available. Installation opens an Administrator PowerShell "
                "window because Windows requires elevation; reboot if prompted.",
                action_label="Install / enable WSL",
                action_handler=self.install_wsl,
            )
        elif not micromamba_ok:
            self.card_wsl.update_state(
                STATE_ACTION,
                "WSL2 is available, but the micromamba package manager is not installed "
                "inside WSL yet.",
                action_label="Install micromamba in WSL",
                action_handler=self.install_wsl_bioenv,
                action_enabled=not self._installing,
            )
        else:
            self.card_wsl.update_state(
                STATE_READY,
                "WSL2 is available and the micromamba package manager is installed.",
            )

    def _update_core_card(self, items: list[ReadinessItem]) -> None:
        wsl_ok = self._status_of(items, "wsl") == "PASS"
        micromamba_ok = self._status_of(items, "WSL micromamba") == "PASS"
        if not (wsl_ok and micromamba_ok):
            self.card_core.update_state(
                STATE_CHECKING,
                "Waiting for WSL2 and micromamba before the bulkseq environment can be set up.",
            )
            return
        if has_wsl_core_environment(items):
            self.card_core.update_state(
                STATE_READY,
                "Snakemake, STAR, featureCounts, samtools, fastp, FastQC and MultiQC are "
                "installed in the bulkseq environment.",
            )
        else:
            self.card_core.update_state(
                STATE_ACTION,
                "The bulkseq environment is missing or incomplete. This installs Snakemake, "
                "SRA tools, FastQC, MultiQC, fastp, STAR, HISAT2, Salmon, featureCounts and "
                "samtools. It can take a while and may ask for your WSL sudo password.",
                action_label="Install / repair core environment",
                action_handler=self.install_wsl_bioenv,
                action_enabled=not self._installing,
            )

    def _update_r_card(self, items: list[ReadinessItem]) -> None:
        core_ready = has_wsl_core_environment(items)
        rscript_ok = self._status_of(items, "WSL Rscript") == "PASS"
        if rscript_ok:
            self.card_r.update_state(
                STATE_READY,
                "R with DESeq2 and enrichment packages is installed; differential expression "
                "and figures can run.",
            )
        elif not core_ready:
            self.card_r.update_state(
                STATE_OPTIONAL,
                "Adds R, DESeq2, enrichment and figure packages. Install the core environment "
                "first, then add this stack. Projects can still be configured without it.",
                action_label="Install full R/DESeq2 stack",
                action_handler=self.install_full_wsl_bioenv,
                action_enabled=False,
            )
        else:
            self.card_r.update_state(
                STATE_ACTION,
                "Adds R, DESeq2, enrichment and figure packages. This step can take much "
                "longer than the core environment.",
                action_label="Install full R/DESeq2 stack",
                action_handler=self.install_full_wsl_bioenv,
                action_enabled=not self._installing,
            )

    def _compose_details(self, items: list[ReadinessItem]) -> str:
        parts = [
            readiness_summary(items),
            "",
            "Next step",
            "---------",
            "\n".join(next_readiness_actions(items)),
            "",
            "Notes",
            "-----",
            "Windows PATH tools may stay REVIEW_REQUIRED when the tools live inside WSL; this is normal.",
            "WSL package setup is staged: first micromamba, then the core bulkseq environment, then the R/DESeq2 stack.",
            "WSL environment install logs are written to scripts/logs/wsl_bioenv_install.log.",
            "The GUI can create projects, edit metadata, estimate runtime, and generate configs before the WSL tools are installed.",
        ]
        return "\n".join(parts)

    # -- install actions (preserved behavior) -----------------------------

    def install_python_dependencies(self) -> None:
        requirements = app_root() / "requirements.txt"
        self._installing = True
        self.card_python.set_action_enabled(False)
        self._log(f"Installing Python dependencies from {requirements}…\n")
        self.install_thread = PipInstallThread(requirements)
        self.install_thread.line.connect(self.text.append)
        self.install_thread.finished_with_code.connect(self._install_finished)
        self.install_thread.start()

    def _install_finished(self, code: int) -> None:
        self._installing = False
        self._log(f"Python dependency installer finished with exit code {code}.\n")
        self.refresh()

    def install_wsl(self) -> None:
        self._log(
            "Opening Administrator PowerShell to install/enable WSL. Approve the Windows "
            "UAC prompt to continue.\n"
        )
        launch_wsl_admin_install()

    def install_wsl_bioenv(self) -> None:
        self._install_wsl_bioenv("core")

    def install_full_wsl_bioenv(self) -> None:
        self._install_wsl_bioenv("full")

    def _install_wsl_bioenv(self, profile: str) -> None:
        self._installing = True
        for card in (self.card_wsl, self.card_core, self.card_r):
            card.set_action_enabled(False)
        self.stop_install_button.setVisible(True)
        self.stop_install_button.setEnabled(True)
        self._log(
            f"Installing WSL bioinformatics environment profile: {profile}. This can take a "
            "long time and may ask for your WSL sudo password.\n"
        )
        self.wsl_install_thread = WslBioenvInstallThread(profile)
        self.wsl_install_thread.line.connect(self.text.append)
        self.wsl_install_thread.finished_with_code.connect(self._wsl_bioenv_finished)
        self.wsl_install_thread.start()

    def _wsl_bioenv_finished(self, code: int) -> None:
        self._installing = False
        self._log(f"WSL bioinformatics installer finished with exit code {code}.\n")
        self.stop_install_button.setEnabled(False)
        self.stop_install_button.setVisible(False)
        self.refresh()

    def stop_wsl_install(self) -> None:
        if self.wsl_install_thread is not None:
            self._log("Stopping WSL bioinformatics installer…\n")
            self.wsl_install_thread.stop()

    def show_setup_log(self) -> None:
        log_path = app_root() / "scripts" / "logs" / "wsl_bioenv_install.log"
        if not self._details_visible:
            self._toggle_details()
        if not log_path.exists():
            self.text.append(f"\nNo WSL bioinformatics setup log found yet: {log_path}\n")
            return
        self.text.setPlainText(log_path.read_text(encoding="utf-8", errors="replace"))
