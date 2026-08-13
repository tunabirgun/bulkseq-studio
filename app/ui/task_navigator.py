"""Guided, responsive navigation for BulkSeq Studio's primary pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _TabBarCompatibility:
    """Small compatibility shim for callers that only configure tab overflow."""

    def __init__(self) -> None:
        self.uses_scroll_buttons = False

    def setUsesScrollButtons(self, enabled: bool) -> None:
        self.uses_scroll_buttons = enabled


class TaskNavigator(QWidget):
    """A QTabWidget-compatible navigator arranged as four scientific stages.

    Page widgets stay in their historical stack order.  The guided presentation
    is intentionally only a view over that stack: existing page indexes, labels,
    ``currentChanged`` and the small QTabWidget API used by the application
    therefore remain stable.
    """

    # Below this width a rail plus results workspace leaves too little room for
    # both the figure/network canvas and its inspector.
    COMPACT_BREAKPOINT = 1366
    STAGE_ORDER = (
        "Project and data",
        "Analysis setup",
        "Validate and run",
        "Explore results",
    )
    STAGE_PAGE_LABELS = {
        "Project and data": ("Project", "Input Data", "Metadata", "Reference Manager"),
        "Analysis setup": ("Workflow Settings", "Resources", "Runtime"),
        "Validate and run": ("Sanity Checks", "Run Monitor"),
        # Results are deliberately task ordered rather than stack ordered.
        "Explore results": ("Outputs", "Reports", "PPI Network"),
    }
    PAGE_STAGES = {
        page: stage
        for stage, pages in STAGE_PAGE_LABELS.items()
        for page in pages
    }
    PAGE_DISPLAY_LABELS = {
        "Project": "Project",
        "Input Data": "Add data",
        "Metadata": "Samples",
        "Reference Manager": "Reference",
        "Workflow Settings": "Analysis settings",
        "Resources": "Compute resources",
        "Runtime": "Runtime estimate",
        "Sanity Checks": "Pre-run checks",
        "Run Monitor": "Run monitor",
        "Reports": "Reports",
        "Outputs": "Figures and tables",
        "PPI Network": "Protein network",
    }

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskNavigator")
        self._tab_bar = _TabBarCompatibility()
        self._elide_mode = Qt.TextElideMode.ElideRight
        self._buttons: list[QToolButton] = []
        self._labels: list[str] = []
        self._corner_widget: QWidget | None = None
        self._compact = False
        self._active_stage = self.STAGE_ORDER[0]
        self._last_page_by_stage: dict[str, int] = {}

        self.setStyleSheet(
            """
            QWidget#taskNavigatorRail { border-right: 1px solid palette(mid); }
            QToolButton[taskNavigatorStageButton="true"] {
                background: transparent;
                border: 1px solid transparent;
                border-left: 3px solid transparent;
                border-radius: 4px;
                color: palette(text);
                min-height: 34px;
                padding: 3px 8px;
                text-align: left;
            }
            QToolButton[taskNavigatorStageButton="true"]:hover { background-color: palette(alternate-base); }
            QToolButton[taskNavigatorStageButton="true"]:checked {
                background-color: palette(alternate-base);
                border-left-color: palette(link);
                color: palette(link);
                font-weight: 600;
            }
            QToolButton[taskNavigatorStageButton="true"]:focus {
                border: 2px dotted palette(text);
            }
            QToolButton[taskNavigatorStageButton="true"]:checked:focus {
                border: 2px dotted palette(text);
                border-left: 3px solid palette(link);
            }
            QToolButton[taskNavigatorItem="true"] {
                background: transparent;
                border: 1px solid transparent;
                border-bottom: 2px solid transparent;
                border-radius: 4px;
                color: palette(text);
                min-height: 30px;
                padding: 2px 8px;
            }
            QToolButton[taskNavigatorItem="true"]:hover { background-color: palette(alternate-base); }
            QToolButton[taskNavigatorItem="true"]:checked {
                border-bottom-color: palette(link);
                color: palette(link);
                font-weight: 600;
            }
            QToolButton[taskNavigatorItem="true"]:focus { border: 2px dotted palette(text); }
            QLabel[taskNavigatorSelectorLabel="true"] { color: palette(placeholder-text); font-weight: 600; }
            """
        )

        self._rail = QWidget(self)
        self._rail.setObjectName("taskNavigatorRail")
        self._rail.setAccessibleName("Workflow stages")
        self._rail.setMinimumWidth(184)
        self._rail.setMaximumWidth(240)
        self._rail_layout = QVBoxLayout(self._rail)
        self._rail_layout.setContentsMargins(8, 10, 8, 10)
        self._rail_layout.setSpacing(4)
        self._stage_buttons: list[QToolButton] = []
        self._stage_group = QButtonGroup(self)
        self._stage_group.setExclusive(True)
        self._stage_group.idClicked.connect(self._activate_stage)
        for stage_index, stage in enumerate(self.STAGE_ORDER):
            button = QToolButton(self._rail)
            button.setObjectName(f"taskNavigatorStage{stage_index}")
            button.setText(stage)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setAccessibleName(
                f"Stage {stage_index + 1} of {len(self.STAGE_ORDER)}, {stage}")
            button.setToolTip(f"{stage}: select this workflow stage")
            button.setProperty("taskNavigatorStageButton", True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._stage_group.addButton(button, stage_index)
            self._stage_buttons.append(button)
            self._rail_layout.addWidget(button)
        self._rail_layout.addStretch(1)

        self._header = QWidget(self)
        self._header.setObjectName("taskNavigatorHeader")
        header_layout = QHBoxLayout(self._header)
        # Keep selector labels and the theme action away from the window edge.
        # A zero inset made the leading "Area" label look visibly clipped even
        # when its glyph metrics technically fit.
        header_layout.setContentsMargins(12, 6, 8, 8)
        header_layout.setSpacing(8)

        self._compact_stage_label = QLabel("Area", self._header)
        self._compact_stage_label.setObjectName("taskNavigatorCompactStageLabel")
        self._compact_stage_label.setProperty("taskNavigatorSelectorLabel", True)
        self._compact_stage_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._compact_stage_selector = QComboBox(self._header)
        self._compact_stage_selector.setObjectName("taskNavigatorCompactStageSelector")
        self._compact_stage_selector.setAccessibleName("Select workflow area")
        self._compact_stage_selector.setToolTip("Select a workflow area")
        self._compact_stage_selector.setMinimumWidth(160)
        self._compact_stage_selector.setMaximumWidth(260)
        for stage in self.STAGE_ORDER:
            self._compact_stage_selector.addItem(stage, stage)
        self._compact_stage_label.setBuddy(self._compact_stage_selector)
        self._compact_stage_selector.currentIndexChanged.connect(self._on_compact_stage_changed)

        self._compact_page_label = QLabel("View", self._header)
        self._compact_page_label.setObjectName("taskNavigatorCompactLabel")
        self._compact_page_label.setProperty("taskNavigatorSelectorLabel", True)
        self._compact_page_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._compact_selector = QComboBox(self._header)
        # Retained for existing callers and UI automation.
        self._compact_selector.setObjectName("taskNavigatorCompactSelector")
        self._compact_selector.setAccessibleName("Select workflow view")
        self._compact_selector.setToolTip("Select a view in the current workflow area")
        self._compact_selector.setMinimumWidth(160)
        self._compact_selector.setMaximumWidth(260)
        self._compact_selector.currentIndexChanged.connect(self._on_compact_page_changed)
        self._compact_page_label.setBuddy(self._compact_selector)

        self._local_host = QWidget(self._header)
        self._local_host.setObjectName("taskNavigatorLocalTasks")
        self._local_layout = QHBoxLayout(self._local_host)
        self._local_layout.setContentsMargins(0, 0, 0, 0)
        self._local_layout.setSpacing(4)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.idClicked.connect(self.setCurrentIndex)

        self._corner_host = QWidget(self._header)
        self._corner_host.setObjectName("taskNavigatorHeaderWidgetSlot")
        self._corner_layout = QHBoxLayout(self._corner_host)
        self._corner_layout.setContentsMargins(0, 0, 0, 0)
        self._corner_layout.setSpacing(0)

        header_layout.addWidget(self._compact_stage_label)
        header_layout.addWidget(self._compact_stage_selector)
        header_layout.addWidget(self._compact_page_label)
        header_layout.addWidget(self._compact_selector)
        header_layout.addWidget(self._local_host, 1)
        header_layout.addStretch(1)
        header_layout.addWidget(self._corner_host)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("taskNavigatorStack")
        self._stack.currentChanged.connect(self._on_stack_changed)

        content = QWidget(self)
        content.setObjectName("taskNavigatorContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._header)
        content_layout.addWidget(self._stack, 1)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(self._rail)
        root.addWidget(content, 1)
        self._update_compact_mode(self.width())

    def addTab(self, page: QWidget, label: str) -> int:
        """Add a page and return its stable stack index."""
        index = self._stack.addWidget(page)
        self._labels.append(label)
        self._add_local_item(index, label)
        stage = self._stage_for(label)
        self._last_page_by_stage.setdefault(stage, index)
        # The visual-results stage intentionally opens its central workspace first.
        if label == "Outputs":
            self._last_page_by_stage[stage] = index
        self._sync_selection(self.currentIndex())
        return index

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < self.count():
            focused_navigation_role = self._focused_navigation_role()
            self._stack.setCurrentIndex(index)
            self._restore_navigation_focus(focused_navigation_role, index)

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def widget(self, index: int) -> QWidget | None:
        return self._stack.widget(index)

    def currentWidget(self) -> QWidget | None:
        return self._stack.currentWidget()

    def count(self) -> int:
        return self._stack.count()

    def tabText(self, index: int) -> str:
        return self._labels[index] if 0 <= index < len(self._labels) else ""

    def indexOf(self, page: QWidget) -> int:
        return self._stack.indexOf(page)

    def setCurrentWidget(self, page: QWidget) -> None:
        index = self.indexOf(page)
        if index >= 0:
            self.setCurrentIndex(index)

    def setCornerWidget(self, widget: QWidget | None, corner: Qt.Corner | None = None) -> None:
        """Place an auxiliary header control (such as the theme toggle) at right."""
        del corner
        if self._corner_widget is not None:
            self._corner_layout.removeWidget(self._corner_widget)
            self._corner_widget.hide()
            self._corner_widget.setParent(None)
        self._corner_widget = widget
        if widget is not None:
            self._corner_layout.addWidget(widget)
            widget.show()

    def cornerWidget(self, corner: Qt.Corner | None = None) -> QWidget | None:
        del corner
        return self._corner_widget

    def setElideMode(self, mode: Qt.TextElideMode) -> None:
        self._elide_mode = mode

    def tabBar(self) -> _TabBarCompatibility:
        return self._tab_bar

    def isCompact(self) -> bool:
        return self._compact

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._update_compact_mode(event.size().width())
        super().resizeEvent(event)

    def _update_compact_mode(self, width: int) -> None:
        compact = width < self.COMPACT_BREAKPOINT
        if compact == self._compact:
            return
        current_page_button = self._button_for_index(self.currentIndex())
        stage_button = self._stage_buttons[self._stage_index(self._active_stage)]
        page_had_focus = any(button.hasFocus() for button in self._buttons)
        stage_had_focus = any(button.hasFocus() for button in self._stage_buttons)
        selector_had_focus = self._compact_selector.hasFocus()
        stage_selector_had_focus = self._compact_stage_selector.hasFocus()
        self._compact = compact
        self._rail.setVisible(not compact)
        self._local_host.setVisible(not compact)
        for widget in (
            self._compact_stage_label,
            self._compact_stage_selector,
            self._compact_page_label,
            self._compact_selector,
        ):
            widget.setVisible(compact)
        self._refresh_stage_controls()
        if compact and page_had_focus:
            self._compact_selector.setFocus(Qt.FocusReason.OtherFocusReason)
        elif compact and stage_had_focus:
            self._compact_stage_selector.setFocus(Qt.FocusReason.OtherFocusReason)
        elif not compact and selector_had_focus and current_page_button is not None:
            current_page_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif not compact and stage_selector_had_focus:
            stage_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _add_local_item(self, index: int, label: str) -> None:
        button = QToolButton(self._local_host)
        button.setObjectName(f"taskNavigatorItem{index}")
        button.setText(self._display_label(label))
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setAccessibleName(f"Go to {label}")
        button.setToolTip(f"Go to {label}")
        button.setProperty("taskNavigatorItem", True)
        button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._button_group.addButton(button, index)
        self._buttons.append(button)
        self._local_layout.addWidget(button)
        button.setVisible(self._stage_for(label) == self._active_stage)

    def _activate_stage(self, stage_index: int) -> None:
        if 0 <= stage_index < len(self.STAGE_ORDER):
            self._activate_stage_name(self.STAGE_ORDER[stage_index])

    def _on_compact_stage_changed(self, selector_index: int) -> None:
        if 0 <= selector_index < len(self.STAGE_ORDER):
            self._activate_stage_name(self.STAGE_ORDER[selector_index])

    def _on_compact_page_changed(self, selector_index: int) -> None:
        if 0 <= selector_index < self._compact_selector.count():
            index = self._compact_selector.itemData(selector_index)
            if isinstance(index, int):
                self.setCurrentIndex(index)

    def _activate_stage_name(self, stage: str) -> None:
        if stage not in self.STAGE_ORDER:
            return
        self._active_stage = stage
        target = self._last_page_by_stage.get(stage)
        if target is None:
            candidates = self._stage_page_indexes(stage)
            target = candidates[0] if candidates else None
        self._refresh_stage_controls()
        if target is not None:
            self.setCurrentIndex(target)

    def _on_stack_changed(self, index: int) -> None:
        if not 0 <= index < self.count():
            return
        stage = self._stage_for(self.tabText(index))
        self._active_stage = stage
        self._last_page_by_stage[stage] = index
        self._sync_selection(index)
        self.currentChanged.emit(index)

    def _sync_selection(self, index: int) -> None:
        if not 0 <= index < self.count():
            return
        stage = self._stage_for(self.tabText(index))
        self._active_stage = stage
        self._refresh_stage_controls()
        for candidate in self._buttons:
            selected = candidate is self._button_for_index(index)
            candidate.setChecked(selected)
            candidate.setProperty("selectedTask", selected)
            self._repolish(candidate)

    def _refresh_stage_controls(self) -> None:
        stage_index = self._stage_index(self._active_stage)
        for index, button in enumerate(self._stage_buttons):
            selected = index == stage_index
            button.setChecked(selected)
            button.setProperty("selectedStage", selected)
            self._repolish(button)
        active_indexes = self._stage_page_indexes(self._active_stage)
        active_set = set(active_indexes)
        # Reinsert only the active stage in its declared task order.  This is
        # important for Results, whose visual order is Outputs, Reports, PPI
        # even though its stable stack indexes are 10, 9, 11.
        while self._local_layout.count():
            self._local_layout.takeAt(0)
        for index, button in enumerate(self._buttons):
            button.setVisible(index in active_set and not self._compact)
        for index in active_indexes:
            self._local_layout.addWidget(self._buttons[index])
        self._set_compact_stage(self._active_stage)
        self._rebuild_compact_pages()

    def _rebuild_compact_pages(self) -> None:
        current = self.currentIndex()
        self._compact_selector.blockSignals(True)
        self._compact_selector.clear()
        for index in self._stage_page_indexes(self._active_stage):
            self._compact_selector.addItem(self._display_label(self.tabText(index)), index)
        selector_index = self._compact_selector.findData(current)
        if selector_index < 0 and self._compact_selector.count():
            selector_index = 0
        if selector_index >= 0:
            self._compact_selector.setCurrentIndex(selector_index)
        self._compact_selector.blockSignals(False)

    def _set_compact_stage(self, stage: str) -> None:
        stage_index = self._stage_index(stage)
        if self._compact_stage_selector.currentIndex() != stage_index:
            self._compact_stage_selector.blockSignals(True)
            self._compact_stage_selector.setCurrentIndex(stage_index)
            self._compact_stage_selector.blockSignals(False)

    def _stage_page_indexes(self, stage: str) -> list[int]:
        indexes: list[int] = []
        for label in self.STAGE_PAGE_LABELS.get(stage, ()):
            try:
                indexes.append(self._labels.index(label))
            except ValueError:
                continue
        return indexes

    def _stage_for(self, label: str) -> str:
        return self.PAGE_STAGES.get(label, self.STAGE_ORDER[0])

    def _display_label(self, label: str) -> str:
        return self.PAGE_DISPLAY_LABELS.get(label, label)

    def _stage_index(self, stage: str) -> int:
        return self.STAGE_ORDER.index(stage)

    def _button_for_index(self, index: int) -> QToolButton | None:
        return self._buttons[index] if 0 <= index < len(self._buttons) else None

    def _focused_navigation_role(self) -> str | None:
        """Return the kind of navigation control that currently owns focus."""
        focused = QApplication.focusWidget()
        if focused in self._stage_buttons:
            return "stage"
        if focused in self._buttons:
            return "page"
        if focused is self._compact_stage_selector:
            return "compact-stage"
        if focused is self._compact_selector:
            return "compact-page"
        return None

    def _restore_navigation_focus(self, role: str | None, index: int) -> None:
        """Keep keyboard focus aligned with the selected page after navigation."""
        if role is None:
            return
        if self._compact:
            target: QWidget | None = (
                self._compact_stage_selector
                if role in {"stage", "compact-stage"}
                else self._compact_selector
            )
        elif role in {"stage", "compact-stage"}:
            stage = self._stage_for(self.tabText(index))
            target = self._stage_buttons[self._stage_index(stage)]
        else:
            target = self._button_for_index(index)
        if target is not None and target.isVisible():
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
