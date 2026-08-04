from __future__ import annotations

import re
import sys

import pytest

from app.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    PALETTES,
    STATUS_PILL_BG,
    _generate_qss,
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


def test_both_palettes_define_the_same_tokens() -> None:
    # The QSS template substitutes strictly, so a token present in one palette and
    # missing from the other would raise KeyError only in that theme.
    assert set(LIGHT_PALETTE) == set(DARK_PALETTE)


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
def test_generated_qss_wires_every_missing_subcontrol_glyph(mode: str) -> None:
    qss = _generate_qss(PALETTES[mode], mode)
    for selector in ("QComboBox::down-arrow", "QSpinBox::up-arrow",
                     "QSpinBox::down-arrow", "QCheckBox::indicator:checked"):
        # A selector may appear more than once (the checked indicator sets its fill in
        # the base sheet and its glyph in the appended one); QSS cascades, so it is
        # enough that some block supplies the image.
        blocks = [m.group(1) for m in
                  re.finditer(re.escape(selector) + r"[^{]*\{([^}]*)\}", qss)]
        assert blocks, f"{selector} missing from the {mode} style sheet"
        assert any("image: url(" in b for b in blocks), \
            f"{selector} has no glyph image in {mode}"


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
