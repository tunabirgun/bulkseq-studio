from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel

# Shared design-language palette. Kept as Python constants for status_color and the
# pill helper; the same hex values are typed directly into APP_QSS below.
PRIMARY = "#2C6FB6"
PRIMARY_HOVER = "#2560A0"
PRIMARY_PRESSED = "#1E4F86"
BACKGROUND = "#F5F7FA"
SURFACE = "#FFFFFF"
BORDER = "#D7DEE6"
TEXT = "#1F2933"
MUTED_TEXT = "#6B7785"
SUCCESS = "#2E7D32"
WARNING = "#B26A00"
ERROR = "#C0392B"
REVIEW = "#6A1B9A"

BASE_FONT_FAMILY = "Segoe UI"
BASE_FONT_POINT_SIZE = 10

# Status string -> accent hex. Keys match the exact readiness status strings.
_STATUS_COLORS = {
    "PASS": SUCCESS,
    "WARNING": WARNING,
    "REVIEW_REQUIRED": REVIEW,
    "FAIL": ERROR,
}

# Light tint background per status for status pills.
_STATUS_PILL_BG = {
    "PASS": "#E6F2E6",
    "WARNING": "#FBEEDA",
    "REVIEW_REQUIRED": "#F1E5F6",
    "FAIL": "#F8E3E0",
}


# Complete application style sheet. Plain triple-quoted string with hard-coded hex so
# Qt's literal { } braces never collide with Python string formatting.
APP_QSS = """
/* ---- Window / dialog surfaces ---- */
QMainWindow, QDialog {
    background-color: #F5F7FA;
    color: #1F2933;
}

QWidget {
    color: #1F2933;
    font-family: "Segoe UI";
    font-size: 10pt;
}

/* ---- Labels ---- */
QLabel {
    color: #1F2933;
    background: transparent;
}

QLabel:disabled {
    color: #6B7785;
}

/* ---- Tabs ---- */
QTabWidget::pane {
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    background-color: #FFFFFF;
    top: -1px;
}

QTabBar::tab {
    background-color: #ECF0F5;
    color: #6B7785;
    border: 1px solid #D7DEE6;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: #FFFFFF;
    color: #1F2933;
    border-color: #D7DEE6;
}

QTabBar::tab:hover:!selected {
    background-color: #F5F7FA;
    color: #1F2933;
}

QTabBar::tab:!selected {
    margin-top: 2px;
}

/* ---- Buttons ---- */
QPushButton {
    background-color: #FFFFFF;
    color: #1F2933;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}

QPushButton:hover {
    background-color: #F0F4F9;
    border-color: #C3CDD8;
}

QPushButton:pressed {
    background-color: #E4EBF3;
    border-color: #B6C2CF;
}

QPushButton:disabled {
    background-color: #F0F2F5;
    color: #A4AEB9;
    border-color: #E1E6EC;
}

QPushButton:focus {
    border-color: #2C6FB6;
}

/* Primary-action buttons: QPushButton[primary="true"] */
QPushButton[primary="true"] {
    background-color: #2C6FB6;
    color: #FFFFFF;
    border: 1px solid #2C6FB6;
    font-weight: 600;
}

QPushButton[primary="true"]:hover {
    background-color: #2560A0;
    border-color: #2560A0;
}

QPushButton[primary="true"]:pressed {
    background-color: #1E4F86;
    border-color: #1E4F86;
}

QPushButton[primary="true"]:disabled {
    background-color: #9FBEDD;
    color: #EAF1F8;
    border-color: #9FBEDD;
}

QPushButton[primary="true"]:focus {
    border-color: #1E4F86;
}

/* ---- Text inputs and combo/spin ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #FFFFFF;
    color: #1F2933;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #2C6FB6;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #2C6FB6;
}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: #F0F2F5;
    color: #A4AEB9;
    border-color: #E1E6EC;
}

QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {
    background-color: #F7F9FB;
    color: #1F2933;
}

/* ---- ComboBox subcontrols and popup ---- */
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #D7DEE6;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}

/* The combo arrow is drawn by the Fusion style (set in apply_theme); overriding
   its image here produced an empty square on some Qt builds. */

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #1F2933;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    outline: none;
    selection-background-color: #2C6FB6;
    selection-color: #FFFFFF;
}

/* ---- SpinBox subcontrols ---- */
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #D7DEE6;
    border-top-right-radius: 6px;
    background-color: #F0F4F9;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid #D7DEE6;
    border-bottom-right-radius: 6px;
    background-color: #F0F4F9;
}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #E4EBF3;
}

/* Spin arrows are left to the Fusion style (set in apply_theme) so they render
   as crisp native triangles rather than CSS-border boxes. */

/* ---- Group boxes (cards) ---- */
QGroupBox {
    background-color: #FFFFFF;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0px 4px;
    color: #1F2933;
    background-color: transparent;
}

/* ---- Tables ---- */
QTableWidget, QTableView {
    background-color: #FFFFFF;
    alternate-background-color: #F7F9FB;
    gridline-color: #E4E9EF;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    selection-background-color: #D9E6F4;
    selection-color: #1F2933;
    outline: none;
}

QTableWidget::item, QTableView::item {
    padding: 4px 6px;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: #D9E6F4;
    color: #1F2933;
}

QHeaderView {
    background-color: #ECF0F5;
    border: none;
}

QHeaderView::section {
    background-color: #ECF0F5;
    color: #6B7785;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #D7DEE6;
    border-bottom: 1px solid #D7DEE6;
    font-weight: 600;
}

QHeaderView::section:last {
    border-right: none;
}

QTableCornerButton::section {
    background-color: #ECF0F5;
    border: none;
    border-right: 1px solid #D7DEE6;
    border-bottom: 1px solid #D7DEE6;
}

/* ---- List widgets ---- */
QListWidget {
    background-color: #FFFFFF;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    outline: none;
    padding: 2px;
}

QListWidget::item {
    padding: 4px 6px;
    border-radius: 4px;
}

QListWidget::item:selected {
    background-color: #2C6FB6;
    color: #FFFFFF;
}

QListWidget::item:hover:!selected {
    background-color: #F0F4F9;
}

/* ---- Checkboxes ---- */
QCheckBox {
    color: #1F2933;
    spacing: 7px;
    background: transparent;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #C3CDD8;
    border-radius: 4px;
    background-color: #FFFFFF;
}

QCheckBox::indicator:hover {
    border-color: #2C6FB6;
}

QCheckBox::indicator:checked {
    background-color: #2C6FB6;
    border-color: #2C6FB6;
}

QCheckBox::indicator:checked:hover {
    background-color: #2560A0;
    border-color: #2560A0;
}

QCheckBox::indicator:disabled {
    border-color: #E1E6EC;
    background-color: #F0F2F5;
}

QCheckBox:disabled {
    color: #A4AEB9;
}

/* ---- Progress bar ---- */
QProgressBar {
    background-color: #ECF0F5;
    border: 1px solid #D7DEE6;
    border-radius: 6px;
    text-align: center;
    color: #1F2933;
    min-height: 16px;
}

QProgressBar::chunk {
    background-color: #2C6FB6;
    border-radius: 5px;
}

/* ---- Scroll bars ---- */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #C3CDD8;
    border-radius: 5px;
    min-height: 28px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background-color: #AEB9C6;
}

QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background-color: #C3CDD8;
    border-radius: 5px;
    min-width: 28px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #AEB9C6;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    height: 0px;
    background: none;
    border: none;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

/* ---- Tooltips ---- */
QToolTip {
    background-color: #1F2933;
    color: #FFFFFF;
    border: none;
    padding: 4px 7px;
    border-radius: 4px;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the BulkSeq Studio light theme: Fusion style, base font, style sheet.

    Fusion renders combo/spin sub-control arrows as crisp native triangles and
    is consistent across platforms, avoiding the empty-square arrows that the
    native Windows style showed under a heavy style sheet.
    """
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    font = QFont(BASE_FONT_FAMILY, BASE_FONT_POINT_SIZE)
    app.setFont(font)
    app.setStyleSheet(APP_QSS)


def status_color(status: str) -> str:
    """Return the accent hex for a readiness status string.

    Unknown statuses fall back to muted text so callers never KeyError.
    """
    return _STATUS_COLORS.get(status, MUTED_TEXT)


def status_pill(status: str, text: str | None = None) -> QLabel:
    """Build a tinted, rounded status pill QLabel.

    The tint is applied via inline setStyleSheet on the label so it overrides the
    global QLabel rule. ``text`` defaults to the status string itself.
    """
    label = QLabel(text if text is not None else status)
    label.setAlignment(Qt.AlignCenter)
    fg = status_color(status)
    bg = _STATUS_PILL_BG.get(status, "#EDEFF2")
    label.setStyleSheet(
        "QLabel {"
        f" color: {fg};"
        f" background-color: {bg};"
        " border-radius: 6px;"
        " padding: 2px 8px;"
        " font-weight: 600;"
        "}"
    )
    return label
