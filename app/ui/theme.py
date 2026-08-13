from __future__ import annotations

import tempfile
from pathlib import Path
from string import Template

import sys

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

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
    "CONTROL_BORDER": "#7D8996",
    "TEXT": "#1F2933",
    "MUTED_TEXT": "#4F5A67",
    "SELECTION_BG": "#2C6FB6",
    "SELECTION_TEXT": "#FFFFFF",
    "TAB_BG_INACTIVE": "#ECF0F5",
    "TAB_BG_ACTIVE": "#FFFFFF",
    "TAB_TEXT_INACTIVE": "#4F5A67",
    "TAB_HOVER_BG": "#F5F7FA",
    # Secondary actions use a deliberate blue-grey fill. Keeping this separate
    # from INPUT_BG prevents buttons from reading as empty text fields.
    "BUTTON_BG": "#E9EEF4",
    "BUTTON_BG_HOVER": "#DEE6EF",
    "BUTTON_BG_PRESSED": "#D1DCE7",
    "BUTTON_BG_DISABLED": "#EEF1F5",
    "BUTTON_TEXT_DISABLED": "#5A6472",
    "BUTTON_BORDER_HOVER": "#2C6FB6",
    "BUTTON_BORDER_PRESSED": "#1E4F86",
    "INPUT_BG": "#FFFFFF",
    "READONLY_BG": "#F1F4F8",
    "CODE_BG": "#F5F7FA",
    "OUTPUT_BORDER": "#7D8996",
    # Compatibility alias for older call sites; QSS uses READONLY_BG.
    "INPUT_BG_READONLY": "#F1F4F8",
    "INPUT_BG_DISABLED": "#F0F2F5",
    "INPUT_TEXT_DISABLED": "#5A6472",
    "INPUT_BORDER_DISABLED": "#E1E6EC",
    "SPINBOX_BUTTON_BG": "#F0F4F9",
    "SPINBOX_BUTTON_HOVER": "#E4EBF3",
    "TABLE_BG": "#FFFFFF",
    "TABLE_ALT_BG": "#F1F4F8",
    "TABLE_GRIDLINE": "#E4E9EF",
    "TABLE_SELECTION_BG": "#D9E6F4",
    "TABLE_SELECTION_TEXT": "#1F2933",
    "TABLE_HEADER_BG": "#ECF0F5",
    "TABLE_HEADER_TEXT": "#505D6B",
    "LIST_ITEM_HOVER_BG": "#F0F4F9",
    "CHECKBOX_BORDER": "#7D8996",
    "CHECKBOX_BG": "#FFFFFF",
    "CHECKBOX_BG_DISABLED": "#F0F2F5",
    "PROGRESSBAR_BG": "#ECF0F5",
    "SCROLLBAR_HANDLE": "#7D8996",
    "SCROLLBAR_HANDLE_HOVER": "#2C6FB6",
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
    "CONTROL_BORDER": "#778495",
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
    "BUTTON_BORDER_HOVER": "#5BA3E0",
    "BUTTON_BORDER_PRESSED": "#3A6FA8",
    "INPUT_BG": "#242A33",
    "READONLY_BG": "#2A3039",
    "CODE_BG": "#1A1D23",
    "OUTPUT_BORDER": "#778495",
    # Compatibility alias for older call sites; QSS uses READONLY_BG.
    "INPUT_BG_READONLY": "#2A3039",
    "INPUT_BG_DISABLED": "#1A1D23",
    "INPUT_TEXT_DISABLED": "#8B939E",
    "INPUT_BORDER_DISABLED": "#2A3039",
    "SPINBOX_BUTTON_BG": "#323A45",
    "SPINBOX_BUTTON_HOVER": "#3D4550",
    "TABLE_BG": "#242A33",
    "TABLE_ALT_BG": "#2A3039",
    "TABLE_GRIDLINE": "#3D4450",
    "TABLE_SELECTION_BG": "#3A4F6F",
    "TABLE_SELECTION_TEXT": "#E8EAED",
    "TABLE_HEADER_BG": "#2F3640",
    "TABLE_HEADER_TEXT": "#9CA3AF",
    "LIST_ITEM_HOVER_BG": "#2F3640",
    "CHECKBOX_BORDER": "#778495",
    "CHECKBOX_BG": "#242A33",
    "CHECKBOX_BG_DISABLED": "#1A1D23",
    "PROGRESSBAR_BG": "#2F3640",
    "SCROLLBAR_HANDLE": "#778495",
    "SCROLLBAR_HANDLE_HOVER": "#5BA3E0",
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
_DUAL_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
             '<path d="{d}" fill="none" stroke="{outer}" stroke-width="{outer_width}" '
             'stroke-linecap="round" stroke-linejoin="round"/>'
             '<path d="{d}" fill="none" stroke="{inner}" stroke-width="{inner_width}" '
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


def _static_glyph_paths() -> dict[str, str]:
    """Write palette-derived, high-contrast glyphs for the static QSS.

    A stylesheet image cannot refer to a QPalette role. Each glyph therefore
    combines an inner light-mode stroke with an outer dark-mode stroke: one is
    legible against either control background without changing the stylesheet on
    a theme switch. The colours are derived from the two public palettes rather
    than frozen literals, so palette changes remain the single source of truth.
    """
    target = _glyph_dir()
    if target is None:
        return {}
    wanted = {
        "CHEVRON_DOWN": (_GLYPH_PATHS["chevron_down"], DARK_PALETTE["MUTED_TEXT"], LIGHT_PALETTE["MUTED_TEXT"], 2.4, 1.25),
        "CHEVRON_DOWN_DISABLED": (_GLYPH_PATHS["chevron_down"], DARK_PALETTE["INPUT_TEXT_DISABLED"], LIGHT_PALETTE["INPUT_TEXT_DISABLED"], 2.4, 1.25),
        "CHEVRON_UP": (_GLYPH_PATHS["chevron_up"], DARK_PALETTE["MUTED_TEXT"], LIGHT_PALETTE["MUTED_TEXT"], 2.4, 1.25),
        "CHEVRON_UP_DISABLED": (_GLYPH_PATHS["chevron_up"], DARK_PALETTE["INPUT_TEXT_DISABLED"], LIGHT_PALETTE["INPUT_TEXT_DISABLED"], 2.4, 1.25),
        "CHECK": (_CHECK_PATH, LIGHT_PALETTE["ON_PRIMARY"], DARK_PALETTE["ON_PRIMARY"], 3.0, 1.4),
    }
    out: dict[str, str] = {}
    for token, (d, outer, inner, outer_width, inner_width) in wanted.items():
        path = target / f"{token.lower()}_static.svg"
        content = _DUAL_SVG.format(
            d=d, outer=outer, inner=inner, outer_width=outer_width, inner_width=inner_width,
        )
        try:
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
# are substituted once to palette(role) expressions by _generate_qss(). Qt caches
# those resolved roles while polishing the widgets, so a live theme switch repolishes
# the Fusion style without reparsing this large style sheet.
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

/* ---- Task-page heading, sections, and empty states ----
   Page introductions establish hierarchy with type and a divider, not another
   card. A bounded surface is opt-in through uiRole="section" or "emptyState". */
QFrame[uiRole="pageIntro"] {
    background: transparent;
    border: none;
    border-bottom: 1px solid $BORDER;
    border-radius: 0px;
}

QFrame[uiRole="pageIntro"] QLabel,
QFrame[uiRole="section"] QLabel,
QFrame[uiRole="emptyState"] QLabel {
    background: transparent;
    border: none;
}

QLabel[uiRole="pageTitle"] {
    color: $TEXT;
    font-size: 13pt;
    font-weight: 600;
}

QLabel[uiRole="pagePurpose"] {
    color: $MUTED_TEXT;
    font-size: 9.5pt;
}

QFrame[uiRole="section"] {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid $BORDER;
    border-radius: 0;
}

QFrame[uiRole="statusBanner"] {
    background-color: $READONLY_BG;
    border: 1px solid $BORDER;
    border-left: 3px solid $PRIMARY;
    border-radius: 6px;
}

QLabel[uiRole="statusBanner"] {
    background-color: $READONLY_BG;
    border: 1px solid $BORDER;
    border-left: 3px solid $PRIMARY;
    border-radius: 6px;
}

QLabel[uiRole="sectionTitle"] {
    color: $TEXT;
    font-size: 10.5pt;
    font-weight: 600;
}

QLabel[uiRole="sectionHint"] {
    color: $MUTED_TEXT;
    font-size: 9pt;
}

QFrame[uiRole="emptyState"] {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: 10px;
}

QLabel[uiRole="emptyTitle"] {
    color: $TEXT;
    font-size: 12pt;
    font-weight: 600;
}

QLabel[uiRole="emptyBody"] {
    color: $MUTED_TEXT;
    font-size: 9.5pt;
}

QLabel[uiRole="sectionLabel"] {
    color: $MUTED_TEXT;
    font-size: 9pt;
    font-weight: 600;
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

QTabBar::tab:focus {
    border: 2px solid $PRIMARY;
    border-bottom: none;
    padding: 6px 15px 7px 15px;
}

/* ---- Narrow results inspectors ----
   These are navigation tabs, not fields or card titles.  A transparent tab row
   with one accent underline keeps that distinction clear in both themes. */
QTabWidget[uiRole="inspectorTabs"]::pane {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: 8px;
    top: -1px;
}

QTabWidget[uiRole="inspectorTabs"] QTabBar::tab {
    background-color: transparent;
    color: $MUTED_TEXT;
    border: none;
    border-bottom: 2px solid $BORDER;
    border-radius: 0;
    padding: 8px 10px;
    margin: 0;
    font-weight: 500;
}

QTabWidget[uiRole="inspectorTabs"] QTabBar::tab:hover:!selected {
    background-color: $TAB_HOVER_BG;
    color: $TEXT;
}

QTabWidget[uiRole="inspectorTabs"] QTabBar::tab:selected {
    background-color: $SURFACE;
    color: $TEXT;
    border-bottom: 3px solid $PRIMARY;
    padding-bottom: 7px;
    font-weight: 600;
}

QTabWidget[uiRole="inspectorTabs"] QTabBar::tab:focus {
    background-color: $TAB_HOVER_BG;
    color: $PRIMARY;
    border-bottom: 3px solid $PRIMARY;
    padding-bottom: 7px;
}

/* ---- Buttons ---- */
QPushButton {
    background-color: $BUTTON_BG;
    color: $TEXT;
    border: 1px solid $CONTROL_BORDER;
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
    border: 2px solid $PRIMARY;
    padding: 5px 13px;
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
    border: 2px solid $ON_PRIMARY;
    padding: 5px 13px;
}

/* Explicit semantic roles keep secondary actions from becoming a row of
   identical outlined rectangles. These properties are opt-in at the call site. */
QPushButton[buttonRole="quiet"] {
    background: transparent;
    border-color: transparent;
}

QPushButton[buttonRole="quiet"]:hover {
    background-color: $BUTTON_BG_HOVER;
    border-color: $BUTTON_BORDER_HOVER;
}

QPushButton[buttonRole="quiet"]:pressed {
    background-color: $BUTTON_BG_PRESSED;
    border-color: $BUTTON_BORDER_PRESSED;
}

QPushButton[buttonRole="quiet"]:focus {
    background-color: $BUTTON_BG_HOVER;
    border: 2px solid $PRIMARY;
    padding: 5px 13px;
}

QPushButton[buttonRole="warning"],
QPushButton[buttonRole="danger"] {
    background-color: $CODE_BG;
    font-weight: 600;
}

QPushButton[buttonRole="warning"] {
    color: $WARNING;
    border-color: $WARNING;
}

QPushButton[buttonRole="danger"] {
    color: $ERROR;
    border-color: $ERROR;
}

QPushButton[buttonRole="warning"]:hover,
QPushButton[buttonRole="danger"]:hover {
    background-color: $SURFACE;
}

QPushButton[buttonRole="warning"]:pressed,
QPushButton[buttonRole="danger"]:pressed {
    background-color: $CODE_BG;
    border-color: $TEXT;
}

QPushButton[buttonRole="warning"]:focus,
QPushButton[buttonRole="danger"]:focus {
    border: 2px solid $PRIMARY;
    padding: 5px 13px;
}

QPushButton[buttonRole="quiet"]:disabled,
QPushButton[buttonRole="warning"]:disabled,
QPushButton[buttonRole="danger"]:disabled {
    background-color: $BUTTON_BG_DISABLED;
    color: $BUTTON_TEXT_DISABLED;
    border-color: $INPUT_BORDER_DISABLED;
}

/* ---- Tool buttons (e.g. theme toggle, info buttons) ---- */
QToolButton {
    color: $TEXT;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px;
}

QToolButton:hover {
    background-color: $BUTTON_BG_HOVER;
}

QToolButton:focus {
    background-color: $BUTTON_BG_HOVER;
    border: 2px solid $PRIMARY;
    padding: 3px;
}

/* ---- Text inputs and combo/spin ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: $INPUT_BG;
    color: $TEXT;
    border: 1px solid $CONTROL_BORDER;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: $SELECTION_BG;
    selection-color: $SELECTION_TEXT;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 2px solid $PRIMARY;
    padding: 4px 7px;
}

QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {
    background-color: $READONLY_BG;
    color: $TEXT;
    border-color: $OUTPUT_BORDER;
}

QLineEdit[outputRole="code"],
QTextEdit[outputRole="code"],
QPlainTextEdit[outputRole="code"],
QLineEdit[uiRole="codeOutput"],
QTextEdit[uiRole="codeOutput"],
QPlainTextEdit[uiRole="codeOutput"] {
    background-color: $CODE_BG;
    color: $TEXT;
    border-color: $OUTPUT_BORDER;
}

/* Disabled wins when a read-only output is also unavailable. */
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: $INPUT_BG_DISABLED;
    color: $INPUT_TEXT_DISABLED;
    border-color: $INPUT_BORDER_DISABLED;
}

/* ---- ComboBox subcontrols and popup ---- */
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid $CONTROL_BORDER;
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
    border-left: 1px solid $CONTROL_BORDER;
    border-top-right-radius: 6px;
    background-color: $SPINBOX_BUTTON_BG;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid $CONTROL_BORDER;
    border-bottom-right-radius: 6px;
    background-color: $SPINBOX_BUTTON_BG;
}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: $SPINBOX_BUTTON_HOVER;
}

/* Spin arrows are left to the Fusion style (set in apply_theme) so they render
   as crisp native triangles rather than CSS-border boxes. */

/* ---- Legacy group boxes ----
   Older pages still use QGroupBox for semantic grouping. During migration they
   remain flat: an internal title plus whitespace, never a notched card outline. */
QGroupBox {
    background: transparent;
    border: none;
    margin-top: 16px;
    padding: 12px 0px 0px 0px;
    font-size: 10.5pt;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px;
    padding: 0px 0px 4px 0px;
    color: $TEXT;
    background: transparent;
    border: none;
}

/* Group contents sit at the body size; only the internal title is stepped up. */
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

QTableWidget:focus, QTableView:focus {
    border: 2px solid $PRIMARY;
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

QListWidget:focus {
    border: 2px solid $PRIMARY;
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
    border: 2px solid transparent;
    border-radius: 4px;
    padding: 0 1px;
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

QCheckBox:focus {
    border: 2px solid $PRIMARY;
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

/* ---- Transient application status ---- */
QStatusBar {
    background-color: $BACKGROUND;
    color: $MUTED_TEXT;
    border-top: 1px solid $BORDER;
    padding: 2px 10px 3px 10px;
}

QStatusBar::item {
    border: none;
}

/* ---- Splitter handles ---- */
QSplitter {
    background-color: $BACKGROUND;
}

QSplitter::handle {
    background-color: $BORDER;
}

QSplitter::handle:hover {
    background-color: $PRIMARY;
}

QSplitter:focus::handle {
    background-color: $BORDER;
    border: 1px solid $PRIMARY;
}

/* ---- Sliders ---- */
QSlider::groove:horizontal {
    height: 6px;
    background-color: $PROGRESSBAR_BG;
    border: 1px solid $BORDER;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    background-color: $PRIMARY;
    border: 2px solid $PRIMARY;
    border-radius: 7px;
}

QSlider:focus::handle:horizontal {
    border: 2px solid $TEXT;
}

QSlider::groove:vertical {
    width: 6px;
    background-color: $PROGRESSBAR_BG;
    border: 1px solid $BORDER;
    border-radius: 3px;
}

QSlider::handle:vertical {
    height: 14px;
    margin: 0 -5px;
    background-color: $PRIMARY;
    border: 2px solid $PRIMARY;
    border-radius: 7px;
}

QSlider:focus::handle:vertical {
    border: 2px solid $TEXT;
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


# The role assignment intentionally derives every QSS colour from build_qpalette().
# Using ``palette(role)`` keeps one static stylesheet for both themes. Qt's style-sheet
# engine caches the resolved roles, so apply_theme() replaces the much smaller Fusion
# style object to repolish widgets after a palette change; setStyleSheet() would also
# reparse the complete stylesheet and costs about half a second on the main window.
_PALETTE_ROLE_QSS = {
    "PRIMARY": "palette(link)",
    "PRIMARY_HOVER": "palette(accent)",
    "PRIMARY_PRESSED": "palette(shadow)",
    # Qt's QSS role spelling is ``tooltip-*`` (unlike the QPalette enum's
    # ToolTipText name). ``tool-tip-text`` is silently ignored and falls back to
    # the ordinary Text role, which rendered dark text on the light primary blue
    # and light text on the dark primary blue.
    "ON_PRIMARY": "palette(tooltip-text)",
    "PRIMARY_DISABLED_BG": "palette(mid)",
    "PRIMARY_DISABLED_TEXT": "palette(placeholder-text)",
    "BACKGROUND": "palette(window)",
    "SURFACE": "palette(base)",
    "BORDER": "palette(mid)",
    "CONTROL_BORDER": "palette(midlight)",
    "TEXT": "palette(text)",
    "MUTED_TEXT": "palette(placeholder-text)",
    "SELECTION_BG": "palette(highlight)",
    "SELECTION_TEXT": "palette(highlighted-text)",
    "TAB_BG_INACTIVE": "palette(alternate-base)",
    "TAB_BG_ACTIVE": "palette(base)",
    "TAB_TEXT_INACTIVE": "palette(placeholder-text)",
    "TAB_HOVER_BG": "palette(light)",
    "BUTTON_BG": "palette(button)",
    "BUTTON_BG_HOVER": "palette(light)",
    "BUTTON_BG_PRESSED": "palette(dark)",
    "BUTTON_BG_DISABLED": "palette(button)",
    "BUTTON_TEXT_DISABLED": "palette(button-text)",
    "BUTTON_BORDER_HOVER": "palette(link)",
    "BUTTON_BORDER_PRESSED": "palette(shadow)",
    "INPUT_BG": "palette(base)",
    "READONLY_BG": "palette(alternate-base)",
    "CODE_BG": "palette(window)",
    "OUTPUT_BORDER": "palette(midlight)",
    "INPUT_BG_READONLY": "palette(alternate-base)",
    "INPUT_BG_DISABLED": "palette(base)",
    "INPUT_TEXT_DISABLED": "palette(text)",
    "INPUT_BORDER_DISABLED": "palette(mid)",
    "SPINBOX_BUTTON_BG": "palette(alternate-base)",
    "SPINBOX_BUTTON_HOVER": "palette(light)",
    "TABLE_BG": "palette(base)",
    "TABLE_ALT_BG": "palette(alternate-base)",
    "TABLE_GRIDLINE": "palette(mid)",
    "TABLE_SELECTION_BG": "palette(light)",
    "TABLE_SELECTION_TEXT": "palette(text)",
    "TABLE_HEADER_BG": "palette(alternate-base)",
    "TABLE_HEADER_TEXT": "palette(placeholder-text)",
    "LIST_ITEM_HOVER_BG": "palette(light)",
    "CHECKBOX_BORDER": "palette(midlight)",
    "CHECKBOX_BG": "palette(base)",
    "CHECKBOX_BG_DISABLED": "palette(button)",
    "PROGRESSBAR_BG": "palette(alternate-base)",
    "SCROLLBAR_HANDLE": "palette(midlight)",
    "SCROLLBAR_HANDLE_HOVER": "palette(link)",
    "TOOLTIP_BG": "palette(tooltip-base)",
    "TOOLTIP_TEXT": "palette(highlighted-text)",
    "SUCCESS": "palette(link)",
    "WARNING": "palette(link-visited)",
    "ERROR": "palette(bright-text)",
    "REVIEW": "palette(link-visited)",
}


def _generate_qss(palette: dict[str, str], mode: str = "light") -> str:
    """Build the palette-aware application stylesheet.

    ``palette`` and ``mode`` remain accepted for compatibility with existing
    callers. They are deliberately validated rather than interpolated: emitting
    a theme-specific QSS here would put live switching back on the slow path.
    The palette-derived dual-stroke glyphs stay visible in both modes without
    replacing this stylesheet; the generated per-mode glyph helper remains public
    for callers that use it outside this application stylesheet.
    """
    missing = set(_PALETTE_ROLE_QSS) - set(palette)
    if missing:
        raise KeyError(f"palette is missing QSS tokens: {sorted(missing)!r}")
    qss = _QSS_TEMPLATE.substitute({**_PALETTE_ROLE_QSS, "FONT_FAMILY": system_ui_font_family()})
    glyphs = _static_glyph_paths()
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
    pal.setColor(QPalette.ColorRole.Base, c("SURFACE"))
    pal.setColor(QPalette.ColorRole.AlternateBase, c("READONLY_BG"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, c("TOOLTIP_BG"))
    # ToolTipText is reserved for text placed on the primary action fill. The
    # tooltip selector itself uses HighlightedText, which is legible on the dark
    # tooltip surface in both modes.
    pal.setColor(QPalette.ColorRole.ToolTipText, c("ON_PRIMARY"))
    pal.setColor(QPalette.ColorRole.Text, c("TEXT"))
    pal.setColor(QPalette.ColorRole.Button, c("BUTTON_BG"))
    pal.setColor(QPalette.ColorRole.ButtonText, c("TEXT"))
    pal.setColor(QPalette.ColorRole.BrightText, c("ERROR"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c("MUTED_TEXT"))
    pal.setColor(QPalette.ColorRole.Highlight, c("SELECTION_BG"))
    pal.setColor(QPalette.ColorRole.HighlightedText, c("SELECTION_TEXT"))
    pal.setColor(QPalette.ColorRole.Link, c("PRIMARY"))
    pal.setColor(QPalette.ColorRole.LinkVisited, c("WARNING"))
    pal.setColor(QPalette.ColorRole.Light, c("BUTTON_BG_HOVER"))
    # Mid is a subtle container divider; Midlight is deliberately stronger and
    # reserved for interactive control boundaries that must remain discernible.
    pal.setColor(QPalette.ColorRole.Midlight, c("CONTROL_BORDER"))
    pal.setColor(QPalette.ColorRole.Dark, c("BUTTON_BG_PRESSED"))
    pal.setColor(QPalette.ColorRole.Mid, c("BORDER"))
    pal.setColor(QPalette.ColorRole.Shadow, c("PRIMARY_PRESSED"))
    pal.setColor(QPalette.ColorRole.Accent, c("PRIMARY_HOVER"))
    # Disabled group so disabled states do not fight the QSS.
    for role, key in (
        (QPalette.ColorRole.Base, "INPUT_BG_DISABLED"),
        (QPalette.ColorRole.Button, "BUTTON_BG_DISABLED"),
        (QPalette.ColorRole.Text, "MUTED_TEXT"),
        (QPalette.ColorRole.ButtonText, "BUTTON_TEXT_DISABLED"),
        (QPalette.ColorRole.WindowText, "MUTED_TEXT"),
        (QPalette.ColorRole.Mid, "INPUT_BORDER_DISABLED"),
        (QPalette.ColorRole.Link, "PRIMARY_DISABLED_BG"),
        (QPalette.ColorRole.HighlightedText, "PRIMARY_DISABLED_TEXT"),
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c(key))
    return pal


_STATIC_QSS_PROPERTY = "_bulkseq_static_qss"
_THEME_MODE_PROPERTY = "_bulkseq_theme_mode"


def _repolish_application(app: QApplication, target_palette: QPalette) -> None:
    """Refresh palette(role) brushes without reinstalling the global style.

    Replacing the Fusion style repolishes every widget through Qt's slow global
    path and takes more than a second once the full application is constructed.
    Re-polishing the QApplication through the existing style refreshes all cached
    palette-role brushes while preserving the one-time QSS parse.
    """
    try:
        style = app.style()
        style.unpolish(app)
        style.polish(app)
    except (AttributeError, RuntimeError, TypeError):
        # Minimal application seams used by tests may expose only palette calls.
        return
    # The global QSS can leave an inherited palette cached on otherwise plain
    # page/viewport widgets even after QApplication polish. Assigning the target
    # palette to existing widgets invalidates those few remaining brushes without
    # the multi-second cost of replacing Fusion.
    try:
        top_levels = [widget for widget in app.topLevelWidgets() if widget.isVisible()]
    except (AttributeError, RuntimeError):
        # Lightweight test seams may not expose the top-level API.
        try:
            widgets = list(app.allWidgets())
        except (AttributeError, RuntimeError):
            widgets = []
    else:
        # Closed windows can remain alive through Qt/Python signal cycles. Walking
        # QApplication.allWidgets() made every theme switch repolish all of those
        # stale trees and caused test sessions (and long-running use) to degrade
        # from milliseconds to minutes. Hidden pages of a visible window still
        # need the new palette, so traverse descendants from visible top levels.
        widgets = []
        for top_level in top_levels:
            widgets.append(top_level)
            widgets.extend(top_level.findChildren(QWidget))
    for widget in widgets:
        try:
            widget.setPalette(target_palette)
        except RuntimeError:
            continue


def apply_theme(app: QApplication, mode: str = "light") -> None:
    """Apply the BulkSeq Studio theme with a one-time QSS installation.

    Fusion renders combo/spin sub-control arrows as crisp native triangles and is
    consistent across platforms, avoiding the empty-square arrows that the native
    Windows style showed under a heavy style sheet. The QPalette is set as well as
    the QSS so palette-driven surfaces (graphics/item viewports, menus) match the
    theme instead of inheriting the OS palette. Re-call to switch themes live.
    """
    if mode not in PALETTES:
        mode = "light"
    first_install = not bool(app.property(_STATIC_QSS_PROPERTY))
    previous_mode = app.property(_THEME_MODE_PROPERTY)
    target_palette = build_qpalette(PALETTES[mode])
    if first_install:
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
        app.setFont(QFont(system_ui_font_family(), BASE_FONT_POINT_SIZE))
        # Install the target palette before the one-time QSS parse so every
        # palette(role) expression resolves against the requested mode.
        app.setPalette(target_palette)
        app.setStyleSheet(_generate_qss(PALETTES[mode], mode))
        app.setProperty(_STATIC_QSS_PROPERTY, True)
    elif previous_mode != mode:
        # palette(role) expressions are resolved when Qt polishes the stylesheet.
        # Seed the new colours, refresh the existing style without replacing it,
        # then reassert the target palette so a platform style cannot reinstall one
        # of its default roles during widget polish.
        app.setPalette(target_palette)
        _repolish_application(app, target_palette)
        app.setPalette(target_palette)
    else:
        app.setPalette(target_palette)
    app.setProperty(_THEME_MODE_PROPERTY, mode)


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
