from __future__ import annotations

import tempfile
from pathlib import Path
from string import Template

import sys

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel

# Two palettes drive a light and a dark theme. Every token below has an entry in
# both maps; the QSS template references them as $TOKEN so the literal { } braces
# of the style sheet never collide with substitution (string.Template only
# touches the $placeholders). Text/background pairs meet WCAG-AA contrast
# (>=4.5:1), verified by computation (see CHANGELOG 0.8.0).

LIGHT_PALETTE: dict[str, str] = {
    "PRIMARY": "#2C6FB6",
    "PRIMARY_HOVER": "#2560A0",
    "PRIMARY_PRESSED": "#1E4F86",
    "ON_PRIMARY": "#FFFFFF",
    "PRIMARY_DISABLED_BG": "#9FBEDD",
    "PRIMARY_DISABLED_TEXT": "#3F4D5A",
    "BACKGROUND": "#F5F7FA",
    "SURFACE": "#FFFFFF",
    "BORDER": "#D7DEE6",
    "TEXT": "#1F2933",
    "MUTED_TEXT": "#4F5A67",
    "SELECTION_BG": "#2C6FB6",
    "SELECTION_TEXT": "#FFFFFF",
    "TAB_BG_INACTIVE": "#ECF0F5",
    "TAB_BG_ACTIVE": "#FFFFFF",
    "TAB_TEXT_INACTIVE": "#4F5A67",
    "TAB_HOVER_BG": "#F5F7FA",
    "BUTTON_BG": "#FFFFFF",
    "BUTTON_BG_HOVER": "#F0F4F9",
    "BUTTON_BG_PRESSED": "#E4EBF3",
    "BUTTON_BG_DISABLED": "#F0F2F5",
    "BUTTON_TEXT_DISABLED": "#5A6472",
    "BUTTON_BORDER_HOVER": "#C3CDD8",
    "BUTTON_BORDER_PRESSED": "#B6C2CF",
    "INPUT_BG": "#FFFFFF",
    "INPUT_BG_READONLY": "#F7F9FB",
    "INPUT_BG_DISABLED": "#F0F2F5",
    "INPUT_TEXT_DISABLED": "#5A6472",
    "INPUT_BORDER_DISABLED": "#E1E6EC",
    "SPINBOX_BUTTON_BG": "#F0F4F9",
    "SPINBOX_BUTTON_HOVER": "#E4EBF3",
    "TABLE_BG": "#FFFFFF",
    "TABLE_ALT_BG": "#F7F9FB",
    "TABLE_GRIDLINE": "#E4E9EF",
    "TABLE_SELECTION_BG": "#D9E6F4",
    "TABLE_SELECTION_TEXT": "#1F2933",
    "TABLE_HEADER_BG": "#ECF0F5",
    "TABLE_HEADER_TEXT": "#505D6B",
    "LIST_ITEM_HOVER_BG": "#F0F4F9",
    "CHECKBOX_BORDER": "#C3CDD8",
    "CHECKBOX_BG": "#FFFFFF",
    "CHECKBOX_BG_DISABLED": "#F0F2F5",
    "PROGRESSBAR_BG": "#ECF0F5",
    "SCROLLBAR_HANDLE": "#C3CDD8",
    "SCROLLBAR_HANDLE_HOVER": "#AEB9C6",
    "TOOLTIP_BG": "#1F2933",
    "TOOLTIP_TEXT": "#FFFFFF",
    "SUCCESS": "#2E7D32",
    "WARNING": "#8B5200",
    "ERROR": "#C0392B",
    "REVIEW": "#6A1B9A",
}

DARK_PALETTE: dict[str, str] = {
    "PRIMARY": "#5BA3E0",
    "PRIMARY_HOVER": "#4A8FCC",
    "PRIMARY_PRESSED": "#3A6FA8",
    "ON_PRIMARY": "#0E1A24",
    "PRIMARY_DISABLED_BG": "#4A5F8A",
    "PRIMARY_DISABLED_TEXT": "#D8E6F4",
    "BACKGROUND": "#1A1D23",
    "SURFACE": "#242A33",
    "BORDER": "#3D4450",
    "TEXT": "#E8EAED",
    "MUTED_TEXT": "#9CA3AF",
    "SELECTION_BG": "#3A4F6F",
    "SELECTION_TEXT": "#E8EAED",
    "TAB_BG_INACTIVE": "#323A45",
    "TAB_BG_ACTIVE": "#242A33",
    "TAB_TEXT_INACTIVE": "#9CA3AF",
    "TAB_HOVER_BG": "#2F3640",
    "BUTTON_BG": "#2F3640",
    "BUTTON_BG_HOVER": "#3D4550",
    "BUTTON_BG_PRESSED": "#4A5361",
    "BUTTON_BG_DISABLED": "#1F2329",
    "BUTTON_TEXT_DISABLED": "#8B939E",
    "BUTTON_BORDER_HOVER": "#4A5361",
    "BUTTON_BORDER_PRESSED": "#5A6370",
    "INPUT_BG": "#2F3640",
    "INPUT_BG_READONLY": "#1F2329",
    "INPUT_BG_DISABLED": "#1A1D23",
    "INPUT_TEXT_DISABLED": "#8B939E",
    "INPUT_BORDER_DISABLED": "#2A3039",
    "SPINBOX_BUTTON_BG": "#323A45",
    "SPINBOX_BUTTON_HOVER": "#3D4550",
    "TABLE_BG": "#242A33",
    "TABLE_ALT_BG": "#2C3239",
    "TABLE_GRIDLINE": "#3D4450",
    "TABLE_SELECTION_BG": "#3A4F6F",
    "TABLE_SELECTION_TEXT": "#E8EAED",
    "TABLE_HEADER_BG": "#2F3640",
    "TABLE_HEADER_TEXT": "#9CA3AF",
    "LIST_ITEM_HOVER_BG": "#2F3640",
    "CHECKBOX_BORDER": "#5A6370",
    "CHECKBOX_BG": "#2F3640",
    "CHECKBOX_BG_DISABLED": "#1A1D23",
    "PROGRESSBAR_BG": "#2F3640",
    "SCROLLBAR_HANDLE": "#5A6370",
    "SCROLLBAR_HANDLE_HOVER": "#6B7280",
    "TOOLTIP_BG": "#2F3640",
    "TOOLTIP_TEXT": "#E8EAED",
    "SUCCESS": "#4CAF50",
    "WARNING": "#FFA726",
    "ERROR": "#EF5350",
    "REVIEW": "#BA68C8",
}

PALETTES = {"light": LIGHT_PALETTE, "dark": DARK_PALETTE}

# Backwards-compatible module-level constants (the light values are the source).
PRIMARY = LIGHT_PALETTE["PRIMARY"]
PRIMARY_HOVER = LIGHT_PALETTE["PRIMARY_HOVER"]
PRIMARY_PRESSED = LIGHT_PALETTE["PRIMARY_PRESSED"]
BACKGROUND = LIGHT_PALETTE["BACKGROUND"]
SURFACE = LIGHT_PALETTE["SURFACE"]
BORDER = LIGHT_PALETTE["BORDER"]
TEXT = LIGHT_PALETTE["TEXT"]
MUTED_TEXT = LIGHT_PALETTE["MUTED_TEXT"]
SUCCESS = LIGHT_PALETTE["SUCCESS"]
WARNING = LIGHT_PALETTE["WARNING"]
ERROR = LIGHT_PALETTE["ERROR"]
REVIEW = LIGHT_PALETTE["REVIEW"]

# Concrete per-platform families, used only when Qt cannot answer yet. Qt returns an
# EMPTY family from systemFont() before a QApplication exists, and this module is
# imported well before one is constructed — emitting font-family: "" into the style
# sheet would be worse than the hardcoded name it replaced.
_FALLBACK_FONT_FAMILY = {
    "win32": "Segoe UI",
}


def system_ui_font_family() -> str:
    """The platform's UI font family name.

    Segoe UI does not exist on most Linux installs, so hardcoding it made
    Qt substitute an arbitrary family there. Qt knows the real system UI font per
    platform, but only once a QApplication exists; call this at style-generation time
    (apply_theme) to get the true value, and accept the fallback before that.
    """
    try:
        family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    except Exception:  # pragma: no cover - defensive
        family = ""
    return family or _FALLBACK_FONT_FAMILY.get(sys.platform, "DejaVu Sans")


# Import-time best effort. apply_theme() re-resolves once the QApplication exists, so
# the shipped style sheet carries the real system font rather than this fallback.
BASE_FONT_FAMILY = system_ui_font_family()
BASE_FONT_POINT_SIZE = 10

# Status string -> accent hex, per mode.
# Each value clears WCAG-AA (4.5:1) against all three surfaces it can land on:
# BACKGROUND, SURFACE, and its own pill tint in _STATUS_PILL_BG. The pill tint is
# the binding constraint in light mode and SURFACE in dark mode. Five of these
# previously failed on their pill tint (light PASS 4.45, light FAIL 4.42, dark PASS
# 4.37, dark FAIL 4.07, dark REVIEW_REQUIRED 3.93); they were re-derived by shifting
# HLS lightness away from the background, preserving hue and saturation, until every
# pairing cleared 4.7:1 — headroom so rounding cannot drop one back under the line.
# test_theme.py recomputes the ratios, so a future palette edit that breaks one fails.
_STATUS_COLORS = {
    "light": {"PASS": "#2C7730", "WARNING": "#8B5200", "REVIEW_REQUIRED": "#6A1B9A", "FAIL": "#B83729"},
    "dark": {"PASS": "#55B559", "WARNING": "#FFA726", "REVIEW_REQUIRED": "#C37DCF", "FAIL": "#F16A67"},
}

# The palettes' semantic accents ARE the status colours. Deriving them here rather
# than repeating the literals keeps the environment-check cards (readiness_dialog,
# which reads PALETTES[mode]["SUCCESS"]) and the status pills on one definition;
# they previously drifted, so the Environment Check kept the pre-AA values.
for _mode, _palette in PALETTES.items():
    _palette["SUCCESS"] = _STATUS_COLORS[_mode]["PASS"]
    _palette["WARNING"] = _STATUS_COLORS[_mode]["WARNING"]
    _palette["ERROR"] = _STATUS_COLORS[_mode]["FAIL"]
    _palette["REVIEW"] = _STATUS_COLORS[_mode]["REVIEW_REQUIRED"]

# Re-derive the backwards-compatible module constants after the sync above.
SUCCESS = LIGHT_PALETTE["SUCCESS"]
WARNING = LIGHT_PALETTE["WARNING"]
ERROR = LIGHT_PALETTE["ERROR"]
REVIEW = LIGHT_PALETTE["REVIEW"]

# Light tint background per status for status pills, per mode. Public so callers
# that build their own tinted callouts (e.g. the reference-mode advisory banner)
# theme them from the same source as the pills instead of a hardcoded hex.
STATUS_PILL_BG = {
    "light": {"PASS": "#E6F2E6", "WARNING": "#FBEEDA", "REVIEW_REQUIRED": "#F1E5F6", "FAIL": "#F8E3E0"},
    "dark": {"PASS": "#1B3D1B", "WARNING": "#4D3A1A", "REVIEW_REQUIRED": "#3D1F4D", "FAIL": "#4D1A1A"},
}

# Image-viewer scene background per mode (a QGraphicsScene ignores widget QSS).
IMAGEVIEWER_BG = {"light": "#ECEFF3", "dark": "#34383F"}


# ---- Sub-control glyphs -----------------------------------------------------
# Styling QComboBox::drop-down / QSpinBox::up-button in a style sheet stops the
# Fusion style from painting its native arrow into that sub-control, leaving an
# empty square. Qt does not fall back, so the glyph has to be supplied as an
# image. The stroke colours come from the active palette rather than being
# frozen into a checked-in asset, so a palette edit cannot silently desync them.

_GLYPH_PATHS = {
    "chevron_down": "M2.75 4.6 L6 7.85 L9.25 4.6",
    "chevron_up": "M2.75 7.4 L6 4.15 L9.25 7.4",
}
_CHECK_PATH = "M2.6 6.3 L4.9 8.6 L9.4 3.7"

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
        '<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _glyph_dir() -> Path | None:
    """Writable directory for the generated glyphs, or None if none is available."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    root = Path(base) if base else Path(tempfile.gettempdir()) / "bulkseq-studio"
    target = root / "glyphs"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return target


def glyph_paths(mode: str) -> dict[str, str]:
    """Write the theme's sub-control glyphs and return {token: posix path}.

    Returns an empty mapping if no writable location exists, in which case the
    caller omits the image rules and Qt renders as it did before — degraded, but
    never a crash on a locked-down machine.
    """
    target = _glyph_dir()
    if target is None:
        return {}
    palette = PALETTES.get(mode, LIGHT_PALETTE)
    wanted = {
        "CHEVRON_DOWN": (_GLYPH_PATHS["chevron_down"], palette["MUTED_TEXT"], 1.6),
        "CHEVRON_DOWN_DISABLED": (_GLYPH_PATHS["chevron_down"], palette["INPUT_TEXT_DISABLED"], 1.6),
        "CHEVRON_UP": (_GLYPH_PATHS["chevron_up"], palette["MUTED_TEXT"], 1.6),
        "CHEVRON_UP_DISABLED": (_GLYPH_PATHS["chevron_up"], palette["INPUT_TEXT_DISABLED"], 1.6),
        "CHECK": (_CHECK_PATH, palette["ON_PRIMARY"], 2.0),
    }
    out: dict[str, str] = {}
    for token, (d, colour, width) in wanted.items():
        path = target / f"{token.lower()}_{mode}.svg"
        content = _SVG.format(d=d, colour=colour, width=width)
        try:
            # Rewrite only on change so a theme toggle does not churn the disk.
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                path.write_text(content, encoding="utf-8")
        except OSError:
            return {}
        out[token] = path.as_posix()
    return out


# Applied only when glyph_paths() succeeded; $TOKENs are absolute file paths.
_GLYPH_QSS = Template("""
QComboBox::down-arrow {
    image: url($CHEVRON_DOWN);
    width: 12px;
    height: 12px;
}
QComboBox::down-arrow:disabled { image: url($CHEVRON_DOWN_DISABLED); }

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url($CHEVRON_UP);
    width: 10px;
    height: 10px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url($CHEVRON_DOWN);
    width: 10px;
    height: 10px;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
    image: url($CHEVRON_UP_DISABLED);
}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
    image: url($CHEVRON_DOWN_DISABLED);
}

QCheckBox::indicator:checked { image: url($CHECK); }
""")


# Complete application style template. Literal { } braces are QSS; $TOKEN markers
# are substituted from the active palette by _generate_qss().
_QSS_TEMPLATE = Template("""
/* ---- Window / dialog surfaces ---- */
QMainWindow, QDialog {
    background-color: $BACKGROUND;
    color: $TEXT;
}

QWidget {
    color: $TEXT;
    font-family: "$FONT_FAMILY";
    font-size: 10pt;
}

/* ---- Labels ---- */
QLabel {
    color: $TEXT;
    background: transparent;
}

QLabel:disabled {
    color: $MUTED_TEXT;
}

/* ---- Tabs ---- */
QTabWidget::pane {
    border: 1px solid $BORDER;
    border-radius: 10px;
    background-color: $SURFACE;
    top: -1px;
}

QTabBar::tab {
    background-color: $TAB_BG_INACTIVE;
    color: $TAB_TEXT_INACTIVE;
    border: 1px solid $BORDER;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: $TAB_BG_ACTIVE;
    color: $TEXT;
    border-color: $BORDER;
}

QTabBar::tab:hover:!selected {
    background-color: $TAB_HOVER_BG;
    color: $TEXT;
}

QTabBar::tab:!selected {
    margin-top: 2px;
}

/* ---- Buttons ---- */
QPushButton {
    background-color: $BUTTON_BG;
    color: $TEXT;
    border: 1px solid $BORDER;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}

QPushButton:hover {
    background-color: $BUTTON_BG_HOVER;
    border-color: $BUTTON_BORDER_HOVER;
}

QPushButton:pressed {
    background-color: $BUTTON_BG_PRESSED;
    border-color: $BUTTON_BORDER_PRESSED;
}

QPushButton:disabled {
    background-color: $BUTTON_BG_DISABLED;
    color: $BUTTON_TEXT_DISABLED;
    border-color: $INPUT_BORDER_DISABLED;
}

QPushButton:focus {
    border-color: $PRIMARY;
}

/* Primary-action buttons: QPushButton[primary="true"] */
QPushButton[primary="true"] {
    background-color: $PRIMARY;
    color: $ON_PRIMARY;
    border: 1px solid $PRIMARY;
    font-weight: 600;
}

QPushButton[primary="true"]:hover {
    background-color: $PRIMARY_HOVER;
    border-color: $PRIMARY_HOVER;
}

QPushButton[primary="true"]:pressed {
    background-color: $PRIMARY_PRESSED;
    border-color: $PRIMARY_PRESSED;
}

QPushButton[primary="true"]:disabled {
    background-color: $PRIMARY_DISABLED_BG;
    color: $PRIMARY_DISABLED_TEXT;
    border-color: $PRIMARY_DISABLED_BG;
}

QPushButton[primary="true"]:focus {
    border-color: $PRIMARY_PRESSED;
}

/* ---- Tool buttons (e.g. theme toggle, info buttons) ---- */
QToolButton {
    color: $TEXT;
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px;
}

QToolButton:hover {
    background-color: $BUTTON_BG_HOVER;
}

/* ---- Text inputs and combo/spin ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: $INPUT_BG;
    color: $TEXT;
    border: 1px solid $BORDER;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: $SELECTION_BG;
    selection-color: $SELECTION_TEXT;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid $PRIMARY;
}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: $INPUT_BG_DISABLED;
    color: $INPUT_TEXT_DISABLED;
    border-color: $INPUT_BORDER_DISABLED;
}

QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {
    background-color: $INPUT_BG_READONLY;
    color: $TEXT;
}

/* ---- ComboBox subcontrols and popup ---- */
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid $BORDER;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}

/* The combo arrow is drawn by the Fusion style (set in apply_theme); overriding
   its image here produced an empty square on some Qt builds. */

QComboBox QAbstractItemView {
    background-color: $SURFACE;
    color: $TEXT;
    border: 1px solid $BORDER;
    border-radius: 6px;
    outline: none;
    selection-background-color: $SELECTION_BG;
    selection-color: $SELECTION_TEXT;
}

/* ---- SpinBox subcontrols ---- */
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid $BORDER;
    border-top-right-radius: 6px;
    background-color: $SPINBOX_BUTTON_BG;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid $BORDER;
    border-bottom-right-radius: 6px;
    background-color: $SPINBOX_BUTTON_BG;
}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: $SPINBOX_BUTTON_HOVER;
}

/* Spin arrows are left to the Fusion style (set in apply_theme) so they render
   as crisp native triangles rather than CSS-border boxes. */

/* ---- Group boxes (cards) ----
   Containers carry a larger radius than the controls inside them (10 vs 6), so a
   card reads as a surface holding controls rather than as one more control. */
QGroupBox {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: 10px;
    margin-top: 18px;
    padding: 18px 14px 14px 14px;
    font-size: 10.5pt;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 2px 8px;
    color: $TEXT;
    /* Surface background so the card's top border does not strike through the
       title text (clean notch instead of an overlapped line). */
    background-color: $SURFACE;
}

/* Card contents sit at the body size; only the card title is stepped up. */
QGroupBox > QWidget { font-size: 10pt; font-weight: 400; }

/* Secondary/explanatory text. Set QLabel.setProperty("hint", True) to use it. */
QLabel[hint="true"] {
    color: $MUTED_TEXT;
    font-size: 9pt;
}

/* ---- Tables ---- */
QTableWidget, QTableView {
    background-color: $TABLE_BG;
    alternate-background-color: $TABLE_ALT_BG;
    gridline-color: $TABLE_GRIDLINE;
    border: 1px solid $BORDER;
    border-radius: 10px;
    selection-background-color: $TABLE_SELECTION_BG;
    selection-color: $TABLE_SELECTION_TEXT;
    outline: none;
}

QTableWidget::item, QTableView::item {
    padding: 4px 6px;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: $TABLE_SELECTION_BG;
    color: $TABLE_SELECTION_TEXT;
}

QHeaderView {
    background-color: $TABLE_HEADER_BG;
    border: none;
}

QHeaderView::section {
    background-color: $TABLE_HEADER_BG;
    color: $TABLE_HEADER_TEXT;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid $BORDER;
    border-bottom: 1px solid $BORDER;
    font-weight: 600;
}

QHeaderView::section:last {
    border-right: none;
}

QTableCornerButton::section {
    background-color: $TABLE_HEADER_BG;
    border: none;
    border-right: 1px solid $BORDER;
    border-bottom: 1px solid $BORDER;
}

/* ---- List widgets ---- */
QListWidget {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: 10px;
    outline: none;
    padding: 2px;
}

QListWidget::item {
    padding: 4px 6px;
    border-radius: 4px;
}

QListWidget::item:selected {
    background-color: $SELECTION_BG;
    color: $SELECTION_TEXT;
}

QListWidget::item:hover:!selected {
    background-color: $LIST_ITEM_HOVER_BG;
}

/* ---- Checkboxes ---- */
QCheckBox {
    color: $TEXT;
    spacing: 7px;
    background: transparent;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $CHECKBOX_BORDER;
    border-radius: 4px;
    background-color: $CHECKBOX_BG;
}

QCheckBox::indicator:hover {
    border-color: $PRIMARY;
}

QCheckBox::indicator:checked {
    background-color: $PRIMARY;
    border-color: $PRIMARY;
}

QCheckBox::indicator:checked:hover {
    background-color: $PRIMARY_HOVER;
    border-color: $PRIMARY_HOVER;
}

QCheckBox::indicator:disabled {
    border-color: $INPUT_BORDER_DISABLED;
    background-color: $CHECKBOX_BG_DISABLED;
}

QCheckBox:disabled {
    color: $BUTTON_TEXT_DISABLED;
}

/* ---- Progress bar ---- */
QProgressBar {
    background-color: $PROGRESSBAR_BG;
    border: 1px solid $BORDER;
    border-radius: 6px;
    text-align: center;
    color: $TEXT;
    min-height: 16px;
}

QProgressBar::chunk {
    background-color: $PRIMARY;
    border-radius: 5px;
}

/* ---- Splitter handles ---- */
QSplitter::handle {
    background-color: $BORDER;
}

QSplitter::handle:hover {
    background-color: $PRIMARY;
}

/* ---- Scroll bars ---- */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: $SCROLLBAR_HANDLE;
    border-radius: 5px;
    min-height: 28px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background-color: $SCROLLBAR_HANDLE_HOVER;
}

QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background-color: $SCROLLBAR_HANDLE;
    border-radius: 5px;
    min-width: 28px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background-color: $SCROLLBAR_HANDLE_HOVER;
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

/* ---- Scroll areas ---- */
QScrollArea {
    background: transparent;
    border: none;
}

/* ---- Tooltips ---- */
QToolTip {
    background-color: $TOOLTIP_BG;
    color: $TOOLTIP_TEXT;
    border: none;
    padding: 4px 7px;
    border-radius: 4px;
}
""")


def _generate_qss(palette: dict[str, str], mode: str = "light") -> str:
    # .substitute (strict) raises KeyError on a missing token, so a typo fails
    # loudly at startup rather than shipping a malformed style sheet.
    # Resolved live: by the time a style sheet is generated the QApplication exists,
    # so this is the real system UI font rather than the import-time fallback.
    qss = _QSS_TEMPLATE.substitute({**palette, "FONT_FAMILY": system_ui_font_family()})
    glyphs = glyph_paths(mode)
    if glyphs:
        qss += _GLYPH_QSS.substitute(glyphs)
    return qss


def build_qpalette(p: dict[str, str]) -> QPalette:
    """Map the theme palette onto a Qt QPalette.

    Fusion draws many surfaces (item-view/graphics-view viewports, menus, native
    sub-controls, disabled states) from the QPalette, not the style sheet. Without
    this, a light QSS over an OS dark palette renders "half light, half dark".
    """
    def c(key: str) -> QColor:
        return QColor(p[key])

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, c("BACKGROUND"))
    pal.setColor(QPalette.ColorRole.WindowText, c("TEXT"))
    pal.setColor(QPalette.ColorRole.Base, c("INPUT_BG"))
    pal.setColor(QPalette.ColorRole.AlternateBase, c("TABLE_ALT_BG"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, c("TOOLTIP_BG"))
    pal.setColor(QPalette.ColorRole.ToolTipText, c("TOOLTIP_TEXT"))
    pal.setColor(QPalette.ColorRole.Text, c("TEXT"))
    pal.setColor(QPalette.ColorRole.Button, c("BUTTON_BG"))
    pal.setColor(QPalette.ColorRole.ButtonText, c("TEXT"))
    pal.setColor(QPalette.ColorRole.BrightText, c("SELECTION_TEXT"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c("MUTED_TEXT"))
    pal.setColor(QPalette.ColorRole.Highlight, c("SELECTION_BG"))
    pal.setColor(QPalette.ColorRole.HighlightedText, c("SELECTION_TEXT"))
    pal.setColor(QPalette.ColorRole.Link, c("PRIMARY"))
    pal.setColor(QPalette.ColorRole.LinkVisited, c("REVIEW"))
    # Disabled group so disabled states do not fight the QSS.
    for role, key in (
        (QPalette.ColorRole.Text, "MUTED_TEXT"),
        (QPalette.ColorRole.ButtonText, "BUTTON_TEXT_DISABLED"),
        (QPalette.ColorRole.WindowText, "MUTED_TEXT"),
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c(key))
    return pal


def apply_theme(app: QApplication, mode: str = "light") -> None:
    """Apply the BulkSeq Studio theme (light or dark): Fusion style, palette, QSS.

    Fusion renders combo/spin sub-control arrows as crisp native triangles and is
    consistent across platforms, avoiding the empty-square arrows that the native
    Windows style showed under a heavy style sheet. The QPalette is set as well as
    the QSS so palette-driven surfaces (graphics/item viewports, menus) match the
    theme instead of inheriting the OS palette. Re-call to switch themes live.
    """
    if mode not in PALETTES:
        mode = "light"
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    font = QFont(system_ui_font_family(), BASE_FONT_POINT_SIZE)
    app.setFont(font)
    app.setPalette(build_qpalette(PALETTES[mode]))
    app.setStyleSheet(_generate_qss(PALETTES[mode], mode))


def status_color(status: str, mode: str = "light") -> str:
    """Return the accent hex for a readiness status string in the active mode.

    Unknown statuses fall back to muted text so callers never KeyError.
    """
    return _STATUS_COLORS.get(mode, _STATUS_COLORS["light"]).get(status, PALETTES.get(mode, LIGHT_PALETTE)["MUTED_TEXT"])


def status_pill(status: str, text: str | None = None, mode: str = "light") -> QLabel:
    """Build a tinted, rounded status pill QLabel for the active mode."""
    label = QLabel(text if text is not None else status)
    label.setAlignment(Qt.AlignCenter)
    fg = status_color(status, mode)
    bg = STATUS_PILL_BG.get(mode, STATUS_PILL_BG["light"]).get(status, "#EDEFF2")
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
