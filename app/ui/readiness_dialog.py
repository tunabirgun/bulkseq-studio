from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.constants import APP_NAME
from app.core.paths import app_root
from app.ui import theme
from app.core.readiness import (
    ReadinessItem,
    check_readiness,
    has_native_core_environment,
    has_wsl_core_environment,
    install_python_packages,
    missing_python_packages,
    next_readiness_actions,
    readiness_summary,
)
from app.core.setup_installer import (
    launch_native_bioenv_install,
    launch_wsl_admin_install,
    launch_wsl_bioenv_install,
)

# ---------------------------------------------------------------------------
# Design tokens: light-theme defaults matching app.ui.theme's LIGHT_PALETTE. _use_mode()
# rebinds these to the active (light or dark) palette when the dialog opens, so Check
# Environment follows the app's theme selection instead of staying a fixed light dialog.
# ---------------------------------------------------------------------------

PRIMARY = "#2C6FB6"
PRIMARY_HOVER = "#2560A0"
PRIMARY_PRESSED = "#1E4F86"
ON_PRIMARY = "#FFFFFF"
PRIMARY_DISABLED_BG = "#9FBEDD"
PRIMARY_DISABLED_TEXT = "#3F4D5A"
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


def _current_mode() -> str:
    # Match the app's light/dark selection (same QSettings key as MainWindow._current_theme_mode)
    # so Check Environment is not a fixed light dialog over a dark app.
    mode = str(QSettings().value("theme_mode", "light"))
    return mode if mode in theme.PALETTES else "light"


def _use_mode(mode: str) -> None:
    # Rebind this module's color tokens to the active theme palette. Every color above is read
    # from a module global at widget-build time, so rebinding here — before the dialog builds its
    # widgets — reskins the whole dialog to the chosen theme. _PILL_COLOR is a literal, so it is
    # rebuilt from the same palette. Called at the top of ReadinessDialog.__init__.
    p = theme.PALETTES.get(mode, theme.LIGHT_PALETTE)
    g = globals()
    g["PRIMARY"], g["PRIMARY_HOVER"], g["PRIMARY_PRESSED"] = p["PRIMARY"], p["PRIMARY_HOVER"], p["PRIMARY_PRESSED"]
    # ON_PRIMARY (text on a filled primary button) and the disabled pair must follow the theme
    # too: in dark mode PRIMARY is light blue, so white-on-blue fails WCAG-AA — the dark palette's
    # ON_PRIMARY is near-black for contrast (matches the app's real primary buttons).
    g["ON_PRIMARY"] = p["ON_PRIMARY"]
    g["PRIMARY_DISABLED_BG"], g["PRIMARY_DISABLED_TEXT"] = p["PRIMARY_DISABLED_BG"], p["PRIMARY_DISABLED_TEXT"]
    g["BACKGROUND"], g["SURFACE"], g["BORDER"] = p["BACKGROUND"], p["SURFACE"], p["BORDER"]
    g["TEXT"], g["MUTED"] = p["TEXT"], p["MUTED_TEXT"]
    g["SUCCESS"], g["WARNING"], g["ERROR"], g["REVIEW"] = p["SUCCESS"], p["WARNING"], p["ERROR"], p["REVIEW"]
    g["_PILL_COLOR"] = {
        STATE_READY: p["SUCCESS"], STATE_ACTION: p["WARNING"],
        STATE_OPTIONAL: p["REVIEW"], STATE_CHECKING: p["MUTED_TEXT"],
    }


def _make_status_pill(state: str) -> QLabel:
    """Build a colored status pill.

    Uses the self-contained _StatusPill, which colors by semantic state from the
    shared palette (ready=green, action=amber, ...). A theme factory that only
    receives the display text cannot color by status, so it is intentionally not
    used here; visual consistency comes from sharing the same palette constants.
    """
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

    def __init__(self, profile: str = "core", native: bool = False, rebuild: bool = False) -> None:
        super().__init__()
        self.profile = profile
        self.native = native
        self.rebuild = rebuild
        self.process = None

    def run(self) -> None:
        if self.native:
            self.process = launch_native_bioenv_install(profile=self.profile, rebuild=self.rebuild)
        else:
            self.process = launch_wsl_bioenv_install(profile=self.profile, rebuild=self.rebuild)
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
        f" color: {ON_PRIMARY};"
        " border: none;"
        " border-radius: 6px;"
        " padding: 7px 16px;"
        " font-size: 9.5pt;"
        " font-weight: 600;"
        "}"
        f"QPushButton:hover {{ background: {PRIMARY_HOVER}; }}"
        f"QPushButton:pressed {{ background: {PRIMARY_PRESSED}; }}"
        f"QPushButton:disabled {{ background: {PRIMARY_DISABLED_BG}; color: {PRIMARY_DISABLED_TEXT}; }}"
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
        f"QPushButton:disabled {{ color: {PRIMARY_DISABLED_TEXT}; }}"
    )


# ---------------------------------------------------------------------------
# Dialog.
# ---------------------------------------------------------------------------


class ReadinessCheckThread(QThread):
    """Runs the (blocking) environment probes off the UI thread so the dialog can
    show a moving progress bar instead of freezing while WSL/conda are queried."""

    done = Signal(list)

    def run(self) -> None:
        try:
            items = check_readiness()
        except Exception:
            items = []
        self.done.emit(items)


class ReadinessDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Reskin this dialog to the app's current light/dark theme before building any widgets.
        _use_mode(_current_mode())
        self.setWindowTitle(f"{APP_NAME} Setup")
        self.resize(720, 640)
        self.setStyleSheet(f"QDialog {{ background: {BACKGROUND}; font-family: {BASE_FONT}; }}")

        self.install_thread: PipInstallThread | None = None
        self.wsl_install_thread: WslBioenvInstallThread | None = None
        self._check_thread: ReadinessCheckThread | None = None
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

        # Indeterminate "busy" bar shown while the (threaded) checks run.
        self.check_progress = QProgressBar()
        self.check_progress.setRange(0, 0)
        self.check_progress.setTextVisible(False)
        self.check_progress.setFixedHeight(6)
        self.check_progress.setVisible(False)
        self.check_progress.setStyleSheet(
            f"QProgressBar {{ background: {SURFACE}; border: 1px solid {BORDER};"
            f" border-radius: 3px; }} QProgressBar::chunk {{ background: {PRIMARY};"
            " border-radius: 3px; }"
        )
        root.addWidget(self.check_progress)

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
        # WSL is a Windows-only execution path. On Linux the pipeline runs natively, so
        # hide the WSL2 card and count readiness against the remaining cards.
        self._is_windows = sys.platform.startswith("win")
        if not self._is_windows:
            self.card_wsl.setVisible(False)
        self._active_cards = (
            (self.card_python, self.card_wsl, self.card_core, self.card_r)
            if self._is_windows
            else (self.card_python, self.card_core, self.card_r)
        )
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

        # Footer: a persistent Repair-environment action (reinstalls the full env to fix
        # a missing or partial tool the per-card "ready" gate does not cover), plus Continue.
        footer = QHBoxLayout()
        self.repair_button = QPushButton("Repair environment")
        self.repair_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.repair_button.setStyleSheet(_secondary_button_style())
        self.repair_button.setToolTip(
            "Reinstall / update the full bioinformatics environment (all tools and the R/DESeq2 "
            "stack). Use this if a run reports a tool as missing even though setup looks ready."
        )
        self.repair_button.clicked.connect(self.repair_environment)
        footer.addWidget(self.repair_button)
        self.rebuild_button = QPushButton("Rebuild from scratch")
        self.rebuild_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rebuild_button.setStyleSheet(_secondary_button_style())
        self.rebuild_button.setToolTip(
            "Delete the bulkseq environment and recreate it cleanly. Use this if a run fails "
            "inside R/DESeq2 or the microarray GEO ingest after an app update — an in-place "
            "update can leave the R/Bioconductor packages inconsistent. Re-downloads the tools."
        )
        self.rebuild_button.clicked.connect(self.rebuild_environment)
        footer.addWidget(self.rebuild_button)
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

    def closeEvent(self, event) -> None:
        # Stop background threads before the widget is destroyed, or their signals
        # fire into deleted slots ("underlying C/C++ object has been deleted").
        for thread, stopper in (
            (self._check_thread, "quit"),
            (self.install_thread, "quit"),
            (self.wsl_install_thread, "stop"),
        ):
            if thread is not None and thread.isRunning():
                getattr(thread, stopper)()
                thread.wait(5000)
        super().closeEvent(event)

    def refresh(self) -> None:
        # Run the blocking probes off-thread so the dialog opens instantly and the
        # busy bar moves while WSL/conda are queried, instead of freezing.
        if self._check_thread is not None and self._check_thread.isRunning():
            return
        # Drop a stale connection so a thread finishing in the gap can't double-fire.
        try:
            self._check_thread.done.disconnect(self._on_check_done)
        except (RuntimeError, TypeError, AttributeError):
            pass
        self.check_progress.setVisible(True)
        self.summary_label.setText("Checking requirements…")
        self.refresh_button.setEnabled(False)
        self._check_thread = ReadinessCheckThread()
        self._check_thread.done.connect(self._on_check_done)
        self._check_thread.start()

    def _on_check_done(self, items: list[ReadinessItem]) -> None:
        self.check_progress.setVisible(False)
        self.refresh_button.setEnabled(True)
        self._update_python_card()
        if self._is_windows:
            self._update_wsl_card(items)
        self._update_core_card(items)
        self._update_r_card(items)
        self._update_summary()
        # Keep the full machine-readable summary available in the log area.
        if self._details_visible:
            self.text.setPlainText(self._compose_details(items))
        # A broken R/Bioconductor stack clears the version-scoped first-run prompt stamp so the
        # next app launch re-opens this check — a broken env should keep being nudged until it is
        # repaired, not silenced after one dismissal.
        if any(it.name in ("WSL R packages", "R packages") and it.status != "PASS" for it in items):
            QSettings().remove("env_check_prompted_version")

    def _ready_count(self) -> int:
        ready = 0
        for card in self._active_cards:
            if isinstance(card.pill, _StatusPill) and card.pill.text() == _PILL_TEXT[STATE_READY]:
                ready += 1
        return ready

    def _update_summary(self) -> None:
        ready = self._ready_count()
        total = len(self._active_cards)
        if ready == total:
            self.summary_label.setText(f"{ready} of {total} ready — setup complete")
            self.summary_label.setStyleSheet(
                f"color: {SUCCESS}; font-size: 10pt; font-weight: 600; background: transparent;"
            )
        else:
            self.summary_label.setText(f"{ready} of {total} ready")
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
        if not self._is_windows:
            if has_native_core_environment(items):
                self.card_core.update_state(
                    STATE_READY,
                    "Snakemake, STAR, featureCounts, samtools, fastp, FastQC and MultiQC are on "
                    "PATH in the local environment.",
                )
            else:
                missing = [n for n in ("snakemake", "STAR", "featureCounts", "samtools", "fastp",
                                       "fastqc", "multiqc") if self._status_of(items, n) != "PASS"]
                self.card_core.update_state(
                    STATE_ACTION,
                    "Not on PATH: " + ", ".join(missing) + ". Activate the bulkseq environment "
                    "(e.g. micromamba activate bulkseq) or create it from "
                    "workflow/envs/bulkseq.lock.yaml, then re-check.",
                )
            return
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
                "samtools. It can take a while and runs in your WSL user account — no sudo "
                "password needed.",
                action_label="Install / repair core environment",
                action_handler=self.install_wsl_bioenv,
                action_enabled=not self._installing,
            )

    def _update_r_card(self, items: list[ReadinessItem]) -> None:
        if not self._is_windows:
            r_ok = self._status_of(items, "Rscript") == "PASS"
            pkgs_ok = self._status_of(items, "R packages") == "PASS"
            if r_ok and pkgs_ok:
                self.card_r.update_state(
                    STATE_READY,
                    "Rscript and the R analysis packages are installed; DESeq2, enrichment and figures can run.",
                )
            elif not r_ok:
                self.card_r.update_state(
                    STATE_ACTION,
                    "Rscript is not on PATH. Install the R/DESeq2 stack into the environment so "
                    "DESeq2, enrichment and figures can run.",
                )
            else:
                self.card_r.update_state(
                    STATE_ACTION,
                    "Rscript is on PATH but some required R packages are missing; install/repair the "
                    "R/DESeq2 stack so DESeq2, enrichment and figures can run.",
                )
            return
        core_ready = has_wsl_core_environment(items)
        # Both the binary AND the analysis packages must be present — a bare Rscript with missing
        # Bioconductor packages would still fail DESeq2/enrichment (and is exactly how an error-127
        # or a mid-run R failure slips past a green-looking card).
        rscript_ok = (self._status_of(items, "WSL Rscript") == "PASS"
                      and self._status_of(items, "WSL R packages") == "PASS")
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
        if self._installing:
            return
        requirements = app_root() / "requirements.txt"
        self._installing = True
        self.card_python.set_action_enabled(False)
        self.repair_button.setEnabled(False)
        self.rebuild_button.setEnabled(False)
        self._log(f"Installing Python dependencies from {requirements}…\n")
        self.install_thread = PipInstallThread(requirements)
        self.install_thread.line.connect(self.text.append)
        self.install_thread.finished_with_code.connect(self._install_finished)
        self.install_thread.start()

    def _install_finished(self, code: int) -> None:
        self._installing = False
        self.repair_button.setEnabled(True)
        self.rebuild_button.setEnabled(True)
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

    def repair_environment(self) -> None:
        # Full reinstall/update — the catch-all repair. On a Windows box the environment
        # lives in WSL; on Linux it is native, so run the setup script directly there.
        self._install_wsl_bioenv("full")

    def rebuild_environment(self) -> None:
        # Clean rebuild: delete the env and recreate it from scratch. Fixes an
        # inconsistent R/Bioconductor stack left by in-place updates across versions
        # (the class of failure where the microarray GEO ingest or DESeq2 dies on load).
        reply = QMessageBox.question(
            self, APP_NAME,
            "Rebuild the bioinformatics environment from scratch?\n\n"
            "This deletes the existing 'bulkseq' environment and recreates it cleanly, then "
            "re-downloads and reinstalls every tool and the R/DESeq2 stack. Use it if a run "
            "fails inside R (DESeq2 or the microarray GEO ingest) after an app update — an "
            "in-place update can leave the R packages inconsistent. It can take a while.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._install_wsl_bioenv("full", rebuild=True)

    def _install_wsl_bioenv(self, profile: str, rebuild: bool = False) -> None:
        if self._installing:
            return
        self._installing = True
        for card in (self.card_wsl, self.card_core, self.card_r):
            card.set_action_enabled(False)
        self.repair_button.setEnabled(False)
        self.rebuild_button.setEnabled(False)
        self.stop_install_button.setVisible(True)
        self.stop_install_button.setEnabled(True)
        where = "your WSL user account" if self._is_windows else "the local micromamba environment"
        action = "Rebuilding (delete + recreate)" if rebuild else "Installing"
        self._log(
            f"{action} the bioinformatics environment (profile: {profile}). This can take a long "
            f"time and installs into {where}; it does not need a sudo password.\n"
        )
        self.wsl_install_thread = WslBioenvInstallThread(profile, native=not self._is_windows, rebuild=rebuild)
        self.wsl_install_thread.line.connect(self.text.append)
        self.wsl_install_thread.finished_with_code.connect(self._wsl_bioenv_finished)
        self.wsl_install_thread.start()

    def _wsl_bioenv_finished(self, code: int) -> None:
        self._installing = False
        self._log(f"WSL bioinformatics installer finished with exit code {code}.\n")
        if code != 0:
            self._log(
                "Setup did not finish cleanly. Open \"Show details / log\" and look for an "
                "ACTION REQUIRED note near the end of the log — it lists the exact WSL command "
                "to run if a prerequisite must be installed manually.\n"
            )
        self.stop_install_button.setEnabled(False)
        self.stop_install_button.setVisible(False)
        self.repair_button.setEnabled(True)
        self.rebuild_button.setEnabled(True)
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
