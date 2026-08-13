from __future__ import annotations

from collections import Counter
import re
import sys

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLineEdit, QMainWindow, QPushButton

from app.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    PALETTES,
    STATUS_PILL_BG,
    _STATIC_QSS_PROPERTY,
    _generate_qss,
    apply_theme,
    glyph_paths,
    status_color,
)


def _relative_luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = channel(r), channel(g), channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def _qss_block(qss: str, selector: str) -> str:
    matches = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
    assert matches, f"{selector} missing from QSS"
    # A selector can first appear as the last item in a combined rule and later
    # receive a role-specific override; the final block is the effective one.
    return matches[-1]


def test_both_palettes_define_the_same_tokens() -> None:
    # The QSS template substitutes strictly, so a token present in one palette and
    # missing from the other would raise KeyError only in that theme.
    assert set(LIGHT_PALETTE) == set(DARK_PALETTE)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_output_and_secondary_surfaces_are_intentionally_distinct(mode: str) -> None:
    palette = PALETTES[mode]
    assert palette["READONLY_BG"] == palette["INPUT_BG_READONLY"]
    assert palette["READONLY_BG"] not in {palette["SURFACE"], palette["INPUT_BG"]}
    assert palette["CODE_BG"] not in {
        palette["SURFACE"], palette["INPUT_BG"], palette["READONLY_BG"],
    }
    assert palette["BUTTON_BG"] != palette["INPUT_BG"]


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_button_text_and_semantic_roles_retain_text_contrast(mode: str) -> None:
    palette = PALETTES[mode]
    assert contrast_ratio(palette["TEXT"], palette["BUTTON_BG"]) >= 4.5
    for semantic in ("WARNING", "ERROR"):
        for background in (palette["CODE_BG"], palette["SURFACE"]):
            assert contrast_ratio(palette[semantic], background) >= 4.5
    for primary_state in ("PRIMARY", "PRIMARY_HOVER", "PRIMARY_PRESSED"):
        assert contrast_ratio(palette["ON_PRIMARY"], palette[primary_state]) >= 3.0


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_output_well_boundaries_have_non_text_contrast(mode: str) -> None:
    palette = PALETTES[mode]
    for background in (palette["READONLY_BG"], palette["CODE_BG"]):
        assert contrast_ratio(palette["OUTPUT_BORDER"], background) >= 3.0


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_interactive_control_boundaries_have_non_text_contrast(mode: str) -> None:
    """Required input/checkbox outlines meet the WCAG non-text 3:1 floor."""
    palette = PALETTES[mode]
    for background in (palette["SURFACE"], palette["BUTTON_BG"]):
        assert contrast_ratio(palette["CONTROL_BORDER"], background) >= 3.0


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_checkbox_and_scrollbar_boundaries_have_non_text_contrast(mode: str) -> None:
    palette = PALETTES[mode]
    for background in (palette["SURFACE"], palette["BACKGROUND"]):
        assert contrast_ratio(palette["CHECKBOX_BORDER"], background) >= 3.0
        assert contrast_ratio(palette["SCROLLBAR_HANDLE"], background) >= 3.0
        assert contrast_ratio(palette["SCROLLBAR_HANDLE_HOVER"], background) >= 3.0
    assert contrast_ratio(palette["PRIMARY"], palette["TAB_HOVER_BG"]) >= 4.5


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("status", ["PASS", "WARNING", "REVIEW_REQUIRED", "FAIL"])
def test_status_colours_meet_wcag_aa_on_their_own_background(mode: str, status: str) -> None:
    # Run status and sanity-check verdicts are the signals a user reads to decide
    # whether the pipeline succeeded; they must be legible in both themes.
    fg = status_color(status, mode)
    for bg in (PALETTES[mode]["BACKGROUND"], PALETTES[mode]["SURFACE"], STATUS_PILL_BG[mode][status]):
        assert contrast_ratio(fg, bg) >= 4.5, f"{status} {fg} on {bg} in {mode}"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_status_colour_differs_between_themes(mode: str) -> None:
    # A regression guard for the bug where every call site passed a literal
    # light-palette hex, so dark mode painted #2E7D32 on #1A1D23 (3.29:1).
    assert status_color("PASS", "light") != status_color("PASS", "dark")


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_subcontrol_glyphs_are_generated_and_themed(mode: str) -> None:
    # Styling ::drop-down suppresses Fusion's native arrow, so the glyph must be
    # supplied as an image or every combo box renders an empty square.
    paths = glyph_paths(mode)
    assert set(paths) == {
        "CHEVRON_DOWN", "CHEVRON_DOWN_DISABLED", "CHEVRON_UP", "CHEVRON_UP_DISABLED", "CHECK",
    }
    from pathlib import Path
    svg = Path(paths["CHEVRON_DOWN"]).read_text(encoding="utf-8")
    # Colour is derived from the live palette, not frozen into the asset.
    assert PALETTES[mode]["MUTED_TEXT"].lower() in svg.lower()
    check = Path(paths["CHECK"]).read_text(encoding="utf-8")
    assert PALETTES[mode]["ON_PRIMARY"].lower() in check.lower()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_generated_qss_wires_static_cross_theme_subcontrol_glyphs(mode: str) -> None:
    qss = _generate_qss(PALETTES[mode], mode)
    # The dual-stroke SVGs contain a palette-derived light and dark stroke, so
    # changing QApplication's palette does not need to replace the QSS just to
    # keep combo/spin/check glyphs visible.
    for selector in ("QComboBox::down-arrow", "QSpinBox::up-arrow",
                     "QSpinBox::down-arrow", "QCheckBox::indicator:checked"):
        blocks = [m.group(1) for m in re.finditer(re.escape(selector) + r"[^\{]*\{([^}]*)\}", qss)]
        assert blocks, f"{selector} missing from static QSS"
        assert any("image: url(" in block and "_static.svg" in block for block in blocks)


def test_generated_qss_uses_palette_roles_and_focus_treatments() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    assert "palette(window)" in qss
    assert LIGHT_PALETTE["BACKGROUND"] not in qss
    for selector in (
        "QToolButton:focus", "QCheckBox:focus", "QTabBar::tab:focus",
        "QListWidget:focus", "QTableWidget:focus", "QSplitter:focus::handle",
        "QSlider:focus::handle:horizontal", "QSlider:focus::handle:vertical",
    ):
        assert selector in qss


def test_groupboxes_are_flat_and_titles_have_no_opaque_notch() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    group = _qss_block(qss, "QGroupBox")
    title = _qss_block(qss, "QGroupBox::title")

    assert "background: transparent" in group
    assert "border: none" in group
    assert "border-radius" not in group
    assert "background-color" not in group
    assert "background: transparent" in title
    assert "background-color" not in title
    assert "border: none" in title


def test_page_intro_is_a_divided_heading_not_a_decorative_card() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    intro = _qss_block(qss, 'QFrame[uiRole="pageIntro"]')

    assert "background: transparent" in intro
    assert "border: none" in intro
    assert "border-bottom: 1px solid palette(mid)" in intro
    assert "border-left" not in intro
    assert "background-color" not in intro


def test_explicit_section_and_output_roles_have_component_styles() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    for selector in (
        'QFrame[uiRole="section"]',
        'QLabel[uiRole="sectionTitle"]',
        'QLabel[uiRole="sectionHint"]',
    ):
        assert _qss_block(qss, selector)
    assert 'QLineEdit[outputRole="code"]' in qss
    assert 'QLineEdit[uiRole="codeOutput"]' in qss
    assert "background-color: palette(window)" in qss
    readonly = re.search(
        r"QLineEdit:read-only,[^{]*\{([^}]*)\}", qss,
    )
    assert readonly is not None
    assert "background-color: palette(alternate-base)" in readonly.group(1)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_rendered_output_wells_differ_from_editable_inputs(mode: str) -> None:
    app = QApplication.instance() or QApplication([])
    app.setProperty(_STATIC_QSS_PROPERTY, False)
    app.setProperty("_bulkseq_theme_mode", None)
    app.setStyleSheet("")
    apply_theme(app, mode)

    editable = QLineEdit()
    readonly = QLineEdit()
    readonly.setReadOnly(True)
    code = QLineEdit()
    code.setReadOnly(True)
    code.setProperty("uiRole", "codeOutput")
    fields = (editable, readonly, code)
    for field in fields:
        field.setFixedSize(160, 32)
        field.show()
    app.processEvents()

    def pixels(field: QLineEdit) -> tuple[str, str]:
        image = field.grab().toImage()
        return (
            image.pixelColor(field.width() // 2, field.height() // 2).name(),
            image.pixelColor(field.width() // 2, 0).name(),
        )

    editable_pixels, readonly_pixels, code_pixels = map(pixels, fields)
    for field in fields:
        field.close()

    assert editable_pixels[0] == PALETTES[mode]["INPUT_BG"].lower()
    assert readonly_pixels[0] == PALETTES[mode]["READONLY_BG"].lower()
    assert code_pixels[0] == PALETTES[mode]["CODE_BG"].lower()
    assert len({editable_pixels[0], readonly_pixels[0], code_pixels[0]}) == 3
    assert readonly_pixels[1] == PALETTES[mode]["OUTPUT_BORDER"].lower()
    assert code_pixels[1] == PALETTES[mode]["OUTPUT_BORDER"].lower()


def test_semantic_button_roles_and_stable_two_pixel_focus_are_defined() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    quiet = _qss_block(qss, 'QPushButton[buttonRole="quiet"]')
    warning = _qss_block(qss, 'QPushButton[buttonRole="warning"]')
    danger = _qss_block(qss, 'QPushButton[buttonRole="danger"]')
    button_focus = _qss_block(qss, "QPushButton:focus")
    primary_focus = _qss_block(qss, 'QPushButton[primary="true"]:focus')

    assert "background: transparent" in quiet
    assert "color: palette(link-visited)" in warning
    assert "color: palette(bright-text)" in danger
    for block in (button_focus, primary_focus):
        assert "border: 2px solid" in block
        # One pixel is transferred from padding to border on focus, preserving
        # the control's outer box and avoiding a layout or label jump.
        assert "padding: 5px 13px" in block
    field_focus = re.search(r"QLineEdit:focus,[^{]*\{([^}]*)\}", qss)
    assert field_focus is not None
    assert "border: 2px solid palette(link)" in field_focus.group(1)
    assert "padding: 4px 7px" in field_focus.group(1)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_rendered_primary_button_uses_on_primary_text(mode: str) -> None:
    """Catch invalid QSS palette-role spellings that silently fall back to Text."""
    app = QApplication.instance() or QApplication([])
    app.setProperty(_STATIC_QSS_PROPERTY, False)
    app.setProperty("_bulkseq_theme_mode", None)
    app.setStyleSheet("")
    apply_theme(app, mode)
    button = QPushButton("Start Run")
    button.setProperty("primary", True)
    button.setFixedSize(140, 40)
    button.show()
    button.style().unpolish(button)
    button.style().polish(button)
    app.processEvents()

    image = button.grab().toImage()
    colours = Counter(
        image.pixelColor(x, y).name()
        for y in range(image.height())
        for x in range(image.width())
    )
    button.close()

    assert colours[PALETTES[mode]["ON_PRIMARY"].lower()] > 20
    assert colours[PALETTES[mode]["TEXT"].lower()] == 0


def test_inspector_navigation_uses_tabs_not_field_like_accordion_rows() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    pane = _qss_block(qss, 'QTabWidget[uiRole="inspectorTabs"]::pane')
    tab = _qss_block(qss, 'QTabWidget[uiRole="inspectorTabs"] QTabBar::tab')
    selected = _qss_block(qss, 'QTabWidget[uiRole="inspectorTabs"] QTabBar::tab:selected')
    focus = _qss_block(qss, 'QTabWidget[uiRole="inspectorTabs"] QTabBar::tab:focus')
    assert "background-color: palette(base)" in pane
    assert "border: 1px solid palette(mid)" in pane
    assert "background-color: transparent" in tab
    assert "border: none" in tab
    assert "border-bottom: 2px solid palette(mid)" in tab
    assert "border-bottom: 3px solid palette(link)" in selected
    assert "border-bottom: 3px solid palette(link)" in focus
    assert "background-color: palette(link);" not in selected
    assert "background-color: palette(light)" in focus
    assert "color: palette(link)" in focus


def test_section_and_status_roles_avoid_notched_groupbox_chrome() -> None:
    qss = _generate_qss(LIGHT_PALETTE, "light")
    section = _qss_block(qss, 'QFrame[uiRole="section"]')
    banner = _qss_block(qss, 'QFrame[uiRole="statusBanner"]')
    assert "background-color: transparent" in section
    assert "border-bottom: 1px solid palette(mid)" in section
    assert "border-radius: 0" in section
    assert "background-color: palette(alternate-base)" in banner
    assert "border-left: 3px solid palette(link)" in banner


class _RecordingWidget:
    def __init__(self) -> None:
        self.palette_calls: list[QPalette] = []

    def setPalette(self, palette: QPalette) -> None:
        self.palette_calls.append(palette)


class _RecordingApplication:
    """Minimal QApplication seam for a failure-capable QSS replacement gate."""

    def __init__(self) -> None:
        self.properties: dict[str, object] = {}
        self.stylesheet_calls: list[str] = []
        self.style_calls: list[str] = []
        self.palette_calls: list[QPalette] = []
        self.unpolished: list[object] = []
        self.polished: list[object] = []
        self.widgets = [_RecordingWidget(), _RecordingWidget()]

    def property(self, name: str) -> object | None:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:
        self.properties[name] = value

    def setStyle(self, style: str) -> None:
        self.style_calls.append(style)

    def setFont(self, _font: object) -> None:
        pass

    def setStyleSheet(self, stylesheet: str) -> None:
        self.stylesheet_calls.append(stylesheet)

    def setPalette(self, palette: QPalette) -> None:
        self.palette_calls.append(palette)

    def style(self) -> _RecordingApplication:
        return self

    def allWidgets(self) -> list[_RecordingWidget]:
        return self.widgets

    def unpolish(self, widget: object) -> None:
        self.unpolished.append(widget)

    def polish(self, widget: object) -> None:
        self.polished.append(widget)


def test_live_theme_switch_does_not_replace_static_stylesheet_and_changes_palette() -> None:
    app = _RecordingApplication()
    apply_theme(app, "light")  # type: ignore[arg-type]
    apply_theme(app, "dark")  # type: ignore[arg-type]

    # This deliberately fails if apply_theme returns to calling setStyleSheet on
    # every toggle; comparing stylesheet strings alone would miss that regression.
    assert len(app.stylesheet_calls) == 1
    assert app.style_calls == ["Fusion"]
    assert len(app.palette_calls) == 3
    assert app.unpolished == [app]
    assert app.polished == [app]
    assert all(len(widget.palette_calls) == 1 for widget in app.widgets)
    assert app.palette_calls[0].color(QPalette.ColorRole.Window) != app.palette_calls[-1].color(QPalette.ColorRole.Window)
    assert app.palette_calls[0].color(QPalette.ColorRole.Link) != app.palette_calls[-1].color(QPalette.ColorRole.Link)


def test_real_application_keeps_the_static_stylesheet_on_live_switch() -> None:
    app = QApplication.instance() or QApplication([])
    app.setProperty(_STATIC_QSS_PROPERTY, False)
    app.setStyleSheet("")
    apply_theme(app, "light")
    static_qss = app.styleSheet()
    light_window = app.palette().color(QPalette.ColorRole.Window)
    apply_theme(app, "dark")
    assert app.styleSheet() == static_qss
    assert app.palette().color(QPalette.ColorRole.Window) != light_window


def test_live_switch_visibly_repaints_the_main_surface() -> None:
    """A palette-only switch is fast but leaves cached QSS colours unchanged."""
    app = QApplication.instance() or QApplication([])
    app.setProperty(_STATIC_QSS_PROPERTY, False)
    app.setProperty("_bulkseq_theme_mode", None)
    app.setStyleSheet("")
    apply_theme(app, "light")
    window = QMainWindow()
    window.resize(80, 60)
    window.show()
    app.processEvents()
    light_pixel = window.grab().toImage().pixelColor(5, 5)

    apply_theme(app, "dark")
    app.processEvents()
    dark_pixel = window.grab().toImage().pixelColor(5, 5)
    window.close()

    assert light_pixel != dark_pixel
    assert light_pixel.lightness() > dark_pixel.lightness()


def test_live_switch_skips_closed_top_level_widget_trees() -> None:
    class CountingWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.explicit_palette_calls = 0

        def setPalette(self, palette: QPalette) -> None:  # noqa: N802 - Qt API
            self.explicit_palette_calls += 1
            super().setPalette(palette)

    app = QApplication.instance() or QApplication([])
    app.setProperty(_STATIC_QSS_PROPERTY, False)
    app.setProperty("_bulkseq_theme_mode", None)
    app.setStyleSheet("")
    apply_theme(app, "light")
    visible = CountingWindow()
    closed = CountingWindow()
    visible.show()
    closed.show()
    app.processEvents()
    closed.close()
    app.processEvents()
    visible.explicit_palette_calls = 0
    closed.explicit_palette_calls = 0

    apply_theme(app, "dark")
    app.processEvents()

    visible.close()
    assert visible.explicit_palette_calls >= 1
    assert closed.explicit_palette_calls == 0


def test_font_family_is_resolved_not_hardcoded() -> None:
    # "Segoe UI" does not exist on most Linux installs. The QSS font-family
    # rule overrides the QApplication font, so both must come from one resolved value
    # or the style sheet silently wins with a family the platform lacks.
    from app.ui.theme import BASE_FONT_FAMILY, system_ui_font_family

    # Never empty: Qt returns "" from systemFont() before a QApplication exists, and
    # this module is imported first, so an unguarded value ships font-family: "".
    assert BASE_FONT_FAMILY, "import-time font family must fall back, not be empty"
    resolved = system_ui_font_family()
    assert resolved, "resolved font family must never be empty"
    qss = _generate_qss(LIGHT_PALETTE, "light")
    assert f'font-family: "{resolved}"' in qss
    assert "Segoe UI" not in qss or sys.platform.startswith("win"), \
        "a Windows-only font family must not be hardcoded into the style sheet"


def test_qss_uses_no_css_only_properties() -> None:
    # Qt style sheets silently ignore these; a rule using one looks like styling
    # but renders nothing, which is how the empty combo arrow shipped.
    qss = _generate_qss(LIGHT_PALETTE, "light")
    for prop in ("transition:", "@keyframes", "box-shadow:", "transform:",
                 "backdrop-filter:", "var(--", "display: flex", "::before", "::after"):
        assert prop not in qss, f"Qt QSS does not support {prop!r}"
