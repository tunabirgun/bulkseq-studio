from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QToolButton, QWidget

from app.ui.task_navigator import TaskNavigator


PAGES = (
    "Project",
    "Input Data",
    "Metadata",
    "Reference Manager",
    "Workflow Settings",
    "Resources",
    "Runtime",
    "Sanity Checks",
    "Run Monitor",
    "Reports",
    "Outputs",
    "PPI Network",
)
STAGES = (
    "Project and data",
    "Analysis setup",
    "Validate and run",
    "Explore results",
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _navigator() -> TaskNavigator:
    navigator = TaskNavigator()
    for expected_index, label in enumerate(PAGES):
        assert navigator.addTab(QWidget(), label) == expected_index
    return navigator


def _show_wide(navigator: TaskNavigator) -> None:
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT, 700)
    navigator.show()
    QApplication.processEvents()


def test_page_order_and_tab_compatibility_api() -> None:
    _app()
    navigator = _navigator()

    assert navigator.count() == len(PAGES)
    assert tuple(navigator.tabText(index) for index in range(navigator.count())) == PAGES
    assert navigator.currentIndex() == 0
    assert navigator.widget(11) is not None
    assert navigator.indexOf(navigator.widget(11)) == 11

    target = navigator.widget(8)
    assert target is not None
    navigator.setCurrentWidget(target)
    assert navigator.currentIndex() == 8
    assert navigator.currentWidget() is target


def test_index_signal_and_original_local_page_accessibility_are_preserved() -> None:
    _app()
    navigator = _navigator()
    signal_spy = QSignalSpy(navigator.currentChanged)

    navigator.setCurrentIndex(5)

    assert navigator.currentIndex() == 5
    assert signal_spy.count() == 1
    selected = navigator.findChild(QToolButton, "taskNavigatorItem5")
    assert selected is not None
    assert selected.accessibleName() == "Go to Resources"
    assert selected.property("selectedTask") is True
    assert navigator.findChild(QComboBox, "taskNavigatorCompactSelector").accessibleName() == "Select workflow view"


def test_wide_mode_has_exactly_four_stage_buttons_and_only_current_stage_pages() -> None:
    _app()
    navigator = _navigator()
    _show_wide(navigator)

    stage_buttons = [
        navigator.findChild(QToolButton, f"taskNavigatorStage{index}")
        for index in range(4)
    ]
    assert all(stage_buttons)
    assert len(stage_buttons) == 4
    visible_stage_text = tuple(button.text() for button in stage_buttons)
    assert visible_stage_text == STAGES
    assert all(not re.match(r"\s*\d+\s*[.)-]", text) for text in visible_stage_text)
    assert tuple(button.accessibleName() for button in stage_buttons) == tuple(
        f"Stage {index + 1} of {len(STAGES)}, {stage}"
        for index, stage in enumerate(STAGES)
    )
    assert sum(
        navigator._local_layout.itemAt(index).widget().isVisible()
        for index in range(navigator._local_layout.count())
    ) == 4

    navigator.setCurrentIndex(6)
    QApplication.processEvents()
    visible = [
        navigator._local_layout.itemAt(index).widget().text()
        for index in range(navigator._local_layout.count())
        if navigator._local_layout.itemAt(index).widget().isVisible()
    ]
    assert visible == ["Analysis settings", "Compute resources", "Runtime estimate"]
    assert len(visible) <= 4


def test_results_stage_has_outputs_first_while_stack_indexes_remain_stable() -> None:
    _app()
    navigator = _navigator()
    _show_wide(navigator)
    stage = navigator.findChild(QToolButton, "taskNavigatorStage3")
    assert stage is not None
    stage.click()
    QApplication.processEvents()

    assert navigator.currentIndex() == 10
    visible = [
        navigator._local_layout.itemAt(index).widget().text()
        for index in range(navigator._local_layout.count())
        if navigator._local_layout.itemAt(index).widget().isVisible()
    ]
    assert visible == ["Figures and tables", "Reports", "Protein network"]
    assert navigator.tabText(9) == "Reports"
    assert navigator.tabText(10) == "Outputs"


def test_compact_stage_and_page_selectors_are_scoped_and_reach_every_page_in_two_actions() -> None:
    _app()
    navigator = _navigator()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 700)
    navigator.show()
    QApplication.processEvents()
    stage_selector = navigator.findChild(QComboBox, "taskNavigatorCompactStageSelector")
    page_selector = navigator.findChild(QComboBox, "taskNavigatorCompactSelector")

    assert navigator.isCompact()
    assert stage_selector is not None and page_selector is not None
    assert stage_selector.isVisible() and page_selector.isVisible()
    assert stage_selector.count() == 4
    assert [stage_selector.itemText(index) for index in range(stage_selector.count())] == list(STAGES)
    assert [page_selector.itemText(index) for index in range(page_selector.count())] == [
        "Project", "Add data", "Samples", "Reference",
    ]
    stage_label = navigator.findChild(QLabel, "taskNavigatorCompactStageLabel")
    page_label = navigator.findChild(QLabel, "taskNavigatorCompactLabel")
    assert stage_label is not None and page_label is not None
    assert stage_label.text() == "Area"
    assert page_label.text() == "View"
    assert stage_label.mapTo(navigator, stage_label.rect().topLeft()).x() >= 10
    assert stage_label.mapTo(navigator, stage_label.rect().topLeft()).y() >= 4
    assert stage_selector.geometry().left() - stage_label.geometry().right() <= 12
    assert page_selector.geometry().left() - page_label.geometry().right() <= 12

    for index, label in enumerate(PAGES):
        stage = TaskNavigator.PAGE_STAGES[label]
        stage_selector.setCurrentIndex(TaskNavigator.STAGE_ORDER.index(stage))
        QApplication.processEvents()
        page_index = page_selector.findData(index)
        assert page_index >= 0, f"{label} was not scoped into its stage page selector"
        page_selector.setCurrentIndex(page_index)
        QApplication.processEvents()
        assert navigator.currentIndex() == index, f"{label} requires more than stage then page"

    assert [page_selector.itemText(index) for index in range(page_selector.count())] == [
        "Figures and tables", "Reports", "Protein network",
    ]


def test_responsive_switch_preserves_focus_for_page_and_stage_controls() -> None:
    _app()
    navigator = _navigator()
    _show_wide(navigator)
    navigator.setCurrentIndex(6)
    QApplication.processEvents()
    page_button = navigator.findChild(QToolButton, "taskNavigatorItem6")
    stage_button = navigator.findChild(QToolButton, "taskNavigatorStage1")
    page_selector = navigator.findChild(QComboBox, "taskNavigatorCompactSelector")
    stage_selector = navigator.findChild(QComboBox, "taskNavigatorCompactStageSelector")
    assert page_button is not None and stage_button is not None
    assert page_selector is not None and stage_selector is not None

    page_button.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 700)
    QApplication.processEvents()
    assert page_selector.hasFocus()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT, 700)
    QApplication.processEvents()
    assert page_button.hasFocus()

    stage_button.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 700)
    QApplication.processEvents()
    assert stage_selector.hasFocus()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT, 700)
    QApplication.processEvents()
    assert stage_button.hasFocus()


def test_responsive_switch_never_leaves_focus_on_hidden_inactive_navigation() -> None:
    _app()
    navigator = _navigator()
    _show_wide(navigator)
    navigator.setCurrentIndex(6)
    QApplication.processEvents()
    inactive_stage = navigator.findChild(QToolButton, "taskNavigatorStage0")
    non_current_page = navigator.findChild(QToolButton, "taskNavigatorItem5")
    stage_selector = navigator.findChild(QComboBox, "taskNavigatorCompactStageSelector")
    page_selector = navigator.findChild(QComboBox, "taskNavigatorCompactSelector")
    assert inactive_stage is not None and non_current_page is not None
    assert stage_selector is not None and page_selector is not None

    inactive_stage.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 700)
    QApplication.processEvents()
    assert stage_selector.hasFocus()
    assert not inactive_stage.isVisible()

    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT, 700)
    QApplication.processEvents()
    non_current_page.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 700)
    QApplication.processEvents()
    assert page_selector.hasFocus()
    assert not non_current_page.isVisible()


def test_wide_programmatic_navigation_rehomes_stage_and_page_focus() -> None:
    _app()
    navigator = _navigator()
    _show_wide(navigator)
    explore_stage = navigator.findChild(QToolButton, "taskNavigatorStage3")
    project_stage = navigator.findChild(QToolButton, "taskNavigatorStage0")
    resources_page = navigator.findChild(QToolButton, "taskNavigatorItem5")
    runtime_page = navigator.findChild(QToolButton, "taskNavigatorItem6")
    assert explore_stage is not None and project_stage is not None
    assert resources_page is not None and runtime_page is not None

    QTest.mouseClick(explore_stage, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert navigator.currentIndex() == 10
    assert explore_stage.hasFocus()

    navigator.setCurrentIndex(0)
    QApplication.processEvents()
    assert project_stage.hasFocus()
    assert project_stage.isChecked()
    assert not explore_stage.hasFocus()
    QTest.keyClick(project_stage, Qt.Key.Key_Space)
    QApplication.processEvents()
    assert navigator.currentIndex() == 0

    navigator.setCurrentIndex(5)
    resources_page.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.setCurrentIndex(6)
    QApplication.processEvents()
    assert runtime_page.hasFocus()
    assert runtime_page.isChecked()
    assert not resources_page.hasFocus()
    QTest.keyClick(runtime_page, Qt.Key.Key_Space)
    QApplication.processEvents()
    assert navigator.currentIndex() == 6


def test_compact_programmatic_navigation_keeps_focus_on_matching_selector() -> None:
    _app()
    navigator = _navigator()
    navigator.resize(TaskNavigator.COMPACT_BREAKPOINT - 1, 700)
    navigator.show()
    QApplication.processEvents()
    stage_selector = navigator.findChild(QComboBox, "taskNavigatorCompactStageSelector")
    page_selector = navigator.findChild(QComboBox, "taskNavigatorCompactSelector")
    assert stage_selector is not None and page_selector is not None
    assert navigator.isCompact()

    stage_selector.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.setCurrentIndex(10)
    QApplication.processEvents()
    assert stage_selector.hasFocus()
    assert stage_selector.currentData() == "Explore results"

    page_selector.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    navigator.setCurrentIndex(9)
    QApplication.processEvents()
    assert page_selector.hasFocus()
    assert page_selector.currentData() == 9


def test_programmatic_navigation_does_not_steal_auxiliary_focus() -> None:
    _app()
    navigator = _navigator()
    _show_wide(navigator)
    auxiliary = QToolButton()
    auxiliary.setText("Theme")
    navigator.setCornerWidget(auxiliary)
    auxiliary.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()

    navigator.setCurrentIndex(10)
    QApplication.processEvents()

    assert auxiliary.hasFocus()
    assert not any(button.hasFocus() for button in navigator._stage_buttons)
    assert not any(button.hasFocus() for button in navigator._buttons)


def test_header_widget_slot_replaces_corner_widget() -> None:
    _app()
    navigator = _navigator()
    first = QLabel("Theme")
    second = QLabel("Help")
    navigator.setCornerWidget(first, Qt.Corner.TopRightCorner)
    navigator.setCornerWidget(second, Qt.Corner.TopRightCorner)

    assert navigator.cornerWidget() is second
    assert first.parent() is None
    assert second.parentWidget().objectName() == "taskNavigatorHeaderWidgetSlot"
