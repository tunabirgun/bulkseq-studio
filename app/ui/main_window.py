from __future__ import annotations

import shutil
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml
from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QSettings,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
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
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import (
    QDesktopServices,
    QFontDatabase,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QShortcut,
)
from PySide6.QtCore import Qt, QUrl

from app.constants import APP_NAME, APP_VERSION, MIN_UNIQUE_MAPPED_WARN_PCT
from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.config_models import (
    AppConfig,
    Deseq2ResultsDirectionProvenance,
    Deseq2ResultsFileProvenance,
)
from app.core.de_results import (
    DETableValidationError,
    ExternalDEImportDetails,
    provenance_payload,
    validate_de_results_table,
    validate_recorded_project_copy,
)
from app.core.input_detection import detect_fastq_inputs
from app.core.metadata import (
    dataframe_from_rows,
    load_metadata,
    read_user_table,
    save_metadata,
    validate_metadata,
)
from app.core.project import (
    ProjectExistsError,
    ProjectManager,
    decimal_comma_warnings,
    is_project_root,
    validate_working_directory,
)
from app.core.provenance import write_run_summary
from app.core.preflight import (
    validate_current_preflight,
    write_input_validation_with_fingerprint,
)
from app.core.reference_manager import catalog_entry_for_organism, load_reference_catalog, md5sum, validate_reference
from app.core.resources import detect_system, recommend_profile, recommend_rule_threads
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
from app.core.paths import (
    data_path,
    is_wsl_unc_path,
    project_configured_path,
    windows_to_wsl_path,
    wsl_recommended_workdir,
    wsl_unc_distro,
    wsl_vhdx_basepath,
)
from app.ui.image_viewer import SVG_AVAILABLE, ImageViewer
from app.ui.metadata_editor import MetadataTable
from app.ui.readiness_dialog import ReadinessDialog
from app.ui.task_navigator import TaskNavigator
from app.ui.theme import IMAGEVIEWER_BG, PALETTES, STATUS_PILL_BG, apply_theme, status_color


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


class _PlainTextLabel(QLabel):
    """Selectable status copy with the small QTextEdit-compatible API we use."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def setPlainText(self, text: str) -> None:
        self.setText(text)

    def toPlainText(self) -> str:
        return self.text()

    def append(self, text: str) -> None:
        current = self.text()
        self.setText(f"{current}\n{text}" if current else text)


class _InsetStatusBar(QStatusBar):
    """Status bar whose transient message respects the application's edge grid.

    Qt paints ``showMessage`` text outside the status bar's child layout, so
    layout margins and stylesheet padding do not move it.  This keeps the same
    public API while painting the message inside ``contentsRect``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._message = ""
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self.clearMessage)
        self.setAccessibleName("Application status")

    def showMessage(self, message: str, timeout: int = 0) -> None:  # noqa: N802 - Qt API
        message = str(message)
        changed = message != self._message
        self._message = message
        self._clear_timer.stop()
        if timeout > 0:
            self._clear_timer.start(timeout)
        if changed:
            self.messageChanged.emit(message)
        self.update()

    def clearMessage(self) -> None:  # noqa: N802 - Qt API
        self._clear_timer.stop()
        if not self._message:
            return
        self._message = ""
        self.messageChanged.emit("")
        self.update()

    def currentMessage(self) -> str:  # noqa: N802 - Qt API
        return self._message

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual name
        super().paintEvent(event)
        if not self._message:
            return
        painter = QPainter(self)
        # This is persistent application status, not placeholder copy.  The
        # PlaceholderText role can be intentionally faint and made completed-run
        # guidance fail normal-text contrast; use the ordinary text role.
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        text_rect = self.contentsRect()
        # Leave the native resize grip clear without moving the left edge.
        text_rect.adjust(0, 0, -24, 0)
        text = self.fontMetrics().elidedText(
            self._message, Qt.TextElideMode.ElideRight, max(1, text_rect.width()))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )


class _InspectorTabs(QTabWidget):
    """Compact, clearly navigational tabs for a narrow results inspector.

    The previous QToolBox treatment left inactive section headers at the bottom
    of a tall panel.  Against the input palette those headers looked like empty
    text fields.  A single tab row keeps the same one-panel-at-a-time behaviour
    while making the navigation role explicit.

    The small ``item*`` aliases preserve the former QToolBox call sites and test
    helpers while the UI uses QTabWidget semantics.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("uiRole", "inspectorTabs")
        self.setDocumentMode(True)
        self.tabBar().setExpanding(True)
        self.tabBar().setUsesScrollButtons(False)
        self.setElideMode(Qt.TextElideMode.ElideRight)

    def addItem(self, widget: QWidget, label: str) -> int:
        return self.addTab(widget, label)

    def itemText(self, index: int) -> str:
        return self.tabText(index)

    def setItemToolTip(self, index: int, text: str) -> None:
        self.setTabToolTip(index, text)

    def itemToolTip(self, index: int) -> str:
        return self.tabToolTip(index)


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
        self._launch_preflight_worker: BackgroundWorker | None = None
        self._phase_refresh_worker: BackgroundWorker | None = None
        self._launch_preflight_ui_state: dict[str, object] | None = None
        self.run_action_buttons: dict[str, QPushButton] = {}
        self.stop_button: QPushButton | None = None
        self._workflow_height_update_pending = False

        self.tabs = TaskNavigator()
        self.setCentralWidget(self.tabs)
        self.setStatusBar(_InsetStatusBar(self))
        # The window owns its minimum so the size contract holds even under direct
        # construction (tests), and the restore-geometry size guard has a real bound.
        self.setMinimumSize(900, 600)
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
        # Every tab exists now, so the form-width pass can see all of them at once.
        self._tune_form_widths()
        # A small status bar at the bottom for transient feedback (e.g. resource
        # detection), so blocking actions show progress instead of looking frozen.
        # The environment check is on-demand (the 'Check Environment' button) so the
        # window opens instantly instead of blocking on WSL/conda probes at startup.
        self.statusBar().setContentsMargins(10, 2, 10, 3)
        if not sys.platform.startswith("win"):
            self.statusBar().showMessage(
                "Ready — create or open a project. Before the first run, use Check Environment "
                "to verify the bioinformatics tools."
            )
        elif shutil.which("wsl") is None:
            self.statusBar().showMessage(
                "WSL2 was not detected. Open Project and select Check Environment before running."
            )
        else:
            self.statusBar().showMessage(
                "Ready — create or open a project. Use Check Environment before the first run."
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
        # Persist first: every widget-level repaint below resolves the current mode
        # through QSettings.  Applying the palette before this write repainted those
        # widgets with the old theme and made a switch look half-finished.
        QSettings().setValue("theme_mode", new_mode)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode=new_mode)
        # ReadinessDialog owns a few semantic card/pill styles that cannot inherit
        # the application palette. Repaint an already-open dialog in place so the
        # mode switch is complete without restarting or losing check state.
        try:
            if self.readiness_dialog is not None and self.readiness_dialog.isVisible():
                self.readiness_dialog.apply_theme(new_mode)
        except RuntimeError:
            self.readiness_dialog = None
        self._sync_theme_toggle(new_mode)
        # A QGraphicsScene ignores widget QSS, so repaint the viewer background.
        if hasattr(self, "figure_viewer"):
            self.figure_viewer.update_theme(IMAGEVIEWER_BG.get(new_mode, IMAGEVIEWER_BG["light"]))
        # A QWebEngineView ignores app QSS too; push the palette into the page.
        if hasattr(self, "ppi_viewer"):
            self.ppi_viewer.update_theme(self._ppi_theme_palette())
        # Widget-level stylesheets are not regenerated by apply_theme; repaint the
        # ones that carry a palette colour so they do not keep the old theme's hex.
        self._repaint_themed_labels(new_mode)

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
                s.setValue(f"outputs/v3/{key}", sp.saveState())

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
        for attr in ("_sanity_worker", "_phase_refresh_worker", "_launch_preflight_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
        # Let short-lived background probes finish so QThread isn't destroyed while
        # running (which would crash). These are bounded WSL/resource queries.
        for attr in ("_wsl_autodetect_worker", "_wsl_workdir_worker", "_detect_worker",
                     "_geo_worker", "_sra_worker", "_reports_worker", "_estimate_worker",
                     "_sanity_worker", "_phase_refresh_worker", "_launch_preflight_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.wait(3000)
        if self.runner_thread is not None and self.runner_thread.isRunning():
            self.runner_thread.wait(5000)
        super().closeEvent(event)

    # A settings form has no reason to grow past a readable measure. Without this the
    # cards stretch to the window edge, so a combo holding "STAR" renders ~1350px wide
    # on a 1600px display. Left-aligned rather than centred: a form is read from the
    # left margin, and centring strands it between two dead bands on a wide window.
    CONTENT_MAX_WIDTH = 1240
    FIELD_MAX_WIDTH = 460

    def _scrollable(self, page: QWidget) -> QScrollArea:
        # Wrap a tall form page so the window can shrink below the page's natural
        # height; the page scrolls instead of forcing a large minimum window size.
        page.setMaximumWidth(self.CONTENT_MAX_WIDTH)
        # Expanding so the cards fill the content column rather than shrinking to
        # their own size hint; the max width above is what actually bounds them, and
        # the trailing stretch absorbs whatever the window has beyond it.
        page.setSizePolicy(QSizePolicy.Policy.Expanding, page.sizePolicy().verticalPolicy())
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        # Stretch 1 on the page, 0 on the spacer: extra width goes to the page until
        # it hits CONTENT_MAX_WIDTH, and only the surplus beyond that falls to the
        # spacer. The other way round, the spacer wins and the page stays at its
        # size hint, leaving the cards floating in a narrow column.
        row.addWidget(page, 1)
        row.addStretch(0)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _inspector_scrollable(self, page: QWidget) -> QScrollArea:
        """Wrap a narrow inspector without inheriting a desktop form's minimum width.

        Inspectors sit beside a preview, so their labels must wrap inside the
        available column. A generic holder retained the form's size hint and could
        clip controls behind the inspector even while both scrollbars reported zero.
        """
        page.setMinimumWidth(0)
        page.setMaximumWidth(16777215)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for combo in page.findChildren(QComboBox):
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, combo.sizePolicy().verticalPolicy())
        for checkbox in page.findChildren(QCheckBox):
            checkbox.setMinimumWidth(0)
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, checkbox.sizePolicy().verticalPolicy())
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Reserve the QTabWidget frame overlap so focus/ensureWidgetVisible never
        # leaves the final control partly hidden behind the inspector's action row.
        scroll.setViewportMargins(0, 0, 0, 14)
        scroll.setMinimumWidth(0)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        return scroll

    def _page_intro(self, title: str, purpose: str) -> QFrame:
        """Build the single, restrained heading treatment used by every task page."""
        card = QFrame()
        card.setObjectName("pageIntro")
        card.setProperty("uiRole", "pageIntro")
        card.setAccessibleName(title)
        intro_layout = QVBoxLayout(card)
        intro_layout.setContentsMargins(12, 8, 12, 8)
        intro_layout.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("pageTitleText")
        heading.setProperty("uiRole", "pageTitle")
        body = QLabel(purpose)
        body.setObjectName("pagePurposeText")
        body.setProperty("uiRole", "pagePurpose")
        body.setWordWrap(True)
        intro_layout.addWidget(heading)
        intro_layout.addWidget(body)
        return card

    def _empty_state_panel(
        self,
        title: str,
        body: str,
        action_text: str | None = None,
        action=None,
    ) -> tuple[QWidget, QLabel, QLabel, QPushButton | None]:
        """Create a bounded empty state instead of leaving instructions loose on a canvas."""
        wrapper = QWidget()
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addStretch(1)
        card = QFrame()
        card.setProperty("uiRole", "emptyState")
        card.setMinimumWidth(380)
        card.setMaximumWidth(560)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(8)
        heading = QLabel(title)
        heading.setProperty("uiRole", "emptyTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(body)
        text.setProperty("uiRole", "emptyBody")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setWordWrap(True)
        # QLabel's size hint underestimates wrapped copy until the card has its
        # final width.  Reserve two text lines so longer empty-state guidance is
        # never compressed underneath the action button at compact sizes.
        if action_text:
            text.setMinimumHeight(40)
        card_layout.addWidget(heading)
        card_layout.addWidget(text)
        button: QPushButton | None = None
        if action_text:
            button = QPushButton(action_text)
            if action is not None:
                button.clicked.connect(action)
            action_row = QHBoxLayout()
            action_row.addStretch(1)
            action_row.addWidget(button)
            action_row.addStretch(1)
            card_layout.addLayout(action_row)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        return wrapper, heading, text, button

    def _section_panel(
        self,
        title: str,
        hint: str | None = None,
    ) -> tuple[QFrame, QVBoxLayout]:
        """Build a content-sized section without a fieldset title notch."""
        section = QFrame()
        section.setProperty("uiRole", "section")
        section.setAccessibleName(title)
        section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(8, 8, 8, 10)
        section_layout.setSpacing(7)
        heading = QLabel(title)
        heading.setProperty("uiRole", "sectionTitle")
        section_layout.addWidget(heading)
        if hint:
            description = QLabel(hint)
            description.setProperty("uiRole", "sectionHint")
            description.setWordWrap(True)
            section_layout.addWidget(description)
        return section, section_layout

    def _disclosure(
        self,
        title: str,
        *,
        expanded: bool = False,
    ) -> tuple[QToolButton, QWidget]:
        """Create a keyboard-accessible native disclosure and its content pane."""
        toggle = QToolButton()
        toggle.setText(title)
        toggle.setAccessibleName(title)
        toggle.setProperty("uiRole", "disclosure")
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        content = QWidget()
        content.setProperty("uiRole", "disclosureContent")
        content.setVisible(expanded)

        def update_disclosure(checked: bool) -> None:
            toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            content.setVisible(checked)

        toggle.toggled.connect(update_disclosure)
        return toggle, content

    def _tune_form_widths(self) -> None:
        """Stop form fields from stretching to the window edge.

        Qt's default FieldGrowthPolicy on Windows/Linux is AllNonFixedFieldsGrow, so
        every expanding field fills its row. Measured before this call: 57 buttons
        wider than 400px, and a unit combo holding "in" (54px size hint) rendering at
        756px. Each control is capped just past its own content instead.
        """
        # The field column keeps Qt's growing policy on purpose: a path field, a log
        # pane and a status box all want the column's width. What gets capped is the
        # individual controls whose content is short and bounded, so a combo holding
        # "STAR" no longer occupies the row a file path needs.
        for form in self.findChildren(QFormLayout):
            if form.property("narrowInspector"):
                form.setHorizontalSpacing(10)
                form.setVerticalSpacing(6)
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            elif form.property("compactColumns"):
                form.setHorizontalSpacing(12)
                form.setVerticalSpacing(6)
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
            else:
                form.setHorizontalSpacing(16)
                form.setVerticalSpacing(10)
                form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        for combo in self.findChildren(QComboBox):
            # Size to the widest entry the combo can actually show, not to the row.
            metrics = combo.fontMetrics()
            widths = [metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())]
            widest = max(widths) if widths else metrics.horizontalAdvance(combo.currentText())
            combo.setMaximumWidth(min(self.FIELD_MAX_WIDTH, max(160, widest + 64)))
        for spin in self.findChildren(QAbstractSpinBox):
            spin.setMaximumWidth(190)
        for button in self.findChildren(QPushButton):
            # A button stretched across the row reads as a banner or a text field
            # rather than something to click; hold it at its natural width.
            button.setMaximumWidth(max(140, button.sizeHint().width() + 32))

    def _build_project_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(self._page_intro(
            "Start or open a project",
            "A project keeps the sample sheet, workflow settings, checks and results together. "
            "Create a new project, open an existing one, or start from the public benchmark."))

        setup_group = QGroupBox("Project location")
        setup_form = QFormLayout(setup_group)
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
        workdir_hint.setProperty("hint", True)
        setup_form.addRow("Project name", self.project_name)
        setup_form.addRow("Working directory", workdir_row)
        setup_form.addRow("", workdir_hint)
        layout.addWidget(setup_group)

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
        primary_actions = QGridLayout()
        primary_actions.setHorizontalSpacing(8)
        primary_actions.setVerticalSpacing(6)
        primary_actions.addWidget(create, 0, 0)
        primary_actions.addWidget(open_existing, 0, 1)
        primary_actions.addWidget(benchmark, 1, 0)
        primary_actions.addWidget(readiness, 1, 1)
        primary_actions.setColumnStretch(2, 1)
        layout.addLayout(primary_actions)

        recent_group = QGroupBox("Recent projects")
        recent_layout = QHBoxLayout(recent_group)
        self.recent_pick = QComboBox()
        self.recent_pick.setToolTip("Projects you have opened before.")
        self.recent_open = QPushButton("Open recent")
        self.recent_open.clicked.connect(self._open_recent_project)
        self.recent_empty_label = QLabel("No recent projects yet.")
        self.recent_empty_label.setProperty("hint", True)
        recent_layout.addWidget(self.recent_pick)
        recent_layout.addWidget(self.recent_open)
        recent_layout.addWidget(self.recent_empty_label)
        recent_layout.addStretch(1)
        layout.addWidget(recent_group)

        status_group = QGroupBox("Project status and next step")
        status_layout = QVBoxLayout(status_group)
        status_banner = QFrame()
        status_banner.setProperty("uiRole", "statusBanner")
        status_banner_layout = QVBoxLayout(status_banner)
        status_banner_layout.setContentsMargins(12, 8, 12, 8)
        self.project_status = _PlainTextLabel(
            "Create a new project or open an existing one. Status and next steps appear here.")
        status_banner_layout.addWidget(self.project_status)
        status_layout.addWidget(status_banner)
        layout.addWidget(status_group)
        layout.addStretch(1)
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
        has_recent = bool(recent)
        self.recent_pick.setVisible(has_recent)
        self.recent_open.setVisible(has_recent)
        self.recent_empty_label.setVisible(not has_recent)

    def _open_recent_project(self) -> None:
        path = self.recent_pick.currentText().strip() if hasattr(self, "recent_pick") else ""
        if path:
            self._load_project(Path(path))

    def _build_input_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(self._page_intro(
            "Choose an input route",
            "Start from public sequencing accessions, local FASTQ files, processed RNA-seq tables, "
            "or a microarray dataset. Each route keeps only the controls relevant to that input."))

        routes = QTabWidget()
        routes.setObjectName("inputRouteTabs")
        routes.setMinimumHeight(250)
        routes.setMaximumHeight(360)
        routes.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.input_route_tabs = routes

        # Public sequencing accessions (SRA / ENA).
        public_page = QWidget()
        public_layout = QVBoxLayout(public_page)
        public_layout.setContentsMargins(10, 10, 10, 10)
        public_hint = QLabel(
            "Paste run or study accessions. Metadata is fetched from ENA; suggested condition "
            "groups must still be reviewed on the Metadata page before differential expression.")
        public_hint.setWordWrap(True)
        public_layout.addWidget(public_hint)
        self.sra_box = QTextEdit()
        # Paste as plain text: strip any source formatting (fonts/colours/links) so pasted
        # accessions come in clean.
        self.sra_box.setAcceptRichText(False)
        self.sra_box.setPlaceholderText("Paste SRR/ERR/DRR runs, or an SRP/PRJNA/GSE study accession, one per line")
        self.sra_box.setMinimumHeight(105)
        self.sra_box.setMaximumHeight(150)
        public_layout.addWidget(self.sra_box)
        public_actions = QHBoxLayout()
        fetch_meta = QPushButton("Fetch metadata && build samples")
        fetch_meta.setProperty("primary", True)
        fetch_meta.clicked.connect(self._fetch_sra_metadata)
        save_sra = QPushButton("Save accessions only")
        save_sra.clicked.connect(self._save_sra)
        public_actions.addWidget(fetch_meta)
        public_actions.addWidget(save_sra)
        public_actions.addStretch(1)
        public_layout.addLayout(public_actions)
        public_layout.addStretch(1)
        routes.addTab(public_page, "Public accessions")

        # Local raw sequencing files.
        fastq_page = QWidget()
        fastq_layout = QVBoxLayout(fastq_page)
        fastq_layout.setContentsMargins(10, 10, 10, 10)
        fastq_hint = QLabel(
            "Select local single-end or paired-end FASTQ files. BulkSeq Studio detects sample "
            "names and pairing, then creates the sample sheet for review.")
        fastq_hint.setWordWrap(True)
        pick_fastq = QPushButton("Select FASTQ Files")
        pick_fastq.setProperty("primary", True)
        pick_fastq.clicked.connect(self._select_fastqs)
        fastq_action = QHBoxLayout()
        fastq_action.addWidget(pick_fastq)
        fastq_action.addStretch(1)
        fastq_layout.addWidget(fastq_hint)
        fastq_layout.addLayout(fastq_action)
        fastq_layout.addStretch(1)
        routes.addTab(fastq_page, "Local FASTQ")

        # Processed RNA-seq starting points.
        processed_page = QWidget()
        processed_layout = QVBoxLayout(processed_page)
        processed_layout.setContentsMargins(10, 10, 10, 10)
        processed_layout.setSpacing(10)

        cm_group = QGroupBox("Raw count matrix")
        cm_layout = QVBoxLayout(cm_group)
        cm_btn = QPushButton("Import count matrix")
        cm_btn.setToolTip("Start from a gene x sample counts table (TSV/CSV or featureCounts output). "
                          "The pipeline skips download/QC/alignment and runs DESeq2 -> figures -> enrichment.")
        cm_btn.clicked.connect(self._import_count_matrix)
        cm_description = QLabel(
            "Gene × sample raw counts. Skips download, read QC and alignment; retains DESeq2, figures and enrichment.")
        cm_description.setWordWrap(True)
        cm_layout.addWidget(cm_description)
        cm_layout.addWidget(cm_btn, 0, Qt.AlignmentFlag.AlignLeft)
        processed_layout.addWidget(cm_group)

        dr_group = QGroupBox("Completed DE table")
        dr_layout = QVBoxLayout(dr_group)
        dr_btn = QPushButton("Import differential-expression results")
        dr_btn.setToolTip("Start from an external differential-expression table (CSV/TSV with at least gene_id, "
                          "log2FoldChange and padj). The pipeline skips alignment/counts/DESeq2 and runs "
                          "enrichment, the volcano/MA/p-value figures and the STRING PPI network. PCA, "
                          "sample heatmaps, sample correlation and genes-of-interest need counts and are skipped.")
        dr_btn.clicked.connect(self._import_deseq2_results)
        dr_description = QLabel(
            "Completed differential-expression table. Skips sample-level analysis; retains compatible figures, enrichment and PPI.")
        dr_description.setWordWrap(True)
        dr_layout.addWidget(dr_description)
        dr_layout.addWidget(dr_btn, 0, Qt.AlignmentFlag.AlignLeft)
        processed_layout.addWidget(dr_group)
        processed_layout.addStretch(1)
        routes.addTab(processed_page, "Count / DE tables")

        # Microarray routes and processing options.
        micro_page = QWidget()
        micro_layout = QVBoxLayout(micro_page)
        micro_layout.setContentsMargins(10, 10, 10, 10)
        micro_hint = QLabel(
            "Fetch a GEO Series microarray or import an already-normalized expression matrix. "
            "RNA-seq GSE records are redirected to the public-accession route.")
        micro_hint.setWordWrap(True)
        micro_layout.addWidget(micro_hint)
        self.gse_box = QLineEdit()
        self.gse_box.setPlaceholderText("GSE accession, e.g. GSE5583")
        self.gse_box.setToolTip(
            "GEO Series (GSE) microarray accessions only. For RNA-seq, enter SRA/ENA run "
            "accessions in the other box instead.")
        geo_btn = QPushButton("Fetch GEO series")
        geo_btn.setToolTip("Load a GEO/GSE microarray dataset. The pipeline ingests the normalized "
                           "intensities (GEOquery/affy), runs limma differential expression, then the "
                           "same figures and enrichment. RNA-seq GSEs are redirected to the SRA box.")
        geo_btn.clicked.connect(self._fetch_geo_series)
        micro_upload_btn = QPushButton("Import microarray matrix")
        micro_upload_btn.setToolTip(
            "Load your own microarray data without a GEO accession: a gene x sample expression "
            "matrix (first column gene ids or symbols, one column per sample; already-normalized "
            "log2 intensities). Runs limma differential expression, figures, and enrichment just "
            "like a fetched GEO series — no download.")
        micro_upload_btn.clicked.connect(self._import_microarray_matrix)
        geo_row = QHBoxLayout()
        geo_row.addWidget(self.gse_box, 1)
        geo_row.addWidget(geo_btn)
        micro_layout.addLayout(geo_row)
        upload_row = QHBoxLayout()
        upload_row.addWidget(micro_upload_btn)
        upload_row.addStretch(1)
        micro_layout.addLayout(upload_row)
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
        micro_layout.addWidget(self.micro_group)
        micro_layout.addStretch(1)
        routes.addTab(micro_page, "Microarray")

        layout.addWidget(routes)
        preview_heading = QLabel("Import summary and next step")
        preview_heading.setProperty("uiRole", "sectionTitle")
        preview_frame = QFrame()
        preview_frame.setProperty("uiRole", "statusBanner")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(12, 8, 12, 8)
        self.input_preview = _PlainTextLabel()
        self.input_preview.setAccessibleName("Input route summary and next step")
        self.input_preview.setPlainText(
            "Choose a route above. Imported samples, detected layout and the next required step appear here.")
        preview_layout.addWidget(self.input_preview)
        layout.addWidget(preview_heading)
        layout.addWidget(preview_frame)
        self.input_preview_frame = preview_frame
        layout.addStretch(1)
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
        save_metadata(samples, self._configured_samples_path())
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
                self.config.enrichment.taxon_id = entry.get("taxon_id")
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
        config_changed = False
        if mode != "microarray" and self.config.enrichment.keytype == "SYMBOL":
            self.config.enrichment.keytype = None
            config_changed = True
        direction = self.config.input.deseq2_results_direction
        file_provenance = self.config.input.deseq2_results_provenance
        if mode != "deseq2_results" and (
            any((direction.numerator, direction.denominator, direction.confirmed, direction.confirmed_at))
            or file_provenance != Deseq2ResultsFileProvenance()
        ):
            self.config.input.deseq2_results_direction = Deseq2ResultsDirectionProvenance()
            self.config.input.deseq2_results_provenance = Deseq2ResultsFileProvenance()
            config_changed = True
        if config_changed and self.project_root is not None:
            self.manager.save_config(self.project_root, self.config)
        if hasattr(self, "input_route_tabs"):
            route_index = {
                "sra": 0,
                "fastq": 1,
                "mixed": 1,
                "count_matrix": 2,
                "deseq2_results": 2,
                "microarray": 3,
            }.get(mode, 0)
            self.input_route_tabs.setCurrentIndex(route_index)
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
                    "External-results mode: alignment and a genome reference are skipped. Select your "
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
        external_results = mode == "deseq2_results"
        if getattr(self, "workflow_de_form", None) is not None:
            # An uploaded DE table already carries its model and signed contrast. Hide the
            # local-model rows so users cannot edit settings that this route must ignore;
            # downstream FDR and fold-change decision thresholds stay available.
            self.workflow_de_form.setRowVisible(self.de_engine, not external_results)
            for row_widget in self.workflow_local_comparison_rows:
                self.workflow_de_form.setRowVisible(row_widget, not external_results)
                row_widget.setEnabled(not external_results)
            # This optional note has no row when a project has only one contrast. Do not
            # resurrect its empty form row while restoring ordinary input modes.
            self.workflow_de_form.setRowVisible(
                self.contrast_info,
                not external_results and bool(self.contrast_info.text().strip()),
            )
            self.contrast_info.setEnabled(not external_results)
        if getattr(self, "external_de_direction_banner", None) is not None:
            direction = self.config.input.deseq2_results_direction
            if external_results and direction.confirmed and direction.numerator and direction.denominator:
                self.external_de_direction_banner.setText(
                    "Positive log2FC means higher expression in "
                    f"{direction.numerator} than in {direction.denominator}. To change this "
                    "interpretation, use Project and data > Add data and re-import the table."
                )
            elif external_results:
                self.external_de_direction_banner.setText(
                    "Imported result direction is not confirmed. Re-import the results table on "
                    "Input data and confirm what positive log2 fold change means; pre-run validation "
                    "will block analysis until then."
                )
            self.external_de_direction_banner.setVisible(external_results)
        if getattr(self, "alpha_threshold_info", None) is not None:
            recorded_method = str(
                self.config.input.deseq2_results_provenance.p_adjustment_method or ""
            ).strip()
            method_key = recorded_method.casefold().replace("-", " ")
            bh_confirmed = (
                "benjamini" in method_key and "hochberg" in method_key
            ) or method_key.strip() in {"bh", "bh fdr"}
            if external_results and not bh_confirmed:
                method_note = (
                    f" The recorded upstream adjustment method is {recorded_method}."
                    if recorded_method and method_key not in {"unknown", "unspecified", "not reported"}
                    else " The upstream adjustment method was not reported."
                )
                threshold_title = "Adjusted p-value"
                threshold_help = (
                    "Threshold applied directly to the adjusted p-values supplied in the imported "
                    f"differential-expression table.{method_note}"
                )
            else:
                threshold_title = "BH FDR"
                threshold_help = (
                    "Significance threshold on Benjamini-Hochberg adjusted p-values. Default 0.05."
                )
            self._set_info_label(self.alpha_threshold_info, threshold_title, threshold_help)
            self.alpha.setAccessibleName(threshold_title)
            self.alpha.setToolTip(threshold_help)
        if getattr(self, "workflow_design_toggle", None) is not None:
            self.workflow_design_toggle.setVisible(not external_results)
            self.workflow_design_toggle.setEnabled(not external_results)
            if external_results:
                self.workflow_design_toggle.setChecked(False)
        if getattr(self, "workflow_design_options", None) is not None:
            self.workflow_design_options.setVisible(
                not external_results and self.workflow_design_toggle.isChecked())
            self.workflow_design_options.setEnabled(not external_results)
        for model_control_name in ("de_min_count", "de_shrink"):
            model_control = getattr(self, model_control_name, None)
            if model_control is not None:
                model_control.setEnabled(not external_results)
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
        if hasattr(self, "trim_poly_g"):
            self._sync_trimmer_controls()
        if hasattr(self, "workflow_summary"):
            self._update_workflow_summary()
        self._update_workflow_section_height()

    def _update_workflow_section_height(self, *_args) -> None:
        """Fit the active settings section before resorting to inner scrolling."""
        tabs = getattr(self, "workflow_section_tabs", None)
        if tabs is None:
            return
        current = tabs.currentWidget()
        if current is None:
            return
        external_comparison = (
            self.config is not None
            and self.config.input.type == "deseq2_results"
            and tabs.currentIndex() == 0
        )

        content_height = current.sizeHint().height()
        if isinstance(current, QScrollArea) and current.widget() is not None:
            holder = current.widget()
            holder_layout = holder.layout()
            section = (
                holder_layout.itemAt(0).widget()
                if holder_layout is not None and holder_layout.count() else None
            )
            section_layout = section.layout() if section is not None else None
            if section_layout is not None:
                section_layout.activate()
                margins = section_layout.contentsMargins()
                content_height = margins.top() + margins.bottom()
                content_width = max(
                    1,
                    current.viewport().width() - margins.left() - margins.right(),
                )
                for index in range(section_layout.count()):
                    item = section_layout.itemAt(index)
                    widget = item.widget()
                    if widget is not None:
                        if widget.isHidden():
                            continue
                        if widget.layout() is not None:
                            widget.layout().activate()
                        if widget.hasHeightForWidth():
                            content_height += widget.heightForWidth(content_width)
                        elif widget.layout() is not None and widget.layout().hasHeightForWidth():
                            content_height += widget.layout().heightForWidth(content_width)
                        else:
                            content_height += widget.sizeHint().height()
                        continue
                    spacer = item.spacerItem()
                    if spacer is not None:
                        content_height += spacer.sizeHint().height()
        # QTabWidget is not a QFrame, so derive its small pane allowance rather
        # than relying on a frameWidth API it does not expose.
        chrome_height = tabs.tabBar().sizeHint().height() + 4
        natural_height = content_height + chrome_height

        # The tab bar and one control row are the true minimum. Short collapsed
        # sections should not inherit a large arbitrary blank surface.
        minimum_height = max(
            tabs.minimumSizeHint().height(), tabs.tabBar().sizeHint().height() + 44)
        desired_height = max(minimum_height, natural_height)
        if external_comparison:
            # The external-results comparison contains only immutable direction
            # provenance and two thresholds; keep that deliberately short.
            desired_height = min(desired_height, 230)

        page = getattr(self, "workflow_page", None)
        page_layout = getattr(self, "workflow_page_layout", None)
        intro = getattr(self, "workflow_intro", None)
        save_bar = getattr(self, "workflow_save_bar", None)
        if (
            page is not None
            and page_layout is not None
            and intro is not None
            and save_bar is not None
            and page.isVisible()
            and page.height() > 0
        ):
            margins = page_layout.contentsMargins()
            intro_height = intro.height() if intro.height() > 0 else intro.sizeHint().height()
            save_height = save_bar.height() if save_bar.height() > 0 else save_bar.sizeHint().height()
            available_height = (
                page.height()
                - margins.top()
                - margins.bottom()
                - intro_height
                - save_height
                - page_layout.spacing() * 2
            )
            desired_height = min(desired_height, max(minimum_height, available_height))

        target_height = max(minimum_height, int(desired_height))
        tabs.setFixedHeight(target_height)
        tabs.updateGeometry()

    def _schedule_workflow_section_height_update(self, *_args) -> None:
        """Coalesce disclosure, navigation and resize updates after Qt relayouts."""
        if self._workflow_height_update_pending:
            return
        self._workflow_height_update_pending = True

        def apply_update() -> None:
            self._workflow_height_update_pending = False
            self._update_workflow_section_height()
            # A window resize can deliver before the page layout receives its
            # final height. Re-evaluate once after that geometry settles.
            QTimer.singleShot(0, self._update_workflow_section_height)

        QTimer.singleShot(0, apply_update)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt virtual name
        # MainWindow.resizeEvent can run before TaskNavigator assigns the final
        # content geometry. Refit from the Workflow page's own Resize event so
        # expanded sections never overshoot the space above the persistent save
        # row during compact/wide transitions.
        if (
            watched is getattr(self, "workflow_page", None)
            and event.type() == QEvent.Type.Resize
            and hasattr(self, "workflow_section_tabs")
        ):
            self._update_workflow_section_height()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "workflow_section_tabs"):
            self._schedule_workflow_section_height_update()

    def _import_deseq2_results(self) -> None:
        if not self._require_project() or self.config is None:
            return
        assert self.project_root is not None
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a differential-expression results table", "",
            "Differential-expression results (*.csv *.tsv *.txt)")
        if not path:
            return
        src = Path(path)
        self.statusBar().showMessage("Validating the complete differential-expression table...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            validated_source = validate_de_results_table(src)
        except DETableValidationError as exc:
            QMessageBox.warning(
                self, APP_NAME,
                "The table cannot be imported:\n\n" + "\n".join(f"• {error}" for error in exc.errors),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        direction = self._ask_de_results_direction()
        if direction is None:
            self.statusBar().showMessage(
                "Import cancelled — log2FC direction was not confirmed.", 6000)
            return
        try:
            details = self._coerce_external_de_details(direction)
            confirmed_at = datetime.now().astimezone().isoformat(timespec="seconds")
            direction_record = Deseq2ResultsDirectionProvenance(
                numerator=details.numerator,
                denominator=details.denominator,
                confirmed=True,
                confirmed_at=confirmed_at,
            )
            method_record = Deseq2ResultsFileProvenance(
                upstream_method=details.upstream_method,
                lfc_shrinkage=details.lfc_shrinkage,
                p_adjustment_method=details.p_adjustment_method,
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, APP_NAME, f"The import provenance is invalid: {exc}")
            return

        # Preserve the source bytes exactly, including a TSV delimiter, but validate
        # the copied bytes before atomically replacing an earlier project copy.
        project_copy = "config/deseq2_results.csv"
        dest = self.project_root / project_copy
        temp_dest = dest.with_name(dest.name + ".importing")
        self.statusBar().showMessage("Copying and verifying the differential-expression table...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            shutil.copyfile(src, temp_dest)
            validated_copy = validate_de_results_table(temp_dest)
            if validated_copy.sha256 != validated_source.sha256:
                raise OSError("the source changed while it was being imported")
            imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
            file_record = Deseq2ResultsFileProvenance.model_validate(provenance_payload(
                validated_copy,
                original_basename=src.name,
                imported_at=imported_at,
                project_copy=project_copy,
                upstream_method=method_record.upstream_method,
                lfc_shrinkage=method_record.lfc_shrinkage,
                p_adjustment_method=method_record.p_adjustment_method,
            ))
            temp_dest.replace(dest)
            self.config.input.type = "deseq2_results"
            self.config.input.deseq2_results = project_copy
            self.config.input.deseq2_results_direction = direction_record
            self.config.input.deseq2_results_provenance = file_record
            self.config.input.count_matrix = None
            self.config.microarray.gse_accession = None
            # A microarray-only SYMBOL keytype must not carry over; fall back to the
            # organism mapping (or the LOC/ENSEMBL handling in the enrichment step).
            if self.config.enrichment.keytype == "SYMBOL":
                self.config.enrichment.keytype = None
            # A results-only route has no sample-level matrix. Keep a valid header
            # without inventing a sample that would imply nonexistent metadata.
            samples = pd.DataFrame(columns=["sample_id", "condition", "layout", "fastq_1"])
            save_metadata(samples, self._configured_samples_path())
            self.metadata_table.load_dataframe(samples)
            if self.config.deseq2.contrasts:
                contrast = self.config.deseq2.contrasts[0]
                contrast.numerator = direction_record.numerator or ""
                contrast.denominator = direction_record.denominator or ""
                contrast.name = f"{direction_record.numerator}_vs_{direction_record.denominator}"
            self.manager.save_config(self.project_root, self.config)
        except (DETableValidationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not create a verified project copy: {exc}")
            return
        finally:
            temp_dest.unlink(missing_ok=True)
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
            f"External-results mode: imported {src.name} as the verified project copy.\n\n"
            "The pipeline skips alignment, counts and local differential-expression modelling, and runs "
            "enrichment (GO/KEGG/GSEA), the "
            "volcano / MA / p-value figures, and the STRING PPI network directly from your table. PCA, "
            "sample-distance and expression heatmaps, sample correlation, the Wilcoxon diagnostic and "
            "genes-of-interest need per-sample counts and are skipped." + org_note +
            f"\n\nDirection recorded: positive log2FoldChange means higher in {direction_record.numerator} "
            f"than {direction_record.denominator}."
            f"\nSource method: {file_record.upstream_method}; LFC shrinkage: {file_record.lfc_shrinkage}; "
            f"adjusted-p method: {file_record.p_adjustment_method}."
            "\n\nNext: select the organism annotation needed for enrichment/STRING, review validation, then run.")
        self.statusBar().showMessage(
            f"Imported and verified {validated_source.row_count:,} differential-expression rows", 8000)

    @staticmethod
    def _coerce_external_de_details(
        value: ExternalDEImportDetails | tuple[str, str],
    ) -> ExternalDEImportDetails:
        # Keep extensions written against the earlier two-value direction prompt
        # compatible; new callers receive the complete record.
        if isinstance(value, ExternalDEImportDetails):
            return value
        if isinstance(value, tuple) and len(value) == 2:
            return ExternalDEImportDetails(value[0], value[1])
        raise TypeError("Expected confirmed external-results import details.")

    def _ask_de_results_direction(self) -> ExternalDEImportDetails | None:
        """Require sign meaning and collect optional upstream-method provenance."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm external-results provenance")
        dialog.setMinimumWidth(560)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(self._page_intro(
            "Define the imported comparison",
            "BulkSeq Studio keeps the supplied signs unchanged. Record exactly what a positive "
            "log2FoldChange means in the source analysis."))
        form_group = QGroupBox("Direction provenance")
        form = QFormLayout(form_group)
        existing = self.config.input.deseq2_results_direction if self.config is not None else None
        numerator = QLineEdit(existing.numerator if existing and existing.numerator else "")
        denominator = QLineEdit(existing.denominator if existing and existing.denominator else "")
        numerator.setObjectName("externalDENumerator")
        denominator.setObjectName("externalDEDenominator")
        numerator.setPlaceholderText("group with positive change, e.g. treated")
        denominator.setPlaceholderText("reference group, e.g. control")
        form.addRow("Numerator group", numerator)
        form.addRow("Denominator group", denominator)
        summary = QLabel()
        summary.setWordWrap(True)
        summary.setProperty("hint", True)
        form.addRow(summary)
        confirmation = QCheckBox(
            "I confirm the direction and any method details entered match the source analysis."
        )
        confirmation.setObjectName("externalDEConfirmation")
        form.addRow(confirmation)
        dialog_layout.addWidget(form_group)

        method_group = QGroupBox("Source analysis details (optional)")
        method_form = QFormLayout(method_group)
        upstream_method = QLineEdit()
        upstream_method.setObjectName("externalDEUpstreamMethod")
        upstream_method.setToolTip("For example: DESeq2, edgeR, limma, or another upstream method.")
        shrinkage = QComboBox()
        shrinkage.setObjectName("externalDELfcShrinkage")
        shrinkage.addItem("Unknown / not recorded", "unknown")
        shrinkage.addItem("Applied", "applied")
        shrinkage.addItem("Not applied", "not_applied")
        p_adjustment = QLineEdit()
        p_adjustment.setObjectName("externalDEPAdjustmentMethod")
        p_adjustment.setToolTip("For example: Benjamini-Hochberg (BH), Storey q value, or Bonferroni.")
        method_form.addRow("Upstream DE method", upstream_method)
        method_form.addRow("LFC shrinkage", shrinkage)
        method_form.addRow("Adjusted-p method", p_adjustment)
        method_hint = QLabel(
            "Leave method fields blank when they are not documented; the project records them as unknown."
        )
        method_hint.setWordWrap(True)
        method_hint.setProperty("hint", True)
        method_form.addRow(method_hint)
        dialog_layout.addWidget(method_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("Confirm and import")
        ok_button.setProperty("primary", True)

        def update_state() -> None:
            num = numerator.text().strip()
            den = denominator.text().strip()
            try:
                Deseq2ResultsDirectionProvenance(
                    numerator=num,
                    denominator=den,
                    confirmed=True,
                    confirmed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                )
                Deseq2ResultsFileProvenance(
                    upstream_method=upstream_method.text(),
                    p_adjustment_method=p_adjustment.text(),
                )
                valid = True
            except ValueError:
                valid = False
            ok_button.setEnabled(valid and confirmation.isChecked())
            if num and den:
                summary.setText(
                    f"Recorded interpretation: positive log2FoldChange = higher in {num} than {den}."
                    if valid else "Use two different, single-line group labels and concise method names.")
            else:
                summary.setText("Enter both group labels to make the sign unambiguous.")

        numerator.textChanged.connect(update_state)
        denominator.textChanged.connect(update_state)
        upstream_method.textChanged.connect(update_state)
        p_adjustment.textChanged.connect(update_state)
        confirmation.toggled.connect(update_state)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)
        update_state()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return ExternalDEImportDetails(
            numerator=numerator.text().strip(),
            denominator=denominator.text().strip(),
            upstream_method=upstream_method.text().strip() or "unknown",
            lfc_shrinkage=str(shrinkage.currentData()),
            p_adjustment_method=p_adjustment.text().strip() or "unknown",
        )

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
                df = read_user_table(src, sep=sep, comment="#", dtype=str)
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
            save_metadata(samples, self._configured_samples_path())
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
                df = read_user_table(src, sep=sep, comment="#", dtype=str)
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
            save_metadata(samples, self._configured_samples_path())
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
        save_metadata(samples, self._configured_samples_path())
        (self.project_root / "config" / "sra_accessions.txt").write_text("\n".join(accessions) + "\n", encoding="utf-8")
        self.metadata_table.load_dataframe(samples)
        if self.config is not None:
            self.config.input.type = "sra"
            layouts = set(samples["layout"])
            self.config.input.layout = layouts.pop() if len(layouts) == 1 else "mixed"  # type: ignore[assignment]
            self.manager.save_config(self.project_root, self.config)
            self._apply_input_mode_ui()
        self.tabs.setCurrentIndex(self.metadata_tab_index)
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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._page_intro(
            "Review samples",
            "Review sample identifiers, conditions, read layout and file/accession assignments. "
            "This step is required for sample-level analysis; imported differential-expression "
            "results can be explored without a sample sheet."))

        # Keep the four most frequent edits visible. Structural and file actions
        # remain one disclosure away instead of competing as twelve peer buttons.
        tooltips = {
            "Paste": "Paste clipboard cells (e.g. copied from Excel) at the selected cell. "
                     "A single copied value fills every selected cell. Ctrl+V works too "
                     "(if a cell is in edit mode, press Esc first).",
            "Restore generated": "Replace the edited table with the last sample sheet generated from imported accessions or files.",
        }
        common_box = QGroupBox("Common edits")
        common_row = QHBoxLayout(common_box)
        common_row.setSpacing(6)
        for text, slot in (
            ("Add row", self.metadata_add_row),
            ("Delete rows", self.metadata_delete_rows),
            ("Assign condition", self._assign_condition),
            ("Paste", self._paste_metadata),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            if text in tooltips:
                btn.setToolTip(tooltips[text])
            common_row.addWidget(btn)
        common_row.addStretch(1)
        layout.addWidget(common_box)

        more_toggle = QToolButton()
        more_toggle.setText("More table tools")
        more_toggle.setCheckable(True)
        more_toggle.setArrowType(Qt.ArrowType.RightArrow)
        more_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        more_toggle.setAccessibleName("Show more sample-table tools")
        layout.addWidget(more_toggle)
        more_box = QWidget()
        more_box.setProperty("uiRole", "disclosureContent")
        action_rows = QVBoxLayout(more_box)
        action_rows.setContentsMargins(8, 4, 0, 8)
        action_rows.setSpacing(7)
        groups = (
            ("Rows", (
                ("Duplicate selected", "Duplicate selected metadata rows", self.metadata_duplicate_rows),
                ("Autofill replicates", "Autofill replicate numbers by condition", self.metadata_autofill),
            )),
            ("Columns", (
                ("Add", "Add metadata column", self._add_column),
                ("Rename", "Rename selected metadata column", self._rename_column),
                ("Remove", "Remove selected metadata column", self._remove_column),
            )),
            ("Files", (
                ("Import table…", "Import sample metadata from TSV, CSV, or XLSX", self._import_metadata),
                ("Export TSV…", "Export sample metadata as TSV", self._export_metadata),
                ("Restore generated", "Restore the last generated sample sheet", self._restore_auto_metadata),
            )),
        )
        row_labels: list[QLabel] = []
        self.metadata_advanced_buttons: list[QPushButton] = []
        for group_title, specs in groups:
            command_row = QHBoxLayout()
            command_row.setContentsMargins(0, 0, 0, 0)
            command_row.setSpacing(6)
            group_label = QLabel(group_title)
            group_label.setProperty("uiRole", "sectionLabel")
            row_labels.append(group_label)
            command_row.addWidget(group_label, 0, Qt.AlignmentFlag.AlignVCenter)
            for text, accessible_name, slot in specs:
                btn = QPushButton(text)
                btn.clicked.connect(slot)
                btn.setAccessibleName(accessible_name)
                btn.setToolTip(
                    tooltips.get("Restore generated", accessible_name)
                    if text == "Restore generated" else accessible_name
                )
                btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                command_row.addWidget(btn)
                self.metadata_advanced_buttons.append(btn)
            command_row.addStretch(1)
            action_rows.addLayout(command_row)
        label_width = max(label.sizeHint().width() for label in row_labels) + 12
        for label in row_labels:
            label.setFixedWidth(label_width)
        more_box.setVisible(False)
        more_toggle.toggled.connect(more_box.setVisible)
        more_toggle.toggled.connect(
            lambda expanded: more_toggle.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        self.metadata_more_toggle = more_toggle
        self.metadata_more_group = more_box
        layout.addWidget(more_box)

        commit_row = QHBoxLayout()
        commit_row.addStretch(1)
        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._validate_metadata)
        save_btn = QPushButton("Save samples.tsv")
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._save_metadata)
        commit_row.addWidget(validate_btn)
        commit_row.addWidget(save_btn)
        layout.addLayout(commit_row)

        self.metadata_table = MetadataTable()
        layout.addWidget(self.metadata_table, 1)
        self.metadata_message_heading = QLabel("Validation messages")
        self.metadata_message_frame = QFrame()
        self.metadata_message_frame.setProperty("uiRole", "statusBanner")
        message_layout = QVBoxLayout(self.metadata_message_frame)
        message_layout.setContentsMargins(12, 7, 12, 7)
        self.metadata_messages = _PlainTextLabel()
        self.metadata_messages.setAccessibleName("Sample validation messages")
        message_layout.addWidget(self.metadata_messages)
        self.metadata_message_heading.setVisible(False)
        self.metadata_message_frame.setVisible(False)
        layout.addWidget(self.metadata_message_heading)
        layout.addWidget(self.metadata_message_frame)
        self.metadata_tab_index = self.tabs.addTab(self._scrollable(page), "Metadata")

    def _build_reference_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._page_intro(
            "Reference and annotation",
            "Choose a genomic reference for raw-read processing, or organism annotation for identifier "
            "mapping, enrichment and STRING. Completed result tables remain viewable without one."))
        self.reference_list = QListWidget()
        self.reference_list.setMinimumHeight(130)
        self.reference_list.setMaximumHeight(190)
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
            self._advisory_banner_qss(self._current_theme_mode()))
        self.reference_mode_banner.setVisible(False)
        self.current_organism_label = QLabel("Selected organism: — none —")
        self.current_organism_label.setWordWrap(True)
        self.current_organism_label.setProperty("uiRole", "sectionLabel")
        layout.addWidget(self.reference_mode_banner)
        preset_group = QGroupBox("Organism preset")
        preset_layout = QVBoxLayout(preset_group)
        preset_layout.addWidget(self.current_organism_label)
        preset_layout.addWidget(self.reference_list)
        preset_layout.addWidget(choose, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(preset_group)

        # Custom files are a secondary route. Keep them one disclosure away so
        # the common organism-preset path remains a single clear decision.
        custom_toggle = QToolButton()
        custom_toggle.setText("Use custom reference files")
        custom_toggle.setCheckable(True)
        custom_toggle.setArrowType(Qt.ArrowType.RightArrow)
        custom_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        custom_toggle.setAccessibleName("Show custom reference fields")
        layout.addWidget(custom_toggle)
        custom_group = QGroupBox("Custom reference")
        custom_layout = QVBoxLayout(custom_group)
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
        custom_layout.addLayout(form)
        self.reference_details = QTextEdit()
        self.reference_details.setReadOnly(True)
        self.reference_details.setMinimumHeight(90)
        self.reference_details.setMaximumHeight(150)
        self.reference_details.setPlaceholderText(
            "Reference validation and lock details appear here.")
        custom_layout.addWidget(self.reference_details)
        custom_group.setVisible(False)
        custom_toggle.toggled.connect(custom_group.setVisible)
        custom_toggle.toggled.connect(
            lambda expanded: custom_toggle.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        self.reference_custom_toggle = custom_toggle
        self.reference_custom_group = custom_group
        layout.addWidget(custom_group)
        layout.addStretch(1)
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
        validation = validate_reference(genome, annotation)
        if any(message.get("status") == "FAIL" for message in validation):
            detail = self._format_messages(validation)
            self.reference_details.setPlainText("Reference validation:\n" + detail)
            QMessageBox.warning(
                self,
                APP_NAME,
                "The custom reference was not selected because its FASTA and annotation "
                "failed structural or contig-compatibility checks.\n\n" + detail,
            )
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
            enr.taxon_id = entry.get("taxon_id")
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

    def _sync_trimmer_controls(self, *_args) -> None:
        """Expose only options the selected trimmer actually consumes."""
        if not hasattr(self, "trim_poly_g"):
            return
        fastp_selected = self.trimmer.currentData() == "fastp"
        enabled = self.trim.isEnabled() and self.trim.isChecked() and fastp_selected
        self.trim_poly_g.setEnabled(enabled)
        self.trim_poly_g.setToolTip(
            "Available for fastp only (NextSeq/NovaSeq two-colour chemistry)."
            if fastp_selected else
            "Not used by the selected trimmer; switch to fastp to enable poly-G trimming."
        )

    def _update_workflow_summary(self, *_args) -> None:
        if not hasattr(self, "workflow_summary"):
            return
        if self.config is None:
            self.workflow_summary.setText(
                "Open or create a project to resolve the input route, comparison, and active analysis modules."
            )
            return
        route = str(self.config.input.type).replace("_", " ")
        numerator = self.numerator.currentText().strip() or "not set"
        denominator = self.denominator.currentText().strip() or "not set"
        if self.config.input.type == "deseq2_results":
            direction = self.config.input.deseq2_results_direction
            source = self.config.input.deseq2_results_provenance
            if direction.confirmed and direction.numerator and direction.denominator:
                sign = (f"positive log2FC = higher in {direction.numerator} than "
                        f"{direction.denominator}")
            else:
                sign = "log2FC direction still needs confirmation"
            plan = (
                "provided differential-expression table · no read processing or local DE model · "
                f"{sign} · upstream method {source.upstream_method} · adjusted-p method "
                f"{source.p_adjustment_method}"
            )
            threshold = (
                f"use supplied adjusted-p values < {self.alpha.value():g} and "
                f"|log2FC| ≥ {self.lfc_threshold.value():g}"
            )
        elif self.config.input.type == "count_matrix":
            plan = (f"raw count matrix · no read alignment · {self.de_engine.currentText()} estimates "
                    f"{numerator} relative to {denominator}")
        elif self.config.input.type == "microarray":
            plan = (f"microarray intensities · no read alignment · limma estimates "
                    f"{numerator} relative to {denominator}")
        else:
            plan = (f"{route} input · {self.aligner.currentText()} / {self.quantifier.currentText()} · "
                    f"estimate {numerator} relative to {denominator}")
        if self.config.input.type != "deseq2_results":
            threshold = (
                f"BH FDR < {self.alpha.value():g} and |log2FC| ≥ {self.lfc_threshold.value():g}"
            )
        self.workflow_summary.setText(f"Current plan: {plan} · {threshold}.")

    def _build_workflow_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        # Preserve the horizontal reading inset while using the vertical space
        # for expanded settings instead of creating an avoidable inner scroll.
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
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
        self.enrichment_warn.setStyleSheet(f"color: {status_color('WARNING', self._current_theme_mode())};")
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
        self.trimmer.currentIndexChanged.connect(self._sync_trimmer_controls)
        self.trim.toggled.connect(self._sync_trimmer_controls)
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
        self.contrast_info.setStyleSheet(f"color: {PALETTES[self._current_theme_mode()]['MUTED_TEXT']};")
        self.contrast_info.setVisible(False)
        self.refresh_conditions_button = QPushButton("Refresh conditions from metadata")
        self.refresh_conditions_button.clicked.connect(self._refresh_conditions)
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
            "microarray mode (which uses limma-trend) or when an external results table is uploaded."
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
        align_form.addRow(self._info_label("Quality threshold (Phred)", "Minimum acceptable per-base Phred quality. This value is translated to the selected trimmer. Default 15."), self.fastp_q)
        align_form.addRow(self._info_label("Minimum read length", "Reads shorter than this after trimming are discarded. This value is translated to the selected trimmer. Protocol default 36."), self.fastp_len)
        align_form.addRow(self._info_label("fastp poly-G trimming", "fastp-only option for 2-colour chemistry (NextSeq/NovaSeq). Disabled for Trim Galore and Trimmomatic."), self.trim_poly_g)
        align_form.addRow("rRNA filtering", self.rrna)
        align_form.addRow(self._info_label("rRNA tool", "SortMeRNA (default, reference-based, ~150 MB database) or RiboDetector (reference-free, no database). Used only when rRNA filtering is on."), self.rrna_tool)
        align_form.addRow(self._info_label("Contamination screen", "Optional FastQ Screen report of the % of reads matching a panel of reference genomes — a QC report, not a filter. Needs a FastQ Screen config (set it under Advanced parameters); skipped if none is given. Results appear in MultiQC."), self.contam_screen)
        self.align_group = align_group

        de_group = QGroupBox("Differential expression")
        de_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        de_outer = QVBoxLayout(de_group)
        self.external_de_direction_banner = QLabel("")
        self.external_de_direction_banner.setObjectName("externalDEDirectionBanner")
        self.external_de_direction_banner.setAccessibleName(
            "Imported differential-expression direction")
        self.external_de_direction_banner.setProperty("uiRole", "statusBanner")
        self.external_de_direction_banner.setWordWrap(True)
        self.external_de_direction_banner.setContentsMargins(10, 8, 10, 8)
        self.external_de_direction_banner.setVisible(False)
        de_outer.addWidget(self.external_de_direction_banner)
        de_form = QFormLayout()
        de_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        de_form.addRow(self._info_label("DE engine", "Statistical engine for the differential test on count data. DESeq2 (default) fits most studies, including small ones; limma-voom is an optional cross-check for larger designs (about 6+ samples per group). Both write the same result tables and figures. Ignored in microarray mode and for external-results uploads."), self.de_engine)

        factor_row = QWidget()
        factor_layout = QHBoxLayout(factor_row)
        factor_layout.setContentsMargins(0, 0, 0, 0)
        factor_layout.setSpacing(8)
        factor_layout.addWidget(self.contrast_factor, 1)
        factor_layout.addWidget(self.refresh_conditions_button)
        de_form.addRow(
            self._info_label("Comparison factor", "The metadata column compared in the differential test (usually 'condition')."),
            factor_row)
        de_form.addRow(self._info_label("Numerator group", "The group whose change is measured. Positive log2 fold change means higher in this group than the denominator."), self.numerator)
        de_form.addRow(self._info_label("Denominator group", "The comparison baseline. Positive log2 fold change means higher in the numerator than this group."), self.denominator)
        direction_hint = QLabel(
            "Direction: positive log2 fold change means higher expression in the numerator group.")
        direction_hint.setWordWrap(True)
        direction_hint.setProperty("hint", True)
        de_form.addRow("", direction_hint)
        de_form.addRow("", self.contrast_info)

        threshold_row = QWidget()
        threshold_layout = QHBoxLayout(threshold_row)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.setSpacing(8)
        self.alpha_threshold_info = self._info_label(
            "BH FDR",
            "Significance threshold on Benjamini-Hochberg adjusted p-values. Default 0.05.",
        )
        threshold_layout.addWidget(self.alpha_threshold_info)
        threshold_layout.addWidget(self.alpha)
        threshold_layout.addSpacing(12)
        threshold_layout.addWidget(self._info_label(
            "|log2FC|", "Minimum absolute log2 fold change for a gene to count as up/down-regulated. Default 1.0."))
        threshold_layout.addWidget(self.lfc_threshold)
        threshold_layout.addStretch(1)
        de_form.addRow("Decision thresholds", threshold_row)
        de_outer.addLayout(de_form)
        self.workflow_de_form = de_form
        self.workflow_comparison_factor_row = factor_row
        self.workflow_direction_hint = direction_hint
        self.workflow_local_comparison_rows = (
            factor_row,
            self.numerator,
            self.denominator,
            direction_hint,
        )

        design_toggle = QToolButton()
        design_toggle.setText("Advanced design and organellar options")
        design_toggle.setCheckable(True)
        design_toggle.setChecked(False)
        design_toggle.setArrowType(Qt.ArrowType.RightArrow)
        design_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        design_toggle.setAccessibleName("Show advanced design and organellar options")
        de_outer.addWidget(design_toggle)

        design_options = QWidget()
        design_form = QFormLayout(design_options)
        design_form.setContentsMargins(0, 0, 0, 0)
        design_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        design_form.addRow(self._info_label("Design formula", "R model formula used by every engine. The last term is the effect of interest; put known batch effects before it, e.g. '~ batch + condition'."), self.design)
        self.design_helper_button = QPushButton("Design helper: adjust for batch / covariates…")
        self.design_helper_button.setToolTip(
            "Compose the design formula from your metadata columns without typing R. Tick the "
            "batch/covariate columns to adjust for; the condition of interest is added last.")
        self.design_helper_button.clicked.connect(self._open_design_helper)
        design_form.addRow("", self.design_helper_button)
        design_form.addRow(self._info_label("Reference level", "The factor's baseline level (normally the same as the denominator); DESeq2 is releveled to this."), self.reference_level)
        design_form.addRow(QLabel("featureCounts strandedness is auto-inferred per protocol."))
        self.organellar = QComboBox()
        self.organellar.addItem("Keep (include in analysis)", "keep")
        self.organellar.addItem("Discard before differential expression", "discard")
        self.organellar.addItem("Analyse separately (nuclear DE + organellar subset)", "separate")
        design_form.addRow(self._info_label(
            "Mitochondrial / chloroplast genes",
            "Organellar (mitochondrial + chloroplast) transcripts can dominate library size and "
            "skew DESeq2 normalization. Keep them, discard them before the differential test, or "
            "analyse them separately (the main DE runs on nuclear genes only; a separate organellar "
            "count subset and a per-sample organellar-fraction table are written). Applies to "
            "STAR/HISAT2/Salmon runs (needs a reference genome)."), self.organellar)
        design_options.setVisible(False)
        design_toggle.toggled.connect(design_options.setVisible)
        design_toggle.toggled.connect(
            lambda expanded: design_toggle.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        design_toggle.toggled.connect(self._schedule_workflow_section_height_update)
        de_outer.addWidget(design_options)
        self.workflow_design_toggle = design_toggle
        self.workflow_design_options = design_options
        self.de_group = de_group

        # ---- Advanced tool parameters (collapsible). Defaults reproduce the validated
        # behaviour; users can set each tool's important parameters manually here. ----
        adv_group = QGroupBox("Advanced parameters")
        adv_outer = QVBoxLayout(adv_group)
        self.adv_toggle = QCheckBox("Show advanced tool parameters")
        self.adv_toggle.setToolTip(
            "Per-tool parameters for fine control. The defaults reproduce the validated pipeline "
            "behaviour, so leave them unless you have a specific reason to change them.")
        self.adv_container = QWidget()
        adv_columns = QGridLayout(self.adv_container)
        adv_columns.setContentsMargins(0, 4, 0, 0)
        adv_columns.setHorizontalSpacing(28)
        adv_columns.setVerticalSpacing(0)

        def advanced_column(title: str) -> tuple[QWidget, QFormLayout]:
            column = QWidget()
            # The form's unconstrained size hint is dominated by expandable path
            # fields. Ignore that hint horizontally so the two columns share the
            # actual pane width instead of forcing a nested horizontal scrollbar.
            column.setMinimumWidth(0)
            column.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(6)
            heading = QLabel(title)
            heading.setProperty("uiRole", "sectionLabel")
            column_layout.addWidget(heading)
            form_holder = QWidget()
            form_holder.setMinimumWidth(0)
            form_holder.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            form = QFormLayout(form_holder)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(12)
            form.setVerticalSpacing(6)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
            form.setProperty("compactColumns", True)
            column_layout.addWidget(form_holder)
            return column, form

        read_column, read_form = advanced_column("Read preparation")
        analysis_column, analysis_form = advanced_column("Alignment and statistics")
        adv_columns.addWidget(
            read_column, 0, 0, alignment=Qt.AlignmentFlag.AlignTop)
        adv_columns.addWidget(
            analysis_column, 0, 1, alignment=Qt.AlignmentFlag.AlignTop)
        adv_columns.setColumnStretch(0, 1)
        adv_columns.setColumnStretch(1, 1)
        self.fastp_u = QSpinBox(); self.fastp_u.setRange(0, 100); self.fastp_u.setValue(40)
        self.fastp_polyx = QCheckBox()
        read_form.addRow(self._info_label("fastp: low-quality limit", "Maximum percentage of low-quality bases before a read is discarded (-u). fastp default 40."), self.fastp_u)
        read_form.addRow(self._info_label("fastp: trim 3' poly-X", "Trim 3' poly-A/poly-X tails (degraded or 3'-biased libraries). fastp default off."), self.fastp_polyx)
        self.tm_sw_q = QSpinBox(); self.tm_sw_q.setRange(0, 40); self.tm_sw_q.setValue(15)
        self.tm_leading = QSpinBox(); self.tm_leading.setRange(0, 40); self.tm_leading.setValue(3)
        self.tm_trailing = QSpinBox(); self.tm_trailing.setRange(0, 40); self.tm_trailing.setValue(3)
        read_form.addRow(self._info_label("Trimmomatic: window quality", "Average Phred required over a 4-base sliding window (SLIDINGWINDOW:4:Q). Default 15."), self.tm_sw_q)
        read_form.addRow(self._info_label("Trimmomatic: leading quality", "Trim leading bases below this quality (LEADING). Default 3."), self.tm_leading)
        read_form.addRow(self._info_label("Trimmomatic: trailing quality", "Trim trailing bases below this quality (TRAILING). Default 3."), self.tm_trailing)
        self.rd_ensure = QComboBox()
        for _lbl, _v in (("norrna (keep confident non-rRNA)", "norrna"), ("rrna", "rrna"), ("both", "both"), ("none", "none")):
            self.rd_ensure.addItem(_lbl, _v)
        self.rd_chunk = QSpinBox(); self.rd_chunk.setRange(16, 4096); self.rd_chunk.setValue(256)
        read_form.addRow(self._info_label("RiboDetector: ensure mode (-e)", "Which class is kept with high confidence. norrna keeps high-confidence non-rRNA reads (recommended)."), self.rd_ensure)
        read_form.addRow(self._info_label("RiboDetector: chunk size", "Reads per batch (x1024): a memory/speed trade-off. Default 256."), self.rd_chunk)
        self.fs_subset = QSpinBox(); self.fs_subset.setRange(1000, 5000000); self.fs_subset.setSingleStep(10000); self.fs_subset.setValue(100000)
        analysis_form.addRow(self._info_label("FastQ Screen: reads sampled", "How many reads FastQ Screen subsamples per sample. Default 100000."), self.fs_subset)
        self.fs_conf = QLineEdit()
        fs_conf_browse = QPushButton("Browse")
        fs_conf_browse.clicked.connect(lambda: self._pick_reference_file(self.fs_conf, "FastQ Screen config (*.conf *.txt);;All files (*)"))
        fs_conf_row = QHBoxLayout(); fs_conf_row.addWidget(self.fs_conf); fs_conf_row.addWidget(fs_conf_browse)
        fs_conf_widget = QWidget(); fs_conf_widget.setLayout(fs_conf_row)
        analysis_form.addRow(self._info_label("FastQ Screen: config", "Path to a fastq_screen.conf listing the bowtie2 genome indexes to screen against (required to run the screen). The built-in genome auto-download is not used; point this at a panel you already have."), fs_conf_widget)
        self.star_twopass = QCheckBox()
        self.star_multimap = QSpinBox(); self.star_multimap.setRange(1, 200); self.star_multimap.setValue(10)
        self.star_mismatch = QDoubleSpinBox(); self.star_mismatch.setRange(0.0, 1.0); self.star_mismatch.setSingleStep(0.02); self.star_mismatch.setDecimals(2); self.star_mismatch.setValue(1.0)
        analysis_form.addRow(self._info_label("STAR: two-pass mode", "Two-pass mapping improves novel-junction detection (slower). STAR default off."), self.star_twopass)
        analysis_form.addRow(self._info_label("STAR: max multimappers", "Reads mapping to more than this many loci are discarded (outFilterMultimapNmax). Default 10."), self.star_multimap)
        analysis_form.addRow(self._info_label("STAR: mismatch ratio", "Max mismatches as a fraction of read length (outFilterMismatchNoverReadLmax). 1.0 = STAR default."), self.star_mismatch)
        self.fc_feature = QLineEdit("exon")
        self.fc_attribute = QLineEdit("gene_id")
        analysis_form.addRow(self._info_label("featureCounts: feature", "GTF feature counted (-t). Default exon."), self.fc_feature)
        analysis_form.addRow(self._info_label("featureCounts: gene attribute", "GTF attribute grouped into genes (-g). Default gene_id."), self.fc_attribute)
        self.de_min_count = QSpinBox(); self.de_min_count.setRange(0, 1000); self.de_min_count.setValue(10)
        self.de_shrink = QComboBox()
        for _v in ("apeglm", "ashr", "normal"):
            self.de_shrink.addItem(_v, _v)
        analysis_form.addRow(self._info_label("DESeq2: minimum count", "Keep genes with at least this many reads in the smallest group. Default 10 (the validated value)."), self.de_min_count)
        analysis_form.addRow(self._info_label("DESeq2: LFC shrinkage", "lfcShrink estimator for the MA/volcano effect sizes. Default apeglm (the validated value)."), self.de_shrink)
        self.adv_container.setVisible(False)
        self.adv_toggle.toggled.connect(self.adv_container.setVisible)
        self.adv_toggle.toggled.connect(self._schedule_workflow_section_height_update)
        adv_outer.addWidget(self.adv_toggle)
        adv_outer.addWidget(self.adv_container)

        out_group = QGroupBox("Outputs")
        out_layout = QVBoxLayout(out_group)
        out_layout.setSpacing(8)

        def option_row(control: QCheckBox, label: str, help_text: str) -> QWidget:
            """Keep option names on one line while retaining detailed help."""
            control.setText(label)
            control.setAccessibleName(label)
            control.setAccessibleDescription(help_text)
            control.setToolTip(help_text)
            control.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            holder = QWidget()
            holder.setProperty("uiRole", "optionRow")
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(control)
            info = QToolButton()
            info.setText("ⓘ")
            info.setAccessibleName(f"About {label}")
            info.setAccessibleDescription(help_text)
            info.setAutoRaise(True)
            info.setCursor(Qt.CursorShape.PointingHandCursor)
            info.setToolTip(help_text)
            info.clicked.connect(lambda _checked=False, title=label, body=help_text:
                                 QMessageBox.information(self, title, body))
            row.addWidget(info)
            row.addStretch(1)
            return holder

        included_label = QLabel("Included outputs")
        included_label.setProperty("uiRole", "sectionLabel")
        out_layout.addWidget(included_label)
        included_grid = QGridLayout()
        included_grid.setContentsMargins(0, 0, 0, 0)
        included_grid.setHorizontalSpacing(24)
        included_grid.setVerticalSpacing(6)
        included_grid.addWidget(option_row(
            self.enrichment,
            "Enrichment",
            "Run the configured GO, KEGG, g:Profiler and custom-gene-set enrichment routes."), 0, 0)
        included_grid.addWidget(option_row(
            self.figures,
            "Publication figures",
            "Render the standard differential-expression, sample and quality-control figures."), 0, 1)
        included_grid.setColumnStretch(0, 1)
        included_grid.setColumnStretch(1, 1)
        out_layout.addLayout(included_grid)
        out_layout.addWidget(self.enrichment_warn)

        optional_label = QLabel("Optional analysis modules")
        optional_label.setProperty("uiRole", "sectionLabel")
        out_layout.addWidget(optional_label)
        optional_grid = QGridLayout()
        optional_grid.setContentsMargins(0, 0, 0, 0)
        optional_grid.setHorizontalSpacing(24)
        optional_grid.setVerticalSpacing(6)
        optional_grid.addWidget(option_row(
            self.gsva,
            "GSVA pathway activity",
            "Sample-level gene-set activity scores from your custom gene sets. Needs a custom GMT under Custom gene sets."), 0, 0)
        optional_grid.addWidget(option_row(
            self.rseqc,
            "Extended QC (RSeQC)",
            "Read-distribution and gene-body-coverage QC added to MultiQC. Available for genome-BAM routes, not Salmon."), 0, 1)
        optional_grid.addWidget(option_row(
            self.meta_analysis,
            "Multi-study meta-analysis",
            "Combine two or more studies with per-study DESeq2, inverse-normal p-value combination and effect-size pooling. Ignored for single-study, microarray and external-results routes."), 1, 0)
        optional_grid.addWidget(option_row(
            self.per_study_enrichment,
            "Per-study enrichment",
            "Run GO and KEGG enrichment separately for every study in the meta-analysis. This optional step is slower and requires multi-study meta-analysis."), 1, 1)
        optional_grid.setColumnStretch(0, 1)
        optional_grid.setColumnStretch(1, 1)
        out_layout.addLayout(optional_grid)
        self.out_group = out_group

        # Custom collections are an opt-in branch rather than part of the first
        # run.  Keep them available in-flow, but avoid opening the Workflow page
        # with another large card and several unused path fields.
        custom_sets_section = QWidget()
        custom_sets_layout = QVBoxLayout(custom_sets_section)
        custom_sets_layout.setContentsMargins(0, 0, 0, 0)
        custom_sets_layout.setSpacing(4)
        (self.custom_gene_sets_toggle,
         self.custom_gene_sets_panel) = self._disclosure(
            "Custom gene sets (enrichment, optional)", expanded=False)
        self.custom_gene_sets_toggle.setObjectName("customGeneSetsToggle")
        self.custom_gene_sets_panel.setObjectName("customGeneSetsPanel")
        self.custom_gene_sets_toggle.setToolTip(
            "Optional GMT, annotation and background inputs for custom enrichment.")
        self.custom_gene_sets_toggle.toggled.connect(
            self._schedule_workflow_section_height_update)
        custom_sets_layout.addWidget(self.custom_gene_sets_toggle)
        custom_sets_layout.addWidget(self.custom_gene_sets_panel)
        cs_form = QFormLayout(self.custom_gene_sets_panel)
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
        workflow_intro = self._page_intro(
            "Analysis settings",
            "Resolve the input route, comparison direction and active analysis modules for this project.")
        self.workflow_intro = workflow_intro
        self.workflow_summary = workflow_intro.findChild(QLabel, "pagePurposeText")
        self.workflow_summary.setAccessibleName("Current analysis plan")
        layout.addWidget(workflow_intro)

        section_tabs = QTabWidget()
        section_tabs.setObjectName("workflowSectionTabs")
        section_tabs.setMinimumHeight(360)
        section_tabs.setMaximumHeight(520)
        section_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        def section_page(*cards: QWidget) -> QScrollArea:
            section = QWidget()
            section_layout = QVBoxLayout(section)
            # Keep flat group titles visibly inside the tab pane. With zero
            # margins, titles such as "Differential expression" sat almost on
            # the pane border and read like a clipped tab label.
            section_layout.setContentsMargins(12, 8, 12, 8)
            # Insert gaps only between real sections. QBoxLayout spacing also
            # applies before a trailing stretch, which created an otherwise
            # empty 8 px scroll range as soon as a disclosure was expanded.
            section_layout.setSpacing(0)
            for index, card in enumerate(cards):
                if index:
                    section_layout.addSpacing(8)
                section_layout.addWidget(card)
            section_layout.addStretch(1)
            return self._scrollable(section)

        section_tabs.addTab(section_page(de_group), "Comparison")
        section_tabs.addTab(section_page(align_group), "Read processing")
        self.custom_gene_sets_section = custom_sets_section
        section_tabs.addTab(section_page(out_group, custom_sets_section), "Output options")
        section_tabs.addTab(section_page(adv_group), "Advanced")
        layout.addWidget(section_tabs)
        self.workflow_section_tabs = section_tabs
        section_tabs.currentChanged.connect(self._schedule_workflow_section_height_update)

        save_bar = QWidget()
        save_row = QHBoxLayout()
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addWidget(QLabel("Changes are stored in the project configuration."))
        save_row.addStretch(1)
        save.setText("Save analysis settings")
        save_row.addWidget(save)
        save_bar.setLayout(save_row)
        layout.addWidget(save_bar)
        layout.addStretch(1)
        self.workflow_page = page
        self.workflow_page_layout = layout
        self.workflow_save_bar = save_bar
        page.installEventFilter(self)
        self.tabs.addTab(page, "Workflow Settings")
        self.tabs.currentChanged.connect(
            lambda index: self._schedule_workflow_section_height_update()
            if index == self.tabs.indexOf(page) else None)
        for signal in (
            self.aligner.currentTextChanged,
            self.quantifier.currentTextChanged,
            self.numerator.currentTextChanged,
            self.denominator.currentTextChanged,
            self.alpha.valueChanged,
            self.lfc_threshold.valueChanged,
        ):
            signal.connect(self._update_workflow_summary)
        self._sync_trimmer_controls()
        self._update_workflow_summary()
        self._schedule_workflow_section_height_update()

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
        layout.addWidget(self._page_intro(
            "Compute resources",
            "Detect the usable CPU and memory, choose a resource profile, then save the allocation "
            "for this project. Recommendations account for the WSL2 limit on Windows."))

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
            "CPU presets use the WSL logical CPU allocation (or host logical CPUs when WSL is "
            "unavailable), rounded down: Low 45%, Balanced 75%, and High 90%. Low uses about "
            "55% of WSL memory, Balanced up to about 75%, and High leaves 2 GB free. "
            "Custom keeps the cores and memory you set below."
        )
        profile_form.addRow(self._info_label("Profile", profile_help), self.profile)
        layout.addWidget(profile_group)

        # Manual limits are useful for experts, but the detected recommendation is
        # the safe default decision. Keep the lower-level values available without
        # making them compete with that primary path.
        manual_toggle = QToolButton()
        manual_toggle.setText("Manual CPU and memory limits")
        manual_toggle.setCheckable(True)
        manual_toggle.setChecked(False)
        manual_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        manual_toggle.setArrowType(Qt.ArrowType.RightArrow)
        manual_toggle.setAccessibleName("Show manual CPU and memory limits")
        layout.addWidget(manual_toggle)

        manual_group = QGroupBox("Manual Adjustment")
        manual_form = QFormLayout(manual_group)
        self.cores = QSpinBox()
        self.cores.setRange(1, 256)
        self.ram = QSpinBox()
        self.ram.setRange(1, 2048)
        manual_form.addRow(
            self._info_label(
                "CPU workers to use",
                "Maximum concurrent CPU workers available to the pipeline scheduler. "
                "Detect first to derive a safe value from the WSL logical CPU allocation "
                "(or host logical CPUs when WSL is unavailable).",
            ),
            self.cores,
        )
        manual_form.addRow(
            self._info_label("Memory (GB)",
                             "RAM allocated to the pipeline. Alignment (STAR) is the most memory-intensive step."),
            self.ram)
        save = QPushButton("Save Resources")
        save.setToolTip("Persist the CPU core and memory allocation above to the project config.")
        save.clicked.connect(self._save_resources)
        save.setEnabled(False)
        self.save_resources_button = save
        manual_group.setVisible(False)
        manual_toggle.toggled.connect(manual_group.setVisible)
        manual_toggle.toggled.connect(
            lambda expanded: manual_toggle.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        self.resource_manual_toggle = manual_toggle
        self.resource_manual_group = manual_group
        layout.addWidget(manual_group)

        save_row = QHBoxLayout()
        save_row.addWidget(save)
        save_row.addStretch(1)
        layout.addLayout(save_row)

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
        layout.setSpacing(10)
        layout.addWidget(self._page_intro(
            "Runtime estimate",
            "Estimate wall-clock time from the open project's sample count, input route and resource profile. "
            "Completed local runs calibrate later estimates on this machine."))
        (self.runtime_no_project_panel,
         _runtime_empty_title,
         _runtime_empty_body,
         _runtime_empty_action) = self._empty_state_panel(
            "Open a project to estimate runtime",
            "The estimate uses the project's input route, sample count and saved compute profile.",
            "Go to Project",
            lambda: self.tabs.setCurrentIndex(0),
        )
        self.runtime_no_project_panel.setMaximumHeight(210)
        layout.addWidget(self.runtime_no_project_panel)

        self.runtime_operational_panel = QWidget()
        runtime_layout = QVBoxLayout(self.runtime_operational_panel)
        runtime_layout.setContentsMargins(0, 0, 0, 0)
        runtime_layout.setSpacing(10)

        estimate = QPushButton("Estimate Runtime")
        estimate.setProperty("primary", True)
        estimate.setToolTip(
            "Estimate wall-clock runtime from your sample count, input mode, and resource settings, "
            "calibrated against past runs on this machine.")
        estimate.clicked.connect(self._estimate_runtime)
        estimate.setEnabled(False)
        self.runtime_estimate_button = estimate
        self.runtime_busy = self._busy_bar()
        result_group = QGroupBox("Estimate and assumptions")
        result_layout = QVBoxLayout(result_group)
        self.runtime_text = _PlainTextLabel()
        self.runtime_text.setProperty("uiRole", "statusBanner")
        self.runtime_text.setContentsMargins(12, 8, 12, 8)
        self.runtime_text.setAccessibleName("Runtime estimate and assumptions")
        self.runtime_text.setPlainText(
            "Ready to estimate. The predicted range, resource assumptions and calibration basis will appear here.")
        estimate_row = QHBoxLayout()
        estimate_row.addWidget(estimate)
        estimate_row.addStretch(1)
        runtime_layout.addLayout(estimate_row)
        runtime_layout.addWidget(self.runtime_busy)
        result_layout.addWidget(self.runtime_text)
        runtime_layout.addWidget(result_group)
        self.runtime_operational_panel.setVisible(False)
        layout.addWidget(self.runtime_operational_panel)
        layout.addStretch(1)
        self.tabs.addTab(self._scrollable(page), "Runtime")

    def _build_sanity_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(self._page_intro(
            "Pre-run checks",
            "Validate the project configuration, sample sheet, contrast and file paths before "
            "starting a full analysis. Review-required findings must be acknowledged; failures "
            "keep the run blocked until they are resolved."))

        buttons = QGridLayout()
        run = QPushButton("Validate current run inputs")
        run.setProperty("primary", True)
        # A button at its exact style size can clip the outer antialiasing pixel
        # of a leading glyph (notably the capital V on Windows). Reserve a
        # font-scaled guard instead of relying on a display-specific pixel fix.
        glyph_guard = max(2, (run.fontMetrics().horizontalAdvance(" ") + 1) // 2)
        # Some platform styles add a final pixel to sizeHint() after a minimum is
        # assigned. Two derived guards preserve at least one complete guard after
        # that recalculation without relying on a display-specific pixel constant.
        run.setMinimumWidth(run.sizeHint().width() + (2 * glyph_guard))
        run.setToolTip(
            "Persist the current settings, then validate the active input route, sample sheet, "
            "comparison direction, reference requirement and enrichment configuration.")
        run.clicked.connect(self._run_sanity_checks)
        refresh = QPushButton("Reload saved phase checks")
        refresh.setToolTip(
            "Reload phase-check JSON files already written by the workflow. This does not rerun them "
            "and does not replace validation of the current inputs.")
        refresh.clicked.connect(self._refresh_phase_checks)
        go_project = QPushButton("Go to Project")
        go_project.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        buttons.addWidget(run, 0, 0)
        buttons.addWidget(refresh, 0, 1)
        buttons.addWidget(go_project, 1, 0)
        buttons.setColumnStretch(2, 1)
        self.sanity_run_button = run
        self.sanity_refresh_button = refresh
        self.sanity_go_project = go_project

        results_group = QGroupBox("Validation results")
        results_layout = QVBoxLayout(results_group)
        self.sanity_state_label = QLabel()
        self.sanity_state_label.setWordWrap(True)
        self.sanity_state_label.setProperty("hint", True)
        self.approve_review = QCheckBox()
        self.approve_review.setVisible(False)
        self.sanity_busy = self._busy_bar()
        self.sanity_text = _PlainTextLabel()
        self.sanity_text.setProperty("uiRole", "statusBanner")
        self.sanity_text.setContentsMargins(12, 8, 12, 8)
        self.sanity_text.setAccessibleName("Pre-run check results")
        self.sanity_text.setVisible(False)
        results_layout.addWidget(self.sanity_state_label)
        results_layout.addWidget(self.sanity_busy)
        results_layout.addWidget(self.sanity_text)
        results_layout.addWidget(self.approve_review)

        self.sanity_next_label = QLabel()
        self.sanity_next_label.setWordWrap(True)
        self.sanity_go_run = QPushButton("Go to Run Monitor")
        self.sanity_go_run.clicked.connect(lambda: self.tabs.setCurrentIndex(8))
        next_row = QHBoxLayout()
        next_row.addWidget(self.sanity_next_label, 1)
        next_row.addWidget(self.sanity_go_run)
        layout.addLayout(buttons)
        layout.addWidget(results_group)
        layout.addLayout(next_row)
        layout.addStretch(1)
        self.sanity_results_group = results_group
        self._sanity_status_signature: tuple[tuple[str, str], ...] | None = None
        self._update_sanity_state({})
        self.tabs.addTab(self._scrollable(page), "Sanity Checks")

    def _build_run_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.addWidget(self._page_intro(
            "Run monitor",
            "Dry-run the plan, start or resume the workflow, and follow the current phase and detailed Snakemake log."))
        (self.run_empty_panel,
         self.run_empty_title,
         self.run_empty_body,
         self.run_go_project) = self._empty_state_panel(
            "No project open",
            "Open or create a project before starting a workflow.",
            "Go to Project",
            lambda: self.tabs.setCurrentIndex(0),
        )
        self.run_empty_panel.setMaximumHeight(190)
        layout.addWidget(self.run_empty_panel)

        self.run_operational_panel = QWidget()
        self.run_operational_panel.setObjectName("runOperationalPanel")
        self.run_operational_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        operational_layout = QVBoxLayout(self.run_operational_panel)
        operational_layout.setContentsMargins(0, 0, 0, 0)
        operational_layout.setSpacing(10)

        # Resume banner: shown when a project is reopened with an interrupted (locked/incomplete) run,
        # so stop -> close -> reopen -> continue is one click. Amber warning styling reads on both themes.
        self.resume_banner = QLabel()
        self.resume_banner.setWordWrap(True)
        self.resume_banner.setStyleSheet(self._advisory_banner_qss(self._current_theme_mode()))
        self.resume_banner.setVisible(False)
        self.resume_button = QPushButton("Resume Interrupted Run")
        self.resume_button.setProperty("buttonRole", "warning")
        self.resume_button.setVisible(False)
        self.resume_button.clicked.connect(self._resume_interrupted)
        _banner_row = QHBoxLayout()
        _banner_row.addWidget(self.resume_banner, 1)
        _banner_row.addWidget(self.resume_button, 0)
        operational_layout.addLayout(_banner_row)

        run_tips = {
            "dry-run": "Show what the pipeline would do, without running anything.",
            "run": "Start the pipeline. Completed steps are reused; only missing outputs are produced.",
            "resume": "Continue a stopped or interrupted run from where it left off "
                      "(re-runs only incomplete/missing steps — the project is the saved state).",
            "unlock": "Release a stale lock left by a killed run so you can start again.",
        }
        for text, mode in [
            ("Start Run", "run"),
            ("Dry Run", "dry-run"),
            ("Resume", "resume"),
            ("Unlock", "unlock"),
        ]:
            button = QPushButton(text)
            button.setObjectName(f"runAction_{mode.replace('-', '_')}")
            button.setToolTip(run_tips.get(mode, ""))
            if mode == "run":
                button.setProperty("primary", True)
            elif mode == "unlock":
                button.setProperty("buttonRole", "warning")
            button.clicked.connect(lambda _checked=False, m=mode: self._start_snakemake(m))
            button.setEnabled(False)
            self.run_action_buttons[mode] = button
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

        stop = QPushButton("Stop")
        stop.setProperty("buttonRole", "danger")
        stop.setEnabled(False)
        stop.setVisible(False)
        stop.setToolTip("Terminate the running pipeline. Already-completed steps are kept and can be resumed later.")
        stop.clicked.connect(self._stop_run)
        self.stop_button = stop

        run_section, run_section_layout = self._section_panel("Run workflow")
        run_section.setObjectName("runWorkflowSection")
        primary_actions = QHBoxLayout()
        primary_actions.setSpacing(8)
        primary_actions.addWidget(self.run_action_buttons["run"])
        primary_actions.addWidget(self.run_action_buttons["dry-run"])
        primary_actions.addStretch(1)
        primary_actions.addWidget(stop)
        run_section_layout.addLayout(primary_actions)

        self.run_options_toggle, self.run_options_panel = self._disclosure("Execution options")
        self.run_options_toggle.setObjectName("runExecutionOptionsToggle")
        self.run_options_panel.setObjectName("runExecutionOptionsPanel")
        options_layout = QHBoxLayout(self.run_options_panel)
        options_layout.setContentsMargins(22, 0, 0, 0)
        options_layout.setSpacing(8)
        options_layout.addWidget(self.use_wsl)
        options_layout.addWidget(self.run_action_buttons["resume"])
        options_layout.addWidget(self.run_action_buttons["unlock"])
        options_layout.addStretch(1)
        run_section_layout.addWidget(self.run_options_toggle)
        run_section_layout.addWidget(self.run_options_panel)
        operational_layout.addWidget(run_section)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        # Qt paints one text colour across both the filled and unfilled halves.
        # No single colour stays legible while the label straddles those surfaces,
        # so keep the percentage in a separate label beside the phase instead.
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Workflow progress")
        self.progress_value_label = QLabel("0%")
        self.progress_value_label.setProperty("uiRole", "sectionHint")
        self.progress_value_label.setAccessibleName("Workflow progress percentage")
        self.progress_value_label.setVisible(False)
        self.progress.valueChanged.connect(
            lambda value: self.progress_value_label.setText(f"{int(value)}%"))
        self.elapsed_label = QLabel("Elapsed: 00:00:00")
        self.elapsed_label.setProperty("uiRole", "sectionHint")
        self.elapsed_label.setVisible(False)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)
        self._run_start = 0.0
        self.status_label = QLabel("Ready — configure the project, then start the workflow.")
        self.status_label.setWordWrap(True)
        self.phase_label = QLabel("")
        self.phase_label.setProperty("uiRole", "sectionHint")

        progress_section, progress_section_layout = self._section_panel("Workflow status")
        progress_section.setObjectName("runProgressSection")
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.status_label, 1)
        progress_section_layout.addLayout(status_row)
        phase_row = QHBoxLayout()
        phase_row.setSpacing(8)
        phase_row.addWidget(self.phase_label)
        phase_row.addStretch(1)
        phase_row.addWidget(self.progress_value_label)
        phase_row.addWidget(self.elapsed_label)
        progress_section_layout.addLayout(phase_row)
        progress_section_layout.addWidget(self.progress)
        operational_layout.addWidget(progress_section)

        # These are deliberately short: all post-run destinations remain visible
        # in one predictable row at a compact desktop width; their tooltips carry
        # the explanatory wording instead of forcing the monitor to overflow.
        open_folder = QPushButton("Folder")
        open_folder.setAccessibleName("Open project folder")
        open_folder.setToolTip("Open the project's root directory in the system file browser.")
        open_folder.clicked.connect(self._open_folder)
        open_report = QPushButton("MultiQC")
        open_report.setAccessibleName("Open MultiQC report")
        open_report.setToolTip(
            "Open the aggregated MultiQC quality-control report (read QC, alignment/quantification "
            "metrics) in your browser. Produced when a run finishes.")
        open_report.clicked.connect(self._open_report)
        open_html = QPushButton("Results")
        open_html.setAccessibleName("Open results report")
        open_html.setToolTip(
            "Open the self-contained HTML results report (figures, top genes, enrichment, and "
            "provenance in one file) in your browser. Produced when a run finishes.")
        open_html.clicked.connect(self._open_results_report)
        self.export_toolsref_button = QPushButton("References")
        self.export_toolsref_button.setAccessibleName("Export tools and references")
        self.export_toolsref_button.setToolTip(
            "Save a text file listing the tool versions, reference genome/annotation (accession, "
            "source, MD5) and enrichment database sources used in this run. Available after the run completes.")
        self.export_toolsref_button.clicked.connect(self._export_tools_references)
        self.export_design_button = QPushButton("Design")
        self.export_design_button.setAccessibleName("Export study design")
        self.export_design_button.setToolTip(
            "Save a text file describing the study design: samples, conditions, layout, the DESeq2 "
            "design formula and contrasts. Available after the run completes.")
        self.export_design_button.clicked.connect(self._export_study_design)
        self.open_project_folder_button = open_folder
        self.open_multiqc_button = open_report
        self.open_results_report_button = open_html
        self.run_project_buttons = [open_folder, open_report, open_html]
        for button in self.run_project_buttons:
            button.setEnabled(False)

        after_section, after_section_layout = self._section_panel("After the run")
        after_section.setObjectName("runAfterSection")
        after_actions = QHBoxLayout()
        after_actions.setSpacing(8)
        after_actions.addWidget(open_html)
        after_actions.addWidget(open_report)
        after_actions.addWidget(self.export_design_button)
        after_actions.addWidget(self.export_toolsref_button)
        after_actions.addWidget(open_folder)
        after_actions.addStretch(1)
        after_section_layout.addLayout(after_actions)
        operational_layout.addWidget(after_section)

        self.command_text = QLineEdit()
        self.command_text.setReadOnly(True)  # displays the launched command; not user-editable
        self.command_text.setProperty("uiRole", "codeOutput")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setProperty("uiRole", "codeOutput")
        self.log_text.setPlaceholderText("The Snakemake log streams here once a run starts.")
        self.log_text.setMinimumHeight(180)

        details_section, details_section_layout = self._section_panel("Execution details")
        details_section.setObjectName("runExecutionDetailsSection")
        self.execution_details_toggle, self.execution_details_panel = self._disclosure(
            "Show command and log")
        self.execution_details_toggle.setObjectName("runExecutionDetailsToggle")
        self.execution_details_panel.setObjectName("runExecutionDetailsPanel")
        details_layout = QVBoxLayout(self.execution_details_panel)
        details_layout.setContentsMargins(22, 0, 0, 0)
        details_layout.setSpacing(6)
        command_label = QLabel("Command")
        command_label.setProperty("uiRole", "sectionHint")
        log_label = QLabel("Detailed log")
        log_label.setProperty("uiRole", "sectionHint")
        details_layout.addWidget(command_label)
        details_layout.addWidget(self.command_text)
        details_layout.addWidget(log_label)
        details_layout.addWidget(self.log_text)
        details_section_layout.addWidget(self.execution_details_toggle)
        details_section_layout.addWidget(self.execution_details_panel)
        operational_layout.addWidget(details_section)

        self._execution_details_had_content = False
        self.command_text.textChanged.connect(self._sync_execution_details)
        self.log_text.textChanged.connect(self._sync_execution_details)

        layout.addWidget(self.run_operational_panel)
        layout.addStretch(1)
        self.run_monitor_page = self._scrollable(page)
        self.tabs.addTab(self.run_monitor_page, "Run Monitor")
        self._refresh_export_buttons()

    def _sync_execution_details(self) -> None:
        """Keep technical output available without forcing the page to grow."""
        if not hasattr(self, "execution_details_toggle"):
            return
        has_content = bool(
            self.command_text.text().strip() or self.log_text.toPlainText().strip())
        if not has_content:
            self.execution_details_toggle.setChecked(False)
        self._execution_details_had_content = has_content

    def _set_run_status(self, text: str, status: str | None = None) -> None:
        """Set the run-status label from a *semantic* status key, not a hex colour.

        Literal hex would be a light-palette value painted on whichever background
        is active, so a completed run in dark mode rendered #2E7D32 on #1A1D23
        (3.29:1, below the 4.5:1 AA floor theme.py claims for every token).
        The key is remembered so _toggle_theme can repaint it.
        """
        self._run_status_key = status
        self.status_label.setText(text)
        self.status_label.setStyleSheet(self._status_label_qss(status))

    def _set_progress_status(self, status: str | None = None) -> None:
        """Apply a semantic progress-bar state that can be repainted on theme change."""
        self._progress_status_key = status
        if status == "FAIL":
            self.progress.setStyleSheet(
                "QProgressBar::chunk { background-color: "
                f"{status_color('FAIL', self._current_theme_mode())}; }}")
        else:
            self.progress.setStyleSheet("")

    def _status_label_qss(self, status: str | None) -> str:
        if not status:
            return ""
        mode = self._current_theme_mode()
        # RUNNING is an activity signal, not a check outcome, so it takes the
        # primary accent rather than a PASS/WARNING/FAIL colour.
        colour = PALETTES[mode]["PRIMARY"] if status == "RUNNING" else status_color(status, mode)
        return f"color: {colour}; font-weight: 600;"

    def _repaint_themed_labels(self, mode: str | None = None) -> None:
        """Re-apply every per-widget stylesheet that encodes a palette colour.

        The application QSS is regenerated on a theme switch, but a stylesheet set
        directly on a widget is not, so these would keep their old palette's colours.
        """
        mode = mode or self._current_theme_mode()
        palette = PALETTES[mode]
        if hasattr(self, "status_label"):
            self.status_label.setStyleSheet(self._status_label_qss(getattr(self, "_run_status_key", None)))
        if hasattr(self, "reference_mode_banner"):
            self.reference_mode_banner.setStyleSheet(self._advisory_banner_qss(mode))
        if hasattr(self, "enrichment_warn"):
            self.enrichment_warn.setStyleSheet(f"color: {status_color('WARNING', mode)};")
        if hasattr(self, "contrast_info"):
            self.contrast_info.setStyleSheet(f"color: {palette['MUTED_TEXT']};")
        if hasattr(self, "resume_banner"):
            self.resume_banner.setStyleSheet(self._advisory_banner_qss(mode))
        if hasattr(self, "progress"):
            self._set_progress_status(getattr(self, "_progress_status_key", None))

    @staticmethod
    def _advisory_banner_qss(mode: str) -> str:
        # Amber callout so the count-matrix/microarray guidance reads as an advisory
        # the user should act on, not a greyed-out aside. Tint and border come from
        # the active palette so the banner stays legible in both themes.
        fg = status_color("WARNING", mode)
        bg = STATUS_PILL_BG[mode]["WARNING"]
        return (f"font-weight: 600; color: {fg}; background: {bg}; "
                f"border: 1px solid {fg}; border-radius: 4px; padding: 6px;")

    def _set_running_ui(self, active: bool) -> None:
        # Only run/resume hold a live process; dry-run/unlock are short-lived but
        # still gate Start to avoid concurrent snakemake against one directory.
        self._run_active = active
        for button in self.run_action_buttons.values():
            button.setEnabled(not active and self.project_root is not None)
        if self.stop_button is not None:
            self.stop_button.setEnabled(active)
            self.stop_button.setVisible(active and self.project_root is not None)
        if hasattr(self, "use_wsl"):
            self.use_wsl.setEnabled(not active and self.project_root is not None)
        if hasattr(self, "elapsed_label"):
            self.elapsed_label.setVisible(
                active or self.elapsed_label.text() != "Elapsed: 00:00:00")
        if active:
            self._set_progress_status()
            self.progress_value_label.setVisible(True)
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
        ("ingest_deseq2_results", "Reading the external differential-expression table"),
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
        generic_resume = self.run_action_buttons.get("resume")
        generic_unlock = self.run_action_buttons.get("unlock")
        if generic_resume is not None:
            generic_resume.setVisible(show)
        if generic_unlock is not None:
            generic_unlock.setVisible(bool(state.get("locked")))
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
            self._set_progress_status()
            self._set_run_status("Stopped", "WARNING")
            self.phase_label.setText("")
            self.log_text.append("Run stopped.")
            return
        # Treat a snakemake-reported failure as failure even when the exit code is 0
        # (the WSL `micromamba run` launcher masks non-zero codes to 0).
        failed_in_output = getattr(self, "_run_error_detected", False)
        if code == 0 and not failed_in_output:
            self.progress.setValue(100)
            self._set_progress_status()
            success_labels = {
                "dry-run": (
                    "Dry run completed",
                    "Plan checked — no analysis steps were executed.",
                ),
                "unlock": ("Project unlocked", "The workflow lock was released."),
                "figures": ("Figures regenerated", "Publication figures were updated."),
                "ppi": ("Protein network rebuilt", "The STRING network was updated."),
                "goi": ("Gene figures generated", "Genes-of-interest outputs were updated."),
                "term": ("Term heatmap generated", "The enrichment-term heatmap was updated."),
            }
            status_text, phase_text = success_labels.get(was_mode, ("Completed", ""))
            self._set_run_status(status_text, "PASS")
            self.phase_label.setText(phase_text)
            self.progress_value_label.setVisible(True)
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
                    "Run complete. Open Explore results > Figures and tables, or "
                    "Explore results > Protein network.", 20000)
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
            self._set_progress_status("FAIL")
            status = "Failed — a rule reported an error (see the log)" if failed_in_output and code == 0 \
                else f"Failed (exit code {code})"
            self._set_run_status(status, "FAIL")
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
            self._set_run_status("Stopping...", "WARNING")
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
        if hasattr(self, "runtime_estimate_button"):
            self.runtime_estimate_button.setEnabled(root is not None)
        if hasattr(self, "runtime_no_project_panel"):
            self.runtime_no_project_panel.setVisible(root is None)
        if hasattr(self, "runtime_operational_panel"):
            self.runtime_operational_panel.setVisible(root is not None)
        if hasattr(self, "run_empty_panel"):
            self.run_empty_panel.setVisible(root is None)
        if hasattr(self, "run_operational_panel"):
            project_open = root is not None
            state_changed = self.run_operational_panel.isHidden() == project_open
            self.run_operational_panel.setVisible(project_open)
            if state_changed and hasattr(self, "run_monitor_page"):
                # Replacing the compact empty state with the taller operational
                # workspace can preserve an obsolete scroll offset and hide the page
                # heading. A new project state always starts at the top of the task.
                self.run_monitor_page.verticalScrollBar().setValue(0)
        for button in getattr(self, "run_action_buttons", {}).values():
            button.setEnabled(root is not None and not self._run_active)
        if hasattr(self, "use_wsl"):
            self.use_wsl.setEnabled(root is not None and not self._run_active)
        if self.stop_button is not None:
            self.stop_button.setVisible(root is not None and self._run_active)
            self.stop_button.setEnabled(root is not None and self._run_active)
        if hasattr(self, "resume_banner"):
            self._refresh_resume_banner()
        if hasattr(self, "open_project_folder_button"):
            self.open_project_folder_button.setEnabled(root is not None)
        if hasattr(self, "open_multiqc_button"):
            self.open_multiqc_button.setEnabled(bool(
                root and (root / "results" / "qc" / "multiqc" / "multiqc_report.html").exists()))
        if hasattr(self, "open_results_report_button"):
            self.open_results_report_button.setEnabled(bool(
                root and (root / "results" / "reports" / "results_report.html").exists()))
        if hasattr(self, "save_resources_button"):
            self.save_resources_button.setEnabled(root is not None)
        for button in getattr(self, "report_project_buttons", []):
            button.setEnabled(root is not None)
        if hasattr(self, "report_no_project_panel"):
            self.report_no_project_panel.setVisible(root is None)
        if hasattr(self, "report_operational_panel"):
            self.report_operational_panel.setVisible(root is not None)
        for control in getattr(self, "output_project_controls", []):
            control.setEnabled(root is not None)
        if hasattr(self, "output_no_project_panel"):
            self.output_no_project_panel.setVisible(root is None)
        if hasattr(self, "output_controls_widget"):
            self.output_controls_widget.setVisible(root is not None)
        if hasattr(self, "_outputs_main_splitter"):
            self._outputs_main_splitter.setVisible(root is not None)
        if hasattr(self, "results_inspector"):
            self.results_inspector.setVisible(root is not None)
        if hasattr(self, "ppi_load_button"):
            self.ppi_load_button.setEnabled(root is not None)
        for control in getattr(self, "ppi_construction_controls", []):
            control.setEnabled(root is not None)
        if hasattr(self, "ppi_go_project"):
            self.ppi_go_project.setVisible(root is None)
        if hasattr(self, "ppi_no_project_panel"):
            self.ppi_no_project_panel.setVisible(root is None)
        if hasattr(self, "ppi_command_widget"):
            self.ppi_command_widget.setVisible(root is not None)
        if hasattr(self, "ppi_workspace"):
            self.ppi_workspace.setVisible(root is not None)
        if hasattr(self, "ppi_inspector"):
            self.ppi_inspector.setVisible(root is not None)
            if root is not None:
                self.ppi_inspector.setCurrentIndex(1)
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
        layout.setSpacing(10)
        layout.addWidget(self._page_intro(
            "Reports and provenance",
            "Generate the consolidated results report after a run, or open the quality-control and "
            "results reports already produced for the current project."))
        (self.report_no_project_panel,
         _report_empty_title,
         _report_empty_body,
         _report_empty_action) = self._empty_state_panel(
            "No project open",
            "Open or create a project to generate reports or review saved provenance and quality-control outputs.",
            "Go to Project",
            lambda: self.tabs.setCurrentIndex(0),
        )
        layout.addWidget(self.report_no_project_panel, 1)

        self.report_operational_panel = QWidget()
        operational_layout = QVBoxLayout(self.report_operational_panel)
        operational_layout.setContentsMargins(0, 0, 0, 0)
        operational_layout.setSpacing(10)
        generate = QPushButton("Generate Reports")
        generate.setProperty("primary", True)
        generate.clicked.connect(self._generate_reports)
        open_results = QPushButton("Open Results Report")
        open_results.clicked.connect(self._open_results_report)
        open_multiqc = QPushButton("Open MultiQC Report")
        open_multiqc.clicked.connect(self._open_report)
        open_folder = QPushButton("Open reports folder")
        open_folder.clicked.connect(lambda: self._open_subpath("results/reports"))
        self.report_project_buttons = [generate, open_results, open_multiqc, open_folder]
        for button in self.report_project_buttons:
            button.setEnabled(False)
        result_group = QGroupBox("Report status")
        result_layout = QVBoxLayout(result_group)
        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setMinimumHeight(140)
        self.report_text.setMaximumHeight(320)
        self.report_text.setPlainText(
            "No reports yet. Complete a run, then generate or open the saved reports from this page.")
        report_actions = QGridLayout()
        report_actions.setHorizontalSpacing(8)
        report_actions.setVerticalSpacing(6)
        for index, button in enumerate(self.report_project_buttons):
            report_actions.addWidget(button, index // 2, index % 2)
        report_actions.setColumnStretch(2, 1)
        operational_layout.addLayout(report_actions)
        result_layout.addWidget(self.report_text)
        operational_layout.addWidget(result_group)
        operational_layout.addStretch(1)
        layout.addWidget(self.report_operational_panel, 1)
        self.report_no_project_panel.setVisible(True)
        self.report_operational_panel.setVisible(False)
        self.tabs.addTab(self._scrollable(page), "Reports")

    def _build_outputs_tab(self) -> None:
        # Resizable workspace: a vertical splitter separates the table (top) from
        # the figure area (bottom); inside the figure area a horizontal splitter
        # separates the figure viewer (left) from the tabbed controls (right).
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._page_intro(
            "Figures and tables",
            "Browse result tables and publication-ready figures. Editing tools appear only when a project is open."))
        (self.output_no_project_panel,
         _output_no_project_title,
         _output_no_project_body,
         _output_no_project_action) = self._empty_state_panel(
            "Open a project to explore outputs",
            "Figures, tables, and editing tools are organised by project. Open or create one to begin.",
            "Go to Project",
            lambda: self.tabs.setCurrentIndex(0),
        )
        layout.addWidget(self.output_no_project_panel, 1)

        # Table picker row.
        self.output_controls_widget = QWidget()
        controls = QHBoxLayout(self.output_controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)
        self.output_table_pick = QComboBox()
        self.output_table_pick.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.output_table_pick.setMinimumContentsLength(18)
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
        layout.addWidget(self.output_controls_widget)

        # --- Table panel (top of the vertical splitter) ---
        table_panel = QWidget()
        # Splitter sub-control styling can colour the splitter's own palette. Keep
        # the content panes opaque so that handle accents never bleed through them.
        table_panel.setAutoFillBackground(True)
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(8, 6, 8, 8)
        table_layout.setSpacing(6)
        self.output_table_heading = QLabel("Table preview (first 200 rows)")
        table_layout.addWidget(self.output_table_heading)
        self.output_table = QTableWidget()
        self.output_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Click a header to sort the loaded preview (numeric columns sort numerically
        # via _SortableItem). Toggled off during (re)population in _load_output_table.
        self.output_table.setSortingEnabled(True)
        self.output_table.horizontalHeader().setSortIndicatorShown(True)
        self.output_table.setMinimumHeight(48)
        self.output_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table_layout.addWidget(self.output_table)

        # --- Figure panel (left of the horizontal splitter) ---
        figure_panel = QWidget()
        figure_panel.setAutoFillBackground(True)
        figure_layout = QVBoxLayout(figure_panel)
        figure_layout.setContentsMargins(8, 6, 8, 8)
        figure_layout.setSpacing(6)
        figure_header = QLabel("Figures — scroll to zoom, drag to pan")
        figure_header.setWordWrap(True)
        figure_header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.output_figure_heading = figure_header
        figure_layout.addWidget(figure_header)
        fig_select = QHBoxLayout()
        self.figure_pick = QComboBox()
        self.figure_pick.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.figure_pick.setMinimumContentsLength(16)
        self.figure_pick.currentTextChanged.connect(self._show_selected_figure)
        self.figure_pick.addItem("(open a project to browse figures)")
        self.figure_pick.setEnabled(False)
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
        fig_select.addWidget(QLabel("Figure:"))
        fig_select.addWidget(self.figure_pick, 1)
        fig_select.addWidget(self.svg_toggle)
        figure_layout.addLayout(fig_select)
        render_actions = QHBoxLayout()
        render_actions.addWidget(regen_figs)
        render_actions.addWidget(refresh_figs)
        render_actions.addStretch(1)
        render_actions.addWidget(QLabel("View"))
        render_actions.addWidget(fit_btn)
        render_actions.addWidget(actual_btn)
        figure_layout.addLayout(render_actions)
        self.regenerate_figures_button = regen_figs
        figure_canvas = QWidget()
        figure_canvas_layout = QStackedLayout(figure_canvas)
        figure_canvas_layout.setContentsMargins(0, 0, 0, 0)
        figure_canvas_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.figure_viewer = ImageViewer()
        self.figure_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Keep the figure scientifically inspectable even when a saved splitter
        # state from a larger screen is restored at the compact desktop size.
        self.figure_viewer.setMinimumSize(300, 280)
        self.figure_viewer.update_theme(IMAGEVIEWER_BG.get(self._current_theme_mode(), IMAGEVIEWER_BG["light"]))
        figure_canvas_layout.addWidget(self.figure_viewer)
        (self.output_empty_state_panel,
         self.output_empty_title,
         self.output_empty_state,
         self.output_empty_action) = self._empty_state_panel(
            "No project open",
            "Open or create a project to browse stored figures and tables. "
            "Figure editing becomes available with the project.",
            "Go to Project",
            self._go_from_output_empty_state,
        )
        figure_canvas_layout.addWidget(self.output_empty_state_panel)
        figure_canvas_layout.setCurrentWidget(self.output_empty_state_panel)
        self.output_figure_stack = figure_canvas_layout
        self.output_figure_canvas = figure_canvas
        figure_layout.addWidget(figure_canvas, 1)

        # --- Progressive inspector (right of the horizontal splitter) ---
        control_panel = _InspectorTabs()
        control_panel.setObjectName("resultsInspector")
        control_panel.setAccessibleName("Results editing panels")
        control_panel.setMinimumWidth(320)
        control_panel.setMaximumWidth(420)
        control_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        control_panel.addItem(self._build_figure_style_group(), "Style")
        # The gene editor owns its scrolling.  Keeping its action footer outside
        # another scroll area makes Save/Generate permanently reachable.
        control_panel.addItem(self._build_goi_group(), "Genes")
        control_panel.addItem(self._inspector_scrollable(self._build_enrichment_terms_group()), "Terms")
        # Compact tab labels keep the entire inspector navigation visible. The
        # full purpose remains available to pointer and assistive-technology users.
        control_panel.setItemToolTip(0, "Global appearance, detail, size and per-figure overrides")
        control_panel.setItemToolTip(1, "Genes of interest: inspect selected genes and create focused figures")
        control_panel.setItemToolTip(2, "Enrichment terms: extract genes or build a heatmap from a term")
        self.results_inspector = control_panel
        self.output_project_controls = [
            self.output_table_pick, load, open_results, regen_figs, refresh_figs,
            fit_btn, actual_btn, self.svg_toggle, control_panel,
        ]
        for control in self.output_project_controls:
            control.setEnabled(False)
        control_panel.setVisible(False)

        inspector_host = QWidget()
        inspector_host.setAutoFillBackground(True)
        inspector_host.setMinimumWidth(328)
        inspector_host.setMaximumWidth(428)
        inspector_host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        inspector_layout = QVBoxLayout(inspector_host)
        inspector_layout.setContentsMargins(8, 6, 0, 8)
        inspector_layout.addWidget(control_panel)
        self._outputs_inspector_host = inspector_host

        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        results_splitter.setChildrenCollapsible(False)
        results_splitter.setHandleWidth(6)
        results_splitter.addWidget(figure_panel)
        results_splitter.addWidget(inspector_host)
        results_splitter.setStretchFactor(0, 1)
        results_splitter.setStretchFactor(1, 0)
        results_splitter.setSizes([700, 420])
        self._outputs_results_splitter = results_splitter

        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.setChildrenCollapsible(True)
        main_splitter.setHandleWidth(8)
        main_splitter.addWidget(table_panel)
        main_splitter.addWidget(results_splitter)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)
        # The table is secondary to figure inspection and starts collapsed. Loading
        # a table expands it to a useful preview without permanently consuming the
        # compact viewport.
        main_splitter.setSizes([0, 680])
        self._outputs_main_splitter = main_splitter
        self._outputs_table_panel = table_panel

        layout.addWidget(main_splitter, 1)

        # Restore saved splitter positions, if any.
        s = QSettings()
        for key, sp in (("_outputs_main_splitter", main_splitter), ("_outputs_results_splitter", results_splitter)):
            st = s.value(f"outputs/v3/{key}", QByteArray())
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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._page_intro(
            "Protein interaction network",
            "Explore the STRING network from differential-expression or genes-of-interest results. "
            "View filters never rebuild the network; construction settings explicitly replace its edge set."))
        (self.ppi_no_project_panel,
         _ppi_no_project_title,
         _ppi_no_project_body,
         _ppi_no_project_action) = self._empty_state_panel(
            "Open a project to explore a protein network",
            "BulkSeq Studio builds the STRING network from the current project's results or genes of interest.",
            "Go to Project",
            lambda: self.tabs.setCurrentIndex(0),
        )
        layout.addWidget(self.ppi_no_project_panel, 1)

        row1 = QHBoxLayout()
        load_btn = QPushButton("Load / refresh network")
        load_btn.setProperty("primary", True)
        load_btn.setToolTip("Assemble the network from this project's results and display it.")
        load_btn.clicked.connect(self._load_ppi_network)
        self.ppi_load_button = load_btn
        load_btn.setEnabled(False)
        # Keep the compact control labels short enough to remain readable beside
        # the network. The full statistical/layout meaning stays in tooltips and
        # accessibility metadata; internal Cytoscape values remain userData.
        self.ppi_layout_pick = QComboBox()
        for label, val in [("Force-directed", "fcose"), ("Compact", "cose"),
                           ("Circle", "circle"), ("Concentric", "concentric"), ("Grid", "grid")]:
            self.ppi_layout_pick.addItem(label, val)
        self.ppi_layout_pick.setAccessibleName("Network layout")
        self.ppi_layout_pick.setToolTip(
            "Network layout. Force-directed uses the fCoSE algorithm; Compact uses CoSE.")
        self.ppi_layout_pick.currentIndexChanged.connect(
            lambda _i: self.ppi_viewer.set_layout(self.ppi_layout_pick.currentData()))
        self.ppi_color_pick = QComboBox()
        for label, val in [("Fold change", "log2FoldChange"), ("Module / cluster", "module")]:
            self.ppi_color_pick.addItem(label, val)
        self.ppi_color_pick.setAccessibleName("Node colour encoding")
        self.ppi_color_pick.setToolTip(
            "Colour nodes by log₂ fold change or detected module / cluster.")
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
                           ("Significance", "neglog10padj")]:
            self.ppi_size_pick.addItem(label, val)
        self.ppi_size_pick.setAccessibleName("Node size encoding")
        self.ppi_size_pick.setToolTip(
            "Size nodes by degree, mean expression, or significance (−log₁₀ adjusted p-value).")
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
        self.ppi_rebuild_score = self.ppi_score
        self.ppi_rebuild_score.setToolTip("STRING combined-score cutoff to rebuild at (0-1000; 400 = "
                                          "medium, 700 = high confidence). Lower it to pull in weaker "
                                          "interactions, then click Rebuild.")
        self.ppi_rebuild_score.valueChanged.connect(self._sync_score_to_figstyle)
        row2.addWidget(self.ppi_rebuild_score)
        rebuild_btn = QPushButton("Rebuild from STRING…")
        rebuild_btn.setToolTip("Re-contact string-db.org and rebuild the network at the 'Rebuild at "
                               "score' shown to the left. This replaces the current network.")
        rebuild_btn.clicked.connect(self._regenerate_ppi)
        self.ppi_rebuild_button = rebuild_btn
        rebuild_btn.setEnabled(False)
        row2.addWidget(rebuild_btn)

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

        self.ppi_status = QLabel("No network loaded — click “Load / refresh network”.")
        self.ppi_status.setWordWrap(True)
        self.ppi_go_project = QPushButton("Go to Project")
        self.ppi_go_project.clicked.connect(lambda: self.tabs.setCurrentIndex(0))

        self.ppi_viewer = PpiViewer()
        self.ppi_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ppi_viewer.setMinimumHeight(360)
        self.ppi_viewer.update_theme(self._ppi_theme_palette())
        self.ppi_view_controls = [
            self.ppi_layout_pick,
            self.ppi_color_pick,
            self.ppi_view_pick,
            self.ppi_size_pick,
            self.ppi_labels_cb,
            self.ppi_italic_cb,
            self.ppi_focus_cb,
            self.ppi_conf,
        ]
        self._set_ppi_network_controls(False)

        self.ppi_command_widget = QWidget()
        command_row = QHBoxLayout(self.ppi_command_widget)
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.addWidget(load_btn)
        command_row.addWidget(self.ppi_status, 1)
        command_row.addWidget(self.ppi_go_project)
        layout.addWidget(self.ppi_command_widget)

        inspector = _InspectorTabs()
        inspector.setObjectName("ppiInspector")
        inspector.setAccessibleName("Protein network controls")
        inspector.setMinimumWidth(320)
        inspector.setMaximumWidth(420)
        inspector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        view_page = QWidget()
        view_form = QFormLayout(view_page)
        view_form.setProperty("narrowInspector", True)
        view_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        view_form.addRow("Layout", self.ppi_layout_pick)
        view_form.addRow("Colour by", self.ppi_color_pick)
        view_form.addRow("Show proteins", self.ppi_view_pick)
        view_form.addRow("Node size", self.ppi_size_pick)
        label_options = QWidget()
        label_options_layout = QVBoxLayout(label_options)
        label_options_layout.setContentsMargins(0, 0, 0, 0)
        label_options_layout.addWidget(self.ppi_labels_cb)
        label_options_layout.addWidget(self.ppi_italic_cb)
        label_options_layout.addWidget(self.ppi_focus_cb)
        labels_heading = QLabel("Labels")
        labels_heading.setProperty("uiRole", "sectionLabel")
        view_form.addRow(labels_heading)
        view_form.addRow(label_options)
        filter_holder = QWidget()
        filter_layout = QHBoxLayout(filter_holder)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.addWidget(self.ppi_conf, 1)
        filter_layout.addWidget(self.ppi_conf_lbl)
        filter_holder.setToolTip(
            "Hide loaded edges below this confidence in the current view. This does not rebuild the network.")
        filter_holder.setAccessibleName("Current-view edge confidence filter")
        view_form.addRow("Edge filter", filter_holder)
        inspector.addItem(self._inspector_scrollable(view_page), "View")
        inspector.setItemToolTip(0, "Current-view layout, colour, size, labels and edge filter")

        network_page = QWidget()
        network_form = QFormLayout(network_page)
        network_form.setProperty("narrowInspector", True)
        network_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        network_note = QLabel(
            "Rebuild contacts STRING and replaces the network edge set. The confidence control in the View tab only hides loaded edges."
        )
        network_note.setWordWrap(True)
        network_note.setProperty("hint", True)
        network_form.addRow(network_note)
        network_form.addRow("STRING combined score (0–1000)", self.ppi_score)
        network_form.addRow("Hub labels in static figure", self.ppi_hub_labels)
        network_form.addRow(rebuild_btn)
        inspector.addItem(self._inspector_scrollable(network_page), "Rebuild")
        inspector.setItemToolTip(1, "Rebuild and replace the STRING network edge set")
        self.ppi_construction_controls = [self.ppi_score, self.ppi_hub_labels, rebuild_btn]
        for control in self.ppi_construction_controls:
            control.setEnabled(False)

        export_page = QWidget()
        export_form = QFormLayout(export_page)
        export_form.setProperty("narrowInspector", True)
        export_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        export_form.addRow("Background", self.ppi_export_bg)
        export_form.addRow(export_png)
        export_form.addRow(export_svg)
        export_form.addRow(save_cyto)
        inspector.addItem(self._inspector_scrollable(export_page), "Export")
        inspector.setItemToolTip(2, "Export the current network view or Cytoscape files")

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("ppiWorkspace")
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(self.ppi_viewer)
        workspace.addWidget(inspector)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 0)
        workspace.setSizes([900, 340])
        layout.addWidget(workspace, 1)
        self.ppi_inspector = inspector
        self.ppi_workspace = workspace
        # At startup there is no project context, so view/rebuild/export controls
        # have no referent. The project lifecycle reveals the inspector and opens
        # the construction section when a project becomes available.
        self.ppi_inspector.setVisible(False)

        self.tabs.addTab(page, "PPI Network")
        # Outputs and PPI are built after Run Monitor's first lifecycle refresh.
        # Apply the current project state once their purposeful empty states exist.
        self._refresh_export_buttons()

    def _ppi_theme_palette(self) -> dict:
        mode = self._current_theme_mode()
        pal = PALETTES.get(mode, PALETTES["light"])
        return {
            "bg": IMAGEVIEWER_BG.get(mode, IMAGEVIEWER_BG["light"]),
            "surface": pal.get("SURFACE", "#ffffff"),
            "text": pal.get("TEXT", "#1a1a1a"),
            "edge": pal.get("CONTROL_BORDER", "#7d8996"),
            "muted": pal.get("MUTED_TEXT", "#8a8a8a"),
            "focus": pal.get("PRIMARY", "#2c6fb6"),
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
                self.ppi_viewer.clear_network(
                    "No PPI network is available yet. Run the pipeline or rebuild the STRING network.")
                self.ppi_status.setText("No PPI network found for this project yet.")
            self._set_ppi_network_controls(False)
            return
        from app.core.ppi_graph import build_ppi_cytoscape_json

        try:
            graph = build_ppi_cytoscape_json(self.project_root)
        except Exception as exc:  # never crash the tab
            self.ppi_viewer.clear_network(
                "The PPI network could not be assembled. Review the status message and project outputs.")
            self._set_ppi_network_controls(False)
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
        self._set_ppi_network_controls(has_network)
        if has_network and hasattr(self, "ppi_inspector"):
            self.ppi_inspector.setCurrentIndex(0)
        if hasattr(self, "ppi_export_png"):
            self.ppi_export_png.setEnabled(has_network)
            self.ppi_export_svg.setEnabled(has_network)
            self.ppi_export_png.setToolTip("" if has_network else "Load a network first.")
            self.ppi_export_svg.setToolTip("" if has_network else "Load a network first.")
            if hasattr(self, "ppi_save_cyto"):
                self.ppi_save_cyto.setEnabled(has_network)
                self.ppi_save_cyto.setToolTip("" if has_network else "Load a network first.")
        if not has_network:
            self.ppi_viewer.set_empty_state(
                "STRING returned no interactions for this project. Try a supported organism, "
                "check gene-symbol mapping, or adjust the Network construction threshold.")
            self.ppi_status.setText(
                "No PPI network for this run — STRING returned no interactions (the organism may "
                "lack STRING coverage, or its genes have no mapped symbols). The static figure, if any, "
                "is under Explore results > Figures and tables.")
        else:
            self.ppi_status.setText(
                f"{n_nodes} proteins, {n_edges} interactions. Hover a protein for details; "
                "click to highlight its neighbours; drag and scroll to explore.")

    def _set_ppi_network_controls(self, enabled: bool) -> None:
        """Gate only controls that operate on an already-loaded network."""
        for control in getattr(self, "ppi_view_controls", []):
            control.setEnabled(enabled)
        if hasattr(self, "ppi_export_bg"):
            self.ppi_export_bg.setEnabled(enabled)

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
            QMessageBox.information(
                self,
                APP_NAME,
                "Interactive export needs the web view; use the static figure under "
                "Explore results > Figures and tables instead.",
            )
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
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        full_help = (
            "Use the identifier format from this run's reference: locus tags (for example "
            "FBgn or FGSG identifiers), Ensembl/RefSeq IDs, or gene symbols present in the "
            "GTF. Unmatched IDs are reported. A completed count-based run can generate a "
            "z-scored heatmap, per-condition expression plots and a counts table; it can "
            "also seed a STRING network when PPI seeding uses this list."
        )
        help_label = QLabel(
            "Paste one gene ID per line. IDs must match the reference used for this run."
        )
        help_label.setWordWrap(True)
        help_label.setToolTip(full_help)
        help_label.setAccessibleName("Genes-of-interest instructions")
        help_label.setAccessibleDescription(full_help)
        v.addWidget(help_label)
        self.goi_box = QTextEdit()
        self.goi_box.setAcceptRichText(False)  # paste gene IDs as plain text, no source formatting
        self.goi_box.setPlaceholderText("One gene ID per line")
        self.goi_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.goi_box.setMinimumHeight(110)
        self.goi_box.setAccessibleName("Genes of interest")
        self.goi_box.setAccessibleDescription(full_help)
        save = QPushButton("Save gene list")
        save.setAccessibleName("Save genes of interest")
        save.setToolTip("Save this genes-of-interest list in the project configuration.")
        save.clicked.connect(self._save_goi)
        generate = QPushButton("Generate gene figures")
        generate.setAccessibleName("Generate genes-of-interest figures from existing results")
        generate.setToolTip("Build the genes-of-interest heatmap, expression plots, and table "
                            "from the already-computed DESeq2 results — no re-alignment or "
                            "re-analysis. Requires a completed run.")
        generate.clicked.connect(self._generate_goi)
        v.addWidget(self.goi_box)
        # A vertical footer fits the narrow inspector without eliding either
        # caption and remains visible while the text editor scrolls its content.
        goi_buttons = QVBoxLayout()
        goi_buttons.setSpacing(6)
        goi_buttons.addWidget(save)
        goi_buttons.addWidget(generate)
        v.addLayout(goi_buttons)
        self.goi_save_button = save
        self.goi_generate_button = generate
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
        # Long, explicit action names matter here: one action writes a table;
        # the other also builds a heatmap. A single horizontal row truncates
        # them in the 320--420 px inspector, so the actions stack deliberately.
        action_column = QVBoxLayout()
        action_column.setContentsMargins(0, 2, 0, 0)
        action_column.setSpacing(6)
        refresh = QPushButton("Refresh terms")
        refresh.setAccessibleName("Refresh enrichment terms")
        refresh.setToolTip("Reload extractable terms from the finished run.")
        refresh.clicked.connect(self._populate_term_picker)
        self.term_table_btn = QPushButton("Extract genes\n→ table")
        self.term_table_btn.setAccessibleName("Extract enrichment-term genes to table")
        self.term_table_btn.setToolTip("Write this term's genes with their DESeq2 stats to a CSV "
                                       "and show it in the table — instant, from existing results.")
        self.term_table_btn.clicked.connect(lambda: self._extract_term_genes(heatmap=False))
        self.term_heatmap_btn = QPushButton("Build heatmap\n+ expression")
        self.term_heatmap_btn.setAccessibleName("Build enrichment-term heatmap and expression table")
        self.term_heatmap_btn.setToolTip("Reuses the finished DESeq2 results — no re-alignment "
                                         "or re-analysis. Adds a focused heatmap for the term's genes.")
        self.term_heatmap_btn.clicked.connect(lambda: self._extract_term_genes(heatmap=True))
        for button in (refresh, self.term_table_btn, self.term_heatmap_btn):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            action_column.addWidget(button)
        self.enrichment_term_actions = action_column
        v.addLayout(action_column)
        self.term_status = QLabel("")
        self.term_status.setWordWrap(True)
        self.term_status.setProperty("hint", True)
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
        holder.setProperty("infoTitle", text)
        holder.setProperty("infoText", help_text)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        label = QLabel(text)
        label.setObjectName("infoLabelText")
        label.setProperty("uiRole", "formLabel")
        # Form labels are concise navigation copy. Keeping them on one line lets
        # QFormLayout reserve their real width instead of collapsing the label
        # column and producing awkward two- and three-line fragments beside a
        # mostly empty field column. Narrow inspector forms wrap the entire row.
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row.addWidget(label)
        info = QToolButton()
        info.setObjectName("infoLabelButton")
        info.setText("ⓘ")  # circled small i
        info.setAccessibleName(f"About {text}")
        info.setAccessibleDescription(help_text)
        info.setAutoRaise(True)
        info.setCursor(Qt.CursorShape.PointingHandCursor)
        info.setToolTip(help_text)
        info.clicked.connect(
            lambda _checked=False, source=holder: QMessageBox.information(
                self,
                str(source.property("infoTitle") or "Setting"),
                str(source.property("infoText") or ""),
            )
        )
        row.addWidget(info)
        row.addStretch(1)
        return holder

    @staticmethod
    def _set_info_label(holder: QWidget, text: str, help_text: str) -> None:
        """Update a labelled help control without reconnecting its click handler."""
        holder.setProperty("infoTitle", text)
        holder.setProperty("infoText", help_text)
        label = holder.findChild(QLabel, "infoLabelText")
        info = holder.findChild(QToolButton, "infoLabelButton")
        if label is not None:
            label.setText(text)
        if info is not None:
            info.setAccessibleName(f"About {text}")
            info.setAccessibleDescription(help_text)
            info.setToolTip(help_text)

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
        families = QFontDatabase.families()
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        group_pick = QComboBox()
        group_pick.setAccessibleName("Figure group to override")
        group_pick.setToolTip("Choose one figure family and edit only the values that should differ from the global style.")
        stack = QStackedWidget()
        self.fig_override_widgets = {}
        for gkey, glabel in self.PALETTE_GROUPS:
            group_pick.addItem(glabel, gkey)
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(0, 4, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(4)
            self.fig_override_widgets[gkey] = {}
            for index, (okey, label) in enumerate(self.OVERRIDE_COLS):
                w = self._make_override_widget(okey, families)
                w.setMinimumWidth(0)
                w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                row = (index // 2) * 2
                column = index % 2
                field_label = QLabel(label)
                field_label.setBuddy(w)
                grid.addWidget(field_label, row, column)
                grid.addWidget(w, row + 1, column)
                grid.setColumnStretch(column, 1)
                self.fig_override_widgets[gkey][okey] = w
            stack.addWidget(page)
        group_pick.currentIndexChanged.connect(stack.setCurrentIndex)
        group_row = QHBoxLayout()
        group_row.setContentsMargins(0, 0, 0, 0)
        group_row.setSpacing(8)
        group_label = QLabel("Figure group")
        group_label.setBuddy(group_pick)
        group_row.addWidget(group_label)
        group_row.addWidget(group_pick, 1)
        layout.addLayout(group_row)
        layout.addWidget(stack, 1)
        self.fig_override_group_pick = group_pick
        self.fig_override_stack = stack
        return editor

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
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        outer = QVBoxLayout(group)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
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
        self.fig_sample_labels = QCheckBox("Show sample labels")
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
        def narrow_form(page: QWidget) -> QFormLayout:
            section_form = QFormLayout(page)
            section_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            section_form.setProperty("narrowInspector", True)
            section_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            section_form.setHorizontalSpacing(10)
            section_form.setVerticalSpacing(8)
            return section_form

        def form_page() -> tuple[QWidget, QFormLayout]:
            page = QWidget()
            return page, narrow_form(page)

        appearance_page = QWidget()
        appearance_layout = QVBoxLayout(appearance_page)
        appearance_layout.setContentsMargins(4, 4, 4, 4)
        appearance_layout.setSpacing(4)
        appearance_common_page = QWidget()
        appearance = narrow_form(appearance_common_page)
        appearance.setContentsMargins(0, 0, 0, 0)
        appearance_layout.addWidget(appearance_common_page)
        (self.figure_appearance_advanced_toggle,
         self.figure_appearance_advanced_panel) = self._disclosure(
            "Typography and labels", expanded=False)
        appearance_advanced = narrow_form(self.figure_appearance_advanced_panel)
        appearance_advanced.setContentsMargins(0, 4, 0, 0)
        appearance_layout.addWidget(self.figure_appearance_advanced_toggle)
        appearance_layout.addWidget(self.figure_appearance_advanced_panel)
        appearance_layout.addStretch(1)
        dimensions_page, dimensions = form_page()
        override_page = QWidget()
        overrides = QVBoxLayout(override_page)
        overrides.setContentsMargins(4, 4, 4, 4)
        overrides.setSpacing(6)

        # Keep the settings most people change together in the first viewport. The
        # rendering-specific controls remain available in place, but no longer make
        # every user scroll through a long technical form before reaching the action
        # buttons. This is an in-flow disclosure rather than another nested card.
        detail_page = QWidget()
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        detail_layout.setSpacing(4)
        detail_common_page = QWidget()
        detail_common = narrow_form(detail_common_page)
        detail_common.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(detail_common_page)
        (self.figure_detail_advanced_toggle,
         self.figure_detail_advanced_panel) = self._disclosure(
            "Advanced plot rendering", expanded=False)
        detail_advanced = narrow_form(self.figure_detail_advanced_panel)
        detail_advanced.setContentsMargins(0, 4, 0, 0)
        detail_layout.addWidget(self.figure_detail_advanced_toggle)
        detail_layout.addWidget(self.figure_detail_advanced_panel)
        detail_layout.addStretch(1)

        save_style = QPushButton("Save style")
        save_style.clicked.connect(self._save_figure_style)
        appearance.addRow(self._info_label("Palette", "Colour scheme for all figures. Blue-Red is diverging; Viridis is colour-blind friendly; Greyscale prints well in mono."), self.fig_palette)
        _ov_note = QLabel(
            "Choose a figure group and set only values that differ; everything else inherits the global style.")
        _ov_note.setWordWrap(True)
        _ov_note.setProperty("hint", True)
        overrides.addWidget(_ov_note)
        overrides.addWidget(self._build_figure_override_table(), 1)
        appearance.addRow(self._info_label("Point size", "Dot size in PCA/volcano scatter plots (ggplot2 size units)."), self.fig_point_size)
        appearance.addRow(self._info_label("Base font size", "Base text size for all figures (ggplot2 theme base_size, points)."), self.fig_base_font)
        appearance_advanced.addRow(self._info_label("Font family", "Font for figure text. Leave as default unless the font is also available in the WSL R environment."), self.fig_font_family)
        appearance_advanced.addRow(self.fig_label_bold)
        appearance_advanced.addRow(self.fig_title_bold)
        appearance_advanced.addRow(self.fig_gene_italic)
        detail_common.addRow(self._info_label("Volcano top-N labels", "Display heuristic: how many significant genes are labelled. 0 = none; the DE table is unchanged."), self.fig_volcano_top)
        detail_common.addRow(self._info_label("Heatmap top-N genes", "Display heuristic: number of top genes shown; the DE table is unchanged."), self.fig_heatmap_top)
        detail_common.addRow(self.fig_sample_labels)
        dimensions.addRow(self._info_label("Size units", "Units for width and height. Pixels are converted using the DPI."), self.fig_dim_unit)
        dimensions.addRow(self._info_label("Width", "Saved figure width for PNG and SVG."), self.fig_width)
        dimensions.addRow(self._info_label("Height", "Saved figure height for PNG and SVG."), self.fig_height)
        dimensions.addRow(self._info_label("DPI (PNG)", "Raster resolution. SVG remains vector; 300 DPI is publication quality."), self.fig_dpi)
        detail_advanced.addRow(self._info_label("PCA n-top genes", "Number of most-variable genes used for the displayed PCA. Protocol default 500."), self.fig_pca_ntop)
        detail_advanced.addRow(self._info_label("Volcano y-axis", "Visual scale only: cap with off-scale markers, show true full height, or compress the tail using sqrt."), self.fig_volcano_yscale)
        detail_advanced.addRow(self._info_label("Volcano y cap", "Visual upper limit in cap mode. Auto uses the 99.5th percentile and marks off-scale points."), self.fig_volcano_ycap)
        detail_advanced.addRow(self._info_label("Volcano point alpha", "Opacity of significant points. Lower values reveal density in the dense core."), self.fig_volcano_alpha)
        detail_advanced.addRow(self.fig_pca_fixed_aspect)
        detail_advanced.addRow(self._info_label("Heatmap z limit", "Symmetric visual cap on heatmap row z-scores."), self.fig_heatmap_zlim)
        detail_advanced.addRow(self._info_label("Enrichment categories shown", "Display heuristic: number of terms shown; enrichment tables are unchanged."), self.fig_enrich_show)
        detail_advanced.addRow(self._info_label("Static PPI figure layout", "Layout algorithm for the saved R network figure. This does not change the STRING edge set."), self.fig_ppi_layout)
        self.figure_detail_common_controls = (
            self.fig_volcano_top,
            self.fig_heatmap_top,
            self.fig_sample_labels,
        )
        self.figure_detail_advanced_controls = (
            self.fig_pca_ntop,
            self.fig_volcano_yscale,
            self.fig_volcano_ycap,
            self.fig_volcano_alpha,
            self.fig_pca_fixed_aspect,
            self.fig_heatmap_zlim,
            self.fig_enrich_show,
            self.fig_ppi_layout,
        )
        # --- PPI network (STRING) controls: customise + regenerate in-app ---
        self.ppi_score = QSpinBox()
        self.ppi_score.setRange(0, 1000)
        self.ppi_score.setSingleStep(50)
        self.ppi_score.setValue(400)
        # Keep this Figure-Style threshold and the PPI-tab "Rebuild at score" spinbox in
        # lockstep, so either Regenerate button rebuilds at the value the user just set.
        self.ppi_score.valueChanged.connect(self._sync_score_to_rebuild)
        self.ppi_hub_labels = QSpinBox()
        self.ppi_hub_labels.setRange(0, 100)
        self.ppi_hub_labels.setValue(15)
        sections = QTabWidget()
        sections.setObjectName("figureStyleSections")
        sections.setMinimumWidth(0)
        sections.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        sections.setElideMode(Qt.TextElideMode.ElideRight)
        sections.setUsesScrollButtons(True)
        sections.addTab(self._inspector_scrollable(appearance_page), "Appearance")
        sections.addTab(self._inspector_scrollable(detail_page), "Detail")
        sections.addTab(self._inspector_scrollable(dimensions_page), "Size")
        sections.addTab(self._inspector_scrollable(override_page), "Overrides")
        outer.addWidget(sections, 1)

        action_row = QHBoxLayout()
        action_row.addWidget(save_style)
        # Escape the ampersand so Windows paints it literally instead of treating
        # the following character as a mnemonic and showing an underscore.
        apply_now = QPushButton("Apply && regenerate")
        apply_now.setAccessibleName("Apply and regenerate figures")
        apply_now.setProperty("primary", True)
        apply_now.setToolTip(
            "Save these settings and re-render figures only; alignment and differential expression are not rerun."
        )
        apply_now.clicked.connect(self._regenerate_figures)
        action_row.addWidget(apply_now)
        outer.addLayout(action_row)
        self.apply_figure_style_button = apply_now
        self.figure_style_sections = sections
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
        self._expand_output_table_preview()
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
        self._fit_output_table_columns([str(column) for column in df.columns])

    def _fit_output_table_columns(self, labels: list[str]) -> None:
        """Size preview columns for readable headers without letting one value dominate."""
        header = self.output_table.horizontalHeader()
        # The preview remains horizontally scrollable. A cap prevents a long pathway
        # description or serialized list from consuming the entire viewport, while
        # common DE headers such as log2FoldChange remain visible in full.
        minimum_width = 72
        maximum_width = 320
        header.setMinimumSectionSize(minimum_width)
        header.setMaximumSectionSize(maximum_width)
        self.output_table.resizeColumnsToContents()
        metrics = header.fontMetrics()
        header_chrome = metrics.horizontalAdvance("  ▼") + 16
        for column, label in enumerate(labels):
            readable_header_width = metrics.horizontalAdvance(label) + header_chrome
            fitted_width = max(
                minimum_width,
                readable_header_width,
                self.output_table.columnWidth(column),
            )
            self.output_table.setColumnWidth(column, min(maximum_width, fitted_width))

    def _expand_output_table_preview(self) -> None:
        splitter = getattr(self, "_outputs_main_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) != 2 or sizes[0] > 1:
            return
        total = max(sum(sizes), splitter.height(), 480)
        preview = min(190, max(130, total // 4))
        splitter.setSizes([preview, max(total - preview, 240)])

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
            if hasattr(self, "output_empty_state"):
                self.output_empty_state_panel.setVisible(False)
                self.output_figure_stack.setCurrentWidget(self.figure_viewer)
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
            if hasattr(self, "output_empty_state"):
                if self.project_root is None:
                    title = "No project open"
                    message = (
                        "Open or create a project to browse stored figures and tables. "
                        "Figure editing becomes available with the project.")
                    action = "Go to Project"
                else:
                    title = "No figures yet"
                    message = (
                        "No figures are available yet. Complete a run or regenerate figures, "
                        "then refresh this view.")
                    action = "Go to Run Monitor"
                self.output_empty_title.setText(title)
                self.output_empty_state.setText(message)
                if self.output_empty_action is not None:
                    self.output_empty_action.setText(action)
                self.output_empty_state_panel.setVisible(True)
                self.output_figure_stack.setCurrentWidget(self.output_empty_state_panel)
            self.figure_pick.addItem("(no figures yet — run the pipeline first)")
            self.figure_pick.setEnabled(False)
            self.figure_pick.blockSignals(False)
            self.figure_viewer.clear()

    def _go_from_output_empty_state(self) -> None:
        self.tabs.setCurrentIndex(0 if self.project_root is None else 8)

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

    def _confirm_project_overwrite(self, root: Path) -> bool:
        # Scaffolding resets the sample sheet, contrasts, gene sets and config to
        # empty defaults. Name exactly what is lost; default to No so Return or Esc
        # keeps the existing project.
        answer = QMessageBox.warning(
            self, APP_NAME,
            f"A project already exists at:\n{root}\n\n"
            "Creating it again resets that project's sample sheet (samples.tsv), "
            "contrasts, gene sets and workflow settings to empty defaults. "
            "Existing results and downloaded data are left alone.\n\n"
            "Overwrite the project configuration?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

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
        except ProjectExistsError as exc:
            if not self._confirm_project_overwrite(exc.root):
                self.project_status.setPlainText(f"Kept the existing project at {exc.root}")
                return
            try:
                root = self.manager.create_project(name, workdir, overwrite=True)
            except (OSError, ValueError) as exc2:
                self.project_status.setPlainText(f"Project creation failed: {exc2}")
                QMessageBox.critical(self, APP_NAME, f"Project creation failed:\n{exc2}")
                return
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
        project_name = self.project_name.text() or benchmark_id
        try:
            root = create_benchmark_project(benchmark_id, workdir, project_name)
        except ProjectExistsError as exc:
            if not self._confirm_project_overwrite(exc.root):
                self.project_status.setPlainText(f"Kept the existing project at {exc.root}")
                return
            try:
                root = create_benchmark_project(benchmark_id, workdir, project_name, overwrite=True)
            except (OSError, ValueError) as exc2:
                self.project_status.setPlainText(f"Benchmark project creation failed: {exc2}")
                QMessageBox.critical(self, APP_NAME, f"Benchmark project creation failed:\n{exc2}")
                return
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

    def _configured_samples_path(self) -> Path:
        """The one sample sheet used by the GUI, preflight and Snakemake."""
        if self.project_root is None:
            raise RuntimeError("No project is open")
        configured = self.config.input.samples if self.config is not None else "config/samples.tsv"
        return project_configured_path(self.project_root, configured)

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
        # Same predicate the create-time overwrite guard uses, so "is a project"
        # cannot mean two different things in the same application.
        if not is_project_root(root):
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
        samples = self._configured_samples_path()
        if samples.exists():
            self.metadata_table.load_tsv(samples)
            # _populate_widgets_from_config seeded the contrast dropdowns from the PREVIOUS
            # table; re-seed now that the new project's samples are loaded (valid selected
            # values are preserved by _refresh_conditions).
            self._refresh_conditions()
        self._refresh_gallery()
        self._refresh_export_buttons()
        self._refresh_input_preview()
        if hasattr(self, "term_pick"):
            self._populate_term_picker()
        self._remember_recent_project(root)
        # Remember the folder this project lives in so the "Open project" picker starts there
        # next time (instead of the app's install/AppData folder).
        QSettings().setValue("last_project_dir", str(root.parent))
        self.project_status.setPlainText(f"Open project: {root}")
        self.statusBar().showMessage(
            "Project open — review the inputs and settings, validate them, then start the workflow.")
        self._refresh_phase_checks()
        # If this project was left with an unfinished run, surface the one-click Resume banner.
        self._refresh_resume_banner()

    def _clear_transient_ui(self) -> None:
        # Reset run/output widgets so a previously opened project's log, status,
        # figures and network do not linger after switching projects.
        self.log_text.clear()
        self.command_text.clear()
        self._set_run_status("Ready — configure the project, then start the workflow.")
        self.phase_label.setText("")
        self.progress.setValue(0)
        self._set_progress_status()
        self.progress_value_label.setVisible(False)
        self.elapsed_label.setText("Elapsed: 00:00:00")
        self.elapsed_label.setVisible(False)
        self.input_preview.clear()
        self.metadata_messages.clear()
        self.metadata_message_heading.setVisible(False)
        self.metadata_message_frame.setVisible(False)
        self.output_table.setRowCount(0)
        if hasattr(self, "_outputs_main_splitter"):
            self._outputs_main_splitter.setSizes([0, max(self._outputs_main_splitter.height(), 480)])
        self.report_text.setPlainText(
            "No reports yet. Complete a run, then generate or open the saved reports from this page."
            if self.project_root is not None else ""
        )
        self.runtime_text.setPlainText(
            "Ready to estimate. The predicted range, resource assumptions and calibration basis will appear here."
            if self.project_root is not None else "")
        # The run-approval gate and the previous project's sanity output must not
        # carry over: approval is per project (a stale tick could let an unreviewed
        # run start).
        self.approve_review.setChecked(False)
        self.sanity_text.clear()
        self._sanity_status_signature = None
        self.ppi_status.setText("No network loaded — click “Load / refresh network”.")
        if hasattr(self, "ppi_viewer"):
            self.ppi_viewer.clear_network()
        self._set_ppi_network_controls(False)
        if hasattr(self, "ppi_export_png"):
            self.ppi_export_png.setEnabled(False)
            self.ppi_export_svg.setEnabled(False)
            if hasattr(self, "ppi_save_cyto"):
                self.ppi_save_cyto.setEnabled(False)

    def _refresh_input_preview(self) -> None:
        """Summarise the selected input route after opening a project.

        The route selector already holds the editing controls; this copy gives the
        user a persistent, derived answer to "what is loaded, and what is next?"
        instead of restoring an empty placeholder whenever projects are switched.
        """
        if self.config is None:
            self.input_preview.setPlainText(
                "Choose a route above. Imported samples, detected layout and the next required step appear here.")
            return
        route = self.config.input.type
        sample_count = int(self.metadata_table.rowCount())
        if route == "count_matrix":
            source = Path(self.config.input.count_matrix or "count matrix").name
            summary = (
                f"Input route: raw count matrix ({source}). {sample_count} sample(s) are loaded. "
                "Next: confirm sample conditions, the comparison and organism annotation."
            )
        elif route == "deseq2_results":
            provenance = self.config.input.deseq2_results_provenance
            source = provenance.original_basename or Path(
                self.config.input.deseq2_results or "differential-expression table").name
            direction = self.config.input.deseq2_results_direction
            direction_copy = (
                f" Positive log2 fold change means {direction.numerator} relative to {direction.denominator}."
                if direction.confirmed and direction.numerator and direction.denominator else
                " Confirm the positive log2 fold-change direction before validation."
            )
            summary = (
                f"Input route: completed differential-expression table ({source})."
                f"{direction_copy} Next: validate, then explore compatible figures, enrichment and PPI."
            )
        elif route == "microarray":
            source = self.config.microarray.gse_accession or Path(
                self.config.microarray.expression_matrix or "expression matrix").name
            summary = (
                f"Input route: microarray ({source}). {sample_count} sample(s) are loaded. "
                "Next: confirm sample groups and the limma comparison."
            )
        elif route == "sra":
            summary = (
                f"Input route: public sequencing accessions. {sample_count} sample(s) are loaded. "
                "Next: review the sample sheet and select the reference."
            )
        elif route == "mixed":
            summary = (
                f"Input route: mixed local and public FASTQ inputs. {sample_count} sample(s) are loaded. "
                "Next: review every file assignment and select the reference."
            )
        else:
            summary = (
                f"Input route: local FASTQ. {sample_count} sample(s) are loaded. "
                "Next: review file assignments and select the reference."
            )
        self.input_preview.setPlainText(summary)

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
        if hasattr(self, "workflow_design_toggle"):
            self.workflow_design_toggle.setChecked(
                self.config.deseq2.design_formula.strip() != "~ condition")
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
            if enr.taxon_id is None:
                enr.taxon_id = entry.get("taxon_id")
            if self.config.ppi.taxon is None:
                self.config.ppi.taxon = entry.get("string_taxon")
        # Restore the custom-reference fields so reopening a project does not show
        # them blank (which would invite an accidental empty re-lock).
        ref = self.config.reference
        if ref.mode == "custom":
            if hasattr(self, "reference_custom_toggle"):
                self.reference_custom_toggle.setChecked(True)
            self.ref_organism.setText(ref.organism_name if ref.organism_name != "unset" else "")
            self.ref_genome.setText(ref.genome_fasta or "")
            self.ref_annotation.setText(ref.annotation_file or "")
            if ref.annotation_format in ("gtf", "gff3"):
                self.ref_format.setCurrentText(ref.annotation_format)
        elif hasattr(self, "reference_custom_toggle"):
            self.reference_custom_toggle.setChecked(False)
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
        # Under WSL the run reads the configured sample sheet inside Linux, so a Windows-drive FASTQ path
        # (C:\...) is unresolvable — translate the file columns to /mnt/<drive>/... first.
        if getattr(self, "use_wsl", None) is not None and self.use_wsl.isChecked():
            for _col in ("fastq_1", "fastq_2"):
                if _col in df.columns:
                    df[_col] = df[_col].map(lambda p: windows_to_wsl_path(p) if p else p)
        assert self.project_root is not None
        save_metadata(df, self.project_root / "config" / "samples.auto_generated.tsv")
        save_metadata(df, self._configured_samples_path())
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
                df = read_user_table(p, sep="\t" if p.suffix.lower() == ".tsv" else ",", dtype=str).fillna("")
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
        save_metadata(self.metadata_table.to_dataframe(), self._configured_samples_path())
        self._show_metadata_message(
            f"Saved {self.config.input.samples if self.config else 'config/samples.tsv'}")

    def _show_metadata_message(self, text: str) -> None:
        self.metadata_messages.setPlainText(text)
        self.metadata_message_heading.setVisible(bool(text.strip()))
        self.metadata_message_frame.setVisible(bool(text.strip()))

    def _active_contrast(self) -> tuple[str, str] | None:
        # The contrast arms drive the multi-study confounding gate (which condition-vs-condition is
        # being compared); read them from the DE-tab combos so the gate fires for >2-level designs.
        if self.config is not None and self.config.input.type == "deseq2_results":
            direction = self.config.input.deseq2_results_direction
            if direction.confirmed and direction.numerator and direction.denominator:
                return direction.numerator, direction.denominator
            return None
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
        self._show_metadata_message(self._format_messages(messages))

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
        enr.taxon_id = entry.get("taxon_id")
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
        ref.genome_md5 = entry.get("genome_md5") or None
        ref.annotation_md5 = entry.get("annotation_md5") or None
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
        if self.config is not None and self.config.input.type == "deseq2_results":
            # External-result direction is validated from immutable import provenance; the
            # hidden local contrast controls are intentionally irrelevant for this route.
            return None
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
        self.config.deseq2.alpha = self.alpha.value()
        self.config.deseq2.lfc_threshold = self.lfc_threshold.value()
        if self.config.input.type != "deseq2_results":
            self.config.deseq2.design_formula = self.design.text()
            factor = self.contrast_factor.text().strip() or "condition"
            if self.reference_level.currentText().strip():
                self.config.deseq2.reference_level = {
                    factor: self.reference_level.currentText().strip()
                }
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
        if profile == "custom" and hasattr(self, "resource_manual_toggle"):
            self.resource_manual_toggle.setChecked(True)
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
        # For a WSL-native workdir the free space is bounded by the Windows drive that
        # backs the vhdx; name it so the number reads as real, not the vhdx's virtual size.
        disk_note = ""
        if is_wsl_unc_path(system.disk_path):
            base = wsl_vhdx_basepath(wsl_unc_distro(system.disk_path))
            if base is not None and base.drive:
                disk_note = f" on {base.drive} (backs the WSL disk)"
        info = (
            f"{system.cpu_model} — {system.physical_cores} physical cores "
            f"({system.logical_threads} logical CPUs), {system.total_ram_gb:.0f} GB RAM, "
            f"{system.disk_free_gb:.0f} GB free disk{disk_note}."
        )
        wsl_cpus = max(int(getattr(system, "wsl_cpus", 0) or 0), 0)
        wsl_ram_gb = max(float(getattr(system, "wsl_ram_gb", 0) or 0), 0.0)
        wsl_physical = max(int(getattr(system, "wsl_physical_cores", 0) or 0), 0)
        if wsl_cpus or wsl_ram_gb:
            # CPU and RAM probes can fail independently. Describe the same per-resource
            # fallback that recommend_profile applies instead of inferring one from the other.
            wsl_parts: list[str] = []
            if wsl_cpus:
                topology = (
                    f"{wsl_cpus} logical CPUs / {wsl_physical} physical cores"
                    if wsl_physical else f"{wsl_cpus} logical CPUs"
                )
                wsl_parts.append(topology)
                cpu_explanation = "the WSL logical CPU allocation"
                if wsl_physical:
                    cpu_explanation += "; the physical-core count is topology information only"
            else:
                cpu_explanation = (
                    f"host logical CPUs ({system.logical_threads}) because the WSL CPU "
                    "allocation was unavailable"
                )
            if wsl_ram_gb:
                wsl_parts.append(f"{wsl_ram_gb:.0f} GB RAM")
                ram_explanation = "the WSL RAM limit"
            else:
                ram_explanation = (
                    f"host RAM ({system.total_ram_gb:.0f} GB) because the WSL RAM limit "
                    "was unavailable"
                )
            info += (
                f"\nWSL2 sees {' / '.join(wsl_parts)} — "
                f"CPU recommendations use {cpu_explanation}. "
                f"RAM recommendations use {ram_explanation}. "
                "Use 'Edit WSL2 memory / CPU limits…' below to change them, then detect again."
            )
        self.system_info_label.setText(info)
        self.recommendation_label.setText(
            f"Recommended for the '{self.profile.currentText()}' profile: "
            f"{rec['total_threads']} CPU workers and {rec['total_memory_gb']} GB RAM."
        )
        if wsl_cpus:
            cpu_basis = (
                f"WSL2 {wsl_physical} physical / {wsl_cpus} logical CPUs"
                if wsl_physical else f"WSL2 {wsl_cpus} logical CPUs"
            )
        else:
            cpu_basis = (
                f"host {system.physical_cores} physical / {system.logical_threads} logical CPUs"
            )
            if wsl_ram_gb:
                cpu_basis += " (WSL CPU allocation unavailable)"
        ram_basis = (
            f"WSL2 {wsl_ram_gb:.0f} GB RAM"
            if wsl_ram_gb else f"host {system.total_ram_gb:.0f} GB RAM"
        )
        if wsl_cpus and not wsl_ram_gb:
            ram_basis += " (WSL RAM limit unavailable)"
        detected_basis = f"CPU basis: {cpu_basis}; RAM basis: {ram_basis}"
        self.statusBar().showMessage(
            f"Detected {detected_basis} — recommending {rec['total_threads']} CPU workers, "
            f"{rec['total_memory_gb']} GB.",
            8000,
        )

    def _save_resources(self) -> None:
        if self.config is None or self.project_root is None:
            return
        self.config.resources.profile = self.profile.currentText()  # type: ignore[assignment]
        self.config.resources.total_threads = self.cores.value()
        self.config.resources.total_memory_gb = self.ram.value()
        # Resource profiles own the schedulable pool and its per-rule subdivision.
        # Persist the derived requests so the workflow actually uses the CPU budget
        # displayed by the GUI (instead of retaining the four-thread scaffold defaults).
        for rule_name, threads in recommend_rule_threads(self.cores.value()).items():
            setattr(self.config.rule_threads, rule_name, threads)
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
        if not self._require_project() or self.config is None:
            return
        running_worker = getattr(self, "_sanity_worker", None)
        if running_worker is not None and running_worker.isRunning():
            return
        phase_worker = self._phase_refresh_worker
        if phase_worker is not None and phase_worker.isRunning():
            return
        assert self.project_root is not None
        self.sanity_busy.setVisible(True)
        self.sanity_run_button.setEnabled(False)
        try:
            # Persist every GUI-owned input before computing the fingerprint. A
            # preflight must describe the exact state a subsequent run will read.
            save_metadata(
                self.metadata_table.to_dataframe(),
                self._configured_samples_path())
            if not self._save_workflow_settings(validate=True):
                self.sanity_busy.setVisible(False)
                self.sanity_run_button.setEnabled(True)
                return
            self._save_resources()
            self._apply_figure_style()

            if self.config.input.type == "deseq2_results":
                messages = self._deseq2_results_preflight_messages()
            else:
                allow_pending_sra = self.config.input.type in (
                    "sra", "count_matrix", "microarray")
                messages = validate_metadata(
                    self.metadata_table.to_dataframe(),
                    allow_pending_sra=allow_pending_sra,
                    design_variables=self._design_variables(),
                    contrast=self._active_contrast(),
                )
            messages = list(messages) + self._route_preflight_messages()
            messages = list(messages) + self._enrichment_config_messages()
            check_path = write_check(self.project_root, "01_input_validation", messages)
            import json

            payload = json.loads(check_path.read_text(encoding="utf-8"))
            root = self.project_root
            def fingerprint_and_validate():
                write_input_validation_with_fingerprint(
                    root,
                    payload,
                    cancel_requested=worker.isInterruptionRequested,
                )
                return validate_current_preflight(
                    root,
                    cancel_requested=worker.isInterruptionRequested,
                )

            worker = BackgroundWorker(fingerprint_and_validate)
            worker.done.connect(
                lambda outcome: self._on_sanity_fingerprint_done(
                    root, payload, messages, outcome,
                ),
            )
            worker.failed.connect(
                lambda exc: self._on_sanity_fingerprint_failed(root, exc),
            )
            self._sanity_worker = worker
            worker.start()
        except Exception:
            self.sanity_busy.setVisible(False)
            self.sanity_run_button.setEnabled(True)
            raise

    def _on_sanity_fingerprint_done(
        self,
        project_root: Path,
        payload: dict,
        messages: list[dict[str, str]],
        outcome,
    ) -> None:
        self.sanity_busy.setVisible(False)
        self._sanity_worker = None
        if self.project_root != project_root or getattr(self, "_closing", False):
            return
        text = self._format_messages(messages)
        if not outcome.valid:
            self._update_sanity_state(
                {"01_input_validation": "STALE"},
                reset_approval=True,
            )
            self.sanity_text.append("")
            self.sanity_text.append("Input fingerprint could not authorize this run:")
            self.sanity_text.append(outcome.reason)
            return
        # Only the current, fingerprinted preflight authorizes launch. Saved
        # downstream phase checks remain inspectable through the reload button.
        self._update_sanity_state(
            {"01_input_validation": payload.get("status", "PASS")},
            reset_approval=True,
        )
        if text:
            self.sanity_text.append("")
            self.sanity_text.append("Latest validation detail:")
            self.sanity_text.append(text)

    def _on_sanity_fingerprint_failed(self, project_root: Path, exc: object) -> None:
        self.sanity_busy.setVisible(False)
        self._sanity_worker = None
        if self.project_root != project_root or getattr(self, "_closing", False):
            return
        self._update_sanity_state({"01_input_validation": "STALE"}, reset_approval=True)
        QMessageBox.warning(
            self,
            APP_NAME,
            f"Could not fingerprint the current scientific inputs:\n\n{exc}",
        )

    def _deseq2_results_preflight_messages(self) -> list[dict[str, str]]:
        """Validate direction, full project copy, and its import-time provenance."""
        if self.config is None or self.project_root is None:
            return [{"status": "FAIL", "message": "Project configuration is not loaded."}]
        direction = self.config.input.deseq2_results_direction
        messages: list[dict[str, str]] = []
        try:
            confirmed = Deseq2ResultsDirectionProvenance.model_validate(direction.model_dump(mode="json"))
            if not confirmed.confirmed:
                raise ValueError("the recorded direction has not been explicitly confirmed")
        except ValueError as exc:
            messages.append({
                "status": "FAIL",
                "message": f"Imported-results direction provenance is incomplete or invalid: {exc}",
            })
        else:
            messages.append({
                "status": "PASS",
                "message": (
                    "Imported-results direction confirmed: positive log2FoldChange means higher in "
                    f"{confirmed.numerator} than {confirmed.denominator}."
                ),
            })

        configured = self.config.input.deseq2_results
        if not configured:
            messages.append({
                "status": "FAIL",
                "message": "The external-results route has no configured project-copy table.",
            })
            return messages
        project_copy = Path(configured)
        if not project_copy.is_absolute():
            project_copy = self.project_root / project_copy
        if not project_copy.exists():
            messages.append({
                "status": "FAIL",
                "message": f"The external-results project copy is missing: {configured}",
            })
            return messages

        file_provenance = self.config.input.deseq2_results_provenance
        file_provenance_invalid = False
        try:
            file_provenance = Deseq2ResultsFileProvenance.model_validate(
                file_provenance.model_dump(mode="json")
            )
        except ValueError as exc:
            file_provenance_invalid = True
            messages.append({
                "status": "FAIL",
                "message": f"External-results file provenance is invalid: {exc}",
            })
        validated, errors = validate_recorded_project_copy(
            project_copy,
            file_provenance,
            configured_project_copy=configured,
        )
        messages.extend({"status": "FAIL", "message": error} for error in errors)
        if validated is not None and not errors and not file_provenance_invalid:
            messages.append({
                "status": "PASS",
                "message": (
                    f"Validated the complete external-results project copy: {validated.row_count:,} rows, "
                    f"{len(validated.column_names)} columns, SHA-256 {validated.sha256[:12]}…."
                ),
            })
        return messages

    def _route_preflight_messages(self) -> list[dict[str, str]]:
        """Validate route-specific project files and reference requirements."""
        if self.config is None or self.project_root is None:
            return [{"status": "FAIL", "message": "Project configuration is not loaded."}]
        messages: list[dict[str, str]] = []
        mode = self.config.input.type
        configured_input = None
        if mode == "count_matrix":
            configured_input = self.config.input.count_matrix
        elif mode == "deseq2_results":
            configured_input = self.config.input.deseq2_results
        elif mode == "microarray" and self.config.microarray.source == "local_matrix":
            configured_input = self.config.microarray.expression_matrix
        if configured_input:
            input_path = Path(configured_input)
            if not input_path.is_absolute():
                input_path = self.project_root / input_path
            if not input_path.exists():
                messages.append({
                    "status": "FAIL",
                    "message": f"Configured input file is missing: {configured_input}",
                })
        elif mode in ("count_matrix", "deseq2_results"):
            messages.append({
                "status": "FAIL",
                "message": f"The {mode.replace('_', ' ')} route has no configured input table.",
            })

        if mode in ("fastq", "sra", "mixed"):
            ref = self.config.reference
            has_url = bool(ref.genome_fasta_url and ref.annotation_gtf_url)
            has_local = bool(ref.genome_fasta and ref.annotation_file)
            if not (has_url or has_local):
                messages.append({
                    "status": "FAIL",
                    "message": "Raw-read processing needs a genome FASTA and annotation. Select a preset or custom reference.",
                })
        if not messages:
            messages.append({
                "status": "PASS",
                "message": "The active input route and reference requirements are configured.",
            })
        return messages

    def _phase_check_statuses(self, *, preflight=None) -> dict[str, str]:
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
            raw_status = payload.get("status", "FAIL") if isinstance(payload, dict) else "FAIL"
            statuses[path.stem] = (
                raw_status
                if isinstance(raw_status, str)
                and raw_status in {"PASS", "WARNING", "REVIEW_REQUIRED", "FAIL", "STALE"}
                else "FAIL"
            )
        if "01_input_validation" in statuses and (
            preflight is None or not preflight.valid
        ):
            # Fingerprinting can read multi-gigabyte inputs. Treat the recorded
            # status as stale until the background refresh proves it current.
            statuses["01_input_validation"] = "STALE"
        return statuses

    def _refresh_phase_checks(self) -> None:
        if not self._require_project():
            return
        assert self.project_root is not None
        running_sanity = getattr(self, "_sanity_worker", None)
        if running_sanity is not None and running_sanity.isRunning():
            return
        running_refresh = self._phase_refresh_worker
        if running_refresh is not None and running_refresh.isRunning():
            return
        root = self.project_root
        pending = self._phase_check_statuses()
        self._update_sanity_state(pending, reset_approval=True)
        if "01_input_validation" not in pending:
            return
        self.sanity_busy.setVisible(True)
        self.sanity_refresh_button.setEnabled(False)

        def validate_saved_check():
            return validate_current_preflight(
                root,
                cancel_requested=worker.isInterruptionRequested,
            )

        worker = BackgroundWorker(validate_saved_check)
        worker.done.connect(
            lambda outcome: self._on_phase_refresh_done(worker, root, outcome),
        )
        worker.failed.connect(
            lambda exc: self._on_phase_refresh_failed(worker, root, exc),
        )
        self._phase_refresh_worker = worker
        worker.start()

    def _on_phase_refresh_done(
        self,
        worker: BackgroundWorker,
        project_root: Path,
        outcome,
    ) -> None:
        if worker is not self._phase_refresh_worker:
            return
        self._phase_refresh_worker = None
        self.sanity_busy.setVisible(False)
        if getattr(self, "_closing", False):
            return
        if self.project_root != project_root:
            QTimer.singleShot(0, self._refresh_phase_checks)
            return
        self._update_sanity_state(
            self._phase_check_statuses(preflight=outcome),
            reset_approval=True,
        )

    def _on_phase_refresh_failed(
        self,
        worker: BackgroundWorker,
        project_root: Path,
        _exc: object,
    ) -> None:
        if worker is not self._phase_refresh_worker:
            return
        self._phase_refresh_worker = None
        self.sanity_busy.setVisible(False)
        if getattr(self, "_closing", False):
            return
        if self.project_root != project_root:
            QTimer.singleShot(0, self._refresh_phase_checks)
            return
        self._update_sanity_state(
            self._phase_check_statuses(),
            reset_approval=True,
        )

    def _update_sanity_state(
        self,
        statuses: dict[str, str] | None = None,
        *,
        reset_approval: bool = False,
    ) -> None:
        """Render the validation state without presenting approval out of context."""
        if not hasattr(self, "sanity_state_label"):
            return
        if self.project_root is None:
            self.sanity_run_button.setEnabled(False)
            self.sanity_refresh_button.setEnabled(False)
            self.sanity_go_project.setVisible(True)
            self.sanity_text.clear()
            self.sanity_text.setVisible(False)
            self.approve_review.setChecked(False)
            self.approve_review.setVisible(False)
            self.sanity_state_label.setText(
                "Open or create a project to validate its configuration and study design.")
            self.sanity_next_label.setText(
                "What happens next: configure a project, then return here before starting a run.")
            self.sanity_go_run.setEnabled(False)
            self._sanity_status_signature = None
            return

        statuses = self._phase_check_statuses() if statuses is None else statuses
        allowed_statuses = {"PASS", "WARNING", "REVIEW_REQUIRED", "FAIL", "STALE"}
        statuses = {
            str(name): (
                status
                if isinstance(status, str) and status in allowed_statuses
                else "FAIL"
            )
            for name, status in statuses.items()
        }
        signature_parts = [f"{name}:{status}" for name, status in sorted(statuses.items())]
        if self.project_root is not None:
            # Include the actual payload, not only check names/statuses. Re-running
            # REVIEW_REQUIRED with different findings must clear an old acknowledgement.
            for name in sorted(statuses):
                path = self.project_root / "checks" / f"{name}.json"
                try:
                    signature_parts.append(path.read_text(encoding="utf-8"))
                except OSError:
                    signature_parts.append("<missing>")
        signature = tuple((str(index), value) for index, value in enumerate(signature_parts))
        if reset_approval or signature != self._sanity_status_signature:
            self.approve_review.setChecked(False)
        self._sanity_status_signature = signature
        self.sanity_run_button.setEnabled(True)
        self.sanity_go_project.setVisible(False)

        if not statuses:
            self.sanity_refresh_button.setEnabled(False)
            self.sanity_text.clear()
            self.sanity_text.setVisible(False)
            self.approve_review.setVisible(False)
            self.sanity_state_label.setText(
                "No checks yet — validate the project, sample sheet, contrast and file paths before running.")
            self.sanity_next_label.setText(
                "What happens next: run validation here, then continue to Run Monitor when the findings are resolved.")
            self.sanity_go_run.setEnabled(False)
            return

        priority = {"STALE": 5, "FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
        worst = max(statuses.values(), key=lambda value: priority.get(value, 0))
        labels = {
            "STALE": "Validation is out of date — run validation again for the current project settings.",
            "FAIL": "Validation failed — resolve the named phase checks before starting a run.",
            "REVIEW_REQUIRED": "Review required — inspect the named findings and acknowledge them below.",
            "WARNING": "Validation completed with warnings — review them before continuing.",
            "PASS": "Validation passed — no blocking findings were reported.",
        }
        self.sanity_state_label.setText(labels.get(worst, f"Validation status: {worst}"))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"Overall: {worst}", f"Last refreshed: {timestamp}", ""]
        lines.extend(f"{name}: {status}" for name, status in statuses.items())
        self.sanity_text.setPlainText("\n".join(lines))
        self.sanity_text.setVisible(True)
        self.sanity_refresh_button.setEnabled(True)

        review_count = sum(status == "REVIEW_REQUIRED" for status in statuses.values())
        self.approve_review.setVisible(review_count > 0)
        if review_count:
            noun = "phase check" if review_count == 1 else "phase checks"
            self.approve_review.setText(
                f"I reviewed the {review_count} {noun} marked Review required")
        self.sanity_go_run.setEnabled(worst not in ("FAIL", "STALE"))
        next_steps = {
            "STALE": "What happens next: run validation again so the checks match the current saved inputs.",
            "FAIL": "What happens next: resolve the failed phase checks, then validate again before opening Run Monitor.",
            "REVIEW_REQUIRED": "What happens next: inspect and acknowledge the review-required findings, then continue to Run Monitor.",
            "WARNING": "What happens next: review the warnings, then continue to Run Monitor when they are acceptable.",
            "PASS": "What happens next: continue to Run Monitor to dry-run or start the validated workflow.",
        }
        self.sanity_next_label.setText(next_steps[worst])

    def _prompt_for_pre_run_validation(self) -> bool:
        """Offer a direct route to validation instead of a dead-end warning box."""
        dialog = QDialog(self)
        dialog.setObjectName("preRunValidationDialog")
        dialog.setWindowTitle(APP_NAME)
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog.setAccessibleName("Pre-run checks required")
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(24, 20, 24, 20)
        dialog_layout.setSpacing(12)

        heading = QLabel("Pre-run checks required")
        heading.setProperty("uiRole", "pageTitle")
        body = QLabel(
            "The saved validation is missing or no longer matches the current inputs and "
            "analysis settings. Open Pre-run checks, validate the current run inputs, "
            "then start the workflow again."
        )
        body.setWordWrap(True)
        body.setAccessibleName(body.text())
        dialog_layout.addWidget(heading)
        dialog_layout.addWidget(body)

        buttons = QDialogButtonBox()
        open_button = buttons.addButton(
            "Open Pre-run checks", QDialogButtonBox.ButtonRole.AcceptRole)
        open_button.setObjectName("preRunValidationOpenButton")
        open_button.setProperty("primary", True)
        open_button.setDefault(True)
        open_button.setAutoDefault(True)
        cancel_button = buttons.addButton(
            "Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setObjectName("preRunValidationCancelButton")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _open_pre_run_validation(self) -> None:
        self.tabs.setCurrentIndex(7)

        def focus_validation_action() -> None:
            button = getattr(self, "sanity_run_button", None)
            if button is not None and button.isVisible() and button.isEnabled():
                button.setFocus(Qt.FocusReason.TabFocusReason)

        # Focus immediately when the page is already laid out, then once more
        # after TaskNavigator finishes the page transition.
        focus_validation_action()
        QTimer.singleShot(0, focus_validation_action)

    def _run_gate_ok(self, *, preflight=None) -> bool:
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
                samples_path = self._configured_samples_path()
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
                    "of interest under Explore results > Figures and tables > Genes, or "
                    "clear the list before running.",
                )
                return False
        if self.project_root is None or self.config is None:
            QMessageBox.warning(self, APP_NAME, "Open or create a project before starting a run.")
            return False

        # Only a current, versioned preflight describes the run about to launch.
        # Downstream phase checks are historical outputs and never substitute for it.
        if preflight is None:
            preflight = validate_current_preflight(self.project_root)
        if not preflight.valid:
            self._update_sanity_state({"01_input_validation": "STALE"}, reset_approval=True)
            if self._prompt_for_pre_run_validation():
                self._open_pre_run_validation()
            return False
        import json

        check_path = self.project_root / "checks" / "01_input_validation.json"
        try:
            payload = json.loads(check_path.read_text(encoding="utf-8"))
            raw_status = payload.get("status", "FAIL")
            status = raw_status if isinstance(raw_status, str) else "FAIL"
        except (OSError, json.JSONDecodeError):
            status = "FAIL"
        statuses = {"01_input_validation": status}
        self._update_sanity_state(statuses)
        if status not in {"PASS", "WARNING", "REVIEW_REQUIRED"}:
            self.tabs.setCurrentIndex(7)
            QMessageBox.warning(
                self, APP_NAME,
                "Cannot start: current input validation failed or returned an unknown status. Resolve the named findings and validate again.")
            return False
        if status == "REVIEW_REQUIRED" and not self.approve_review.isChecked():
            self.tabs.setCurrentIndex(7)
            QMessageBox.warning(
                self, APP_NAME,
                "Current validation contains review-required findings. Review them and tick the acknowledgement on Pre-run checks.")
            return False
        return True

    def _set_launch_preflight_busy(
        self,
        busy: bool,
        *,
        restore_content: bool = True,
    ) -> None:
        """Show launch validation without pretending that a pipeline is running."""
        if busy:
            if self._launch_preflight_ui_state is None:
                self._launch_preflight_ui_state = {
                    "progress_minimum": self.progress.minimum(),
                    "progress_maximum": self.progress.maximum(),
                    "progress_value": self.progress.value(),
                    "progress_label_visible": self.progress_value_label.isVisible(),
                    "status": self.status_label.text(),
                    "phase": self.phase_label.text(),
                    "status_bar": self.statusBar().currentMessage(),
                    "button_enabled": {
                        name: button.isEnabled()
                        for name, button in self.run_action_buttons.items()
                    },
                    "use_wsl_enabled": self.use_wsl.isEnabled(),
                    "page_enabled": [
                        self.tabs.widget(index).isEnabled()
                        for index in range(self.tabs.count())
                    ],
                }
            run_page_index = self.tabs.indexOf(self.run_monitor_page)
            for index in range(self.tabs.count()):
                if index != run_page_index:
                    self.tabs.widget(index).setEnabled(False)
            for button in self.run_action_buttons.values():
                button.setEnabled(False)
            self.use_wsl.setEnabled(False)
            self.progress.setRange(0, 0)
            self.progress_value_label.setVisible(False)
            self.status_label.setText("Checking current scientific inputs before launch...")
            self.phase_label.setText(
                "Pages remain available for review; scientific controls are locked until this check finishes.",
            )
            self.statusBar().showMessage("Checking current scientific inputs before launch...")
            return

        state = self._launch_preflight_ui_state
        self._launch_preflight_ui_state = None
        if state is None:
            return
        if restore_content:
            self.progress.setRange(
                int(state["progress_minimum"]), int(state["progress_maximum"]),
            )
            self.progress.setValue(int(state["progress_value"]))
            self.progress_value_label.setVisible(bool(state["progress_label_visible"]))
            self.status_label.setText(str(state["status"]))
            self.phase_label.setText(str(state["phase"]))
            previous_message = str(state["status_bar"])
            if previous_message:
                self.statusBar().showMessage(previous_message)
            else:
                self.statusBar().clearMessage()
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.statusBar().clearMessage()
        button_enabled = state["button_enabled"]
        if isinstance(button_enabled, dict):
            for name, button in self.run_action_buttons.items():
                button.setEnabled(
                    bool(button_enabled.get(name, False))
                    and self.project_root is not None
                    and not self._run_active
                )
        self.use_wsl.setEnabled(
            bool(state["use_wsl_enabled"])
            and self.project_root is not None
            and not self._run_active
        )
        page_enabled = state.get("page_enabled")
        if isinstance(page_enabled, list):
            for index, enabled in enumerate(page_enabled[: self.tabs.count()]):
                self.tabs.widget(index).setEnabled(bool(enabled))

    def _begin_launch_preflight(self, mode: str) -> None:
        worker = self._launch_preflight_worker
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage(
                "The current scientific inputs are already being checked.", 5000,
            )
            return
        assert self.project_root is not None
        assert self.config is not None
        root = self.project_root
        config_snapshot = self.config.model_dump_json()

        def validate_for_launch():
            return validate_current_preflight(
                root,
                cancel_requested=worker.isInterruptionRequested,
            )

        worker = BackgroundWorker(validate_for_launch)
        worker.done.connect(
            lambda outcome: self._on_launch_preflight_done(
                worker, root, config_snapshot, mode, outcome,
            ),
        )
        worker.failed.connect(
            lambda exc: self._on_launch_preflight_failed(worker, root, exc),
        )
        self._launch_preflight_worker = worker
        self._set_launch_preflight_busy(True)
        worker.start()

    def _on_launch_preflight_done(
        self,
        worker: BackgroundWorker,
        project_root: Path,
        config_snapshot: str,
        mode: str,
        outcome,
    ) -> None:
        if worker is not self._launch_preflight_worker:
            return
        self._launch_preflight_worker = None
        if getattr(self, "_closing", False):
            self._launch_preflight_ui_state = None
            return
        if self.project_root != project_root:
            self._set_launch_preflight_busy(False, restore_content=False)
            self._refresh_export_buttons()
            return
        self._set_launch_preflight_busy(False)
        if self.config is None or self.config.model_dump_json() != config_snapshot:
            self._update_sanity_state(
                {"01_input_validation": "STALE"}, reset_approval=True,
            )
            if self._prompt_for_pre_run_validation():
                self._open_pre_run_validation()
            self._refresh_resume_banner()
            return
        try:
            self._start_snakemake_impl(
                mode,
                _validated_preflight=outcome,
                _validated_root=project_root,
            )
        except Exception as exc:
            self._handle_start_error(exc)

    def _on_launch_preflight_failed(
        self,
        worker: BackgroundWorker,
        project_root: Path,
        exc: object,
    ) -> None:
        if worker is not self._launch_preflight_worker:
            return
        self._launch_preflight_worker = None
        if getattr(self, "_closing", False):
            self._launch_preflight_ui_state = None
            return
        if self.project_root != project_root:
            self._set_launch_preflight_busy(False, restore_content=False)
            self._refresh_export_buttons()
            return
        self._set_launch_preflight_busy(False)
        self._pending_recover = False
        self._refresh_resume_banner()
        QMessageBox.warning(
            self,
            APP_NAME,
            f"Could not verify the current scientific inputs before launch:\n\n{exc}",
        )

    def _handle_start_error(self, exc: Exception) -> None:
        import traceback as _tb

        detail = _tb.format_exc()
        try:
            self.log_text.append(f"Failed to start run: {exc}")
            self.log_text.append(detail)
        except Exception:
            pass
        self._pending_recover = False
        self._set_running_ui(False)
        QMessageBox.critical(self, APP_NAME, f"Failed to start the run:\n\n{exc}")

    def _start_snakemake(self, mode: str) -> None:
        # Never let a failure here crash the app; surface it in the log + a dialog.
        try:
            self._start_snakemake_impl(mode)
        except Exception as exc:
            self._handle_start_error(exc)

    def _start_snakemake_impl(
        self,
        mode: str,
        *,
        _validated_preflight=None,
        _validated_root: Path | None = None,
    ) -> None:
        if self.config is None or self.project_root is None:
            self.statusBar().showMessage(
                "Open or create a project before starting the workflow.", 8000)
            QMessageBox.information(
                self, APP_NAME,
                "Open or create a project first, then return to Run Monitor.")
            return
        if _validated_root is not None and self.project_root != _validated_root:
            return
        launch_worker = self._launch_preflight_worker
        if _validated_preflight is None and launch_worker is not None and launch_worker.isRunning():
            self.statusBar().showMessage(
                "Wait for the current scientific-input check to finish before starting another action.",
                6000,
            )
            return
        # Guard double-starts: one snakemake per directory at a time.
        if self._run_active or (self.runner is not None and self.runner.is_running()):
            self.log_text.append("A run is already active. Stop it before starting another.")
            self._pending_recover = False  # a stranded recover flag would mis-handle the active run's finish
            return
        if _validated_preflight is None:
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
            # Persist the in-memory metadata table so the run uses current edits;
            # Snakemake reads config.input.samples from disk, not the GUI table.
            save_metadata(self.metadata_table.to_dataframe(), self._configured_samples_path())
            # Validate the contrast only for the differential-expression modes; unlock,
            # dry-run and the figure/ppi/goi regenerations reuse existing DE results.
            if not self._save_workflow_settings(validate=mode in ("run", "resume", "recover")):
                return  # invalid contrast; the user was warned
            self._save_resources()
            # Persist figure-style + PPI controls so in-session edits are honored by the run
            # (previously only Save/Regenerate applied them; a plain Run dropped them).
            self._apply_figure_style()
            if mode in ("run", "resume", "recover"):
                self._begin_launch_preflight(mode)
                return
        if mode in ("run", "resume", "recover") and not self._run_gate_ok(
            preflight=_validated_preflight,
        ):
            self._refresh_resume_banner()  # a blocked resume/recover must not leave the banner stranded hidden
            return
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
            self._set_progress_status()
            status = {"figures": "Regenerating figures...",
                      "goi": "Generating genes-of-interest outputs...",
                      "ppi": "Rebuilding PPI network...",
                      "term": "Building enrichment-term heatmap..."}.get(mode, "Running...")
            self._set_run_status(status, "RUNNING")
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
            self._set_run_status("Running..." if mode == "dry-run" else "Unlocking...", "RUNNING")
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
