from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from scripts.capture_gui_matrix import (
    _CAPTURE_IDENTITY_MAX_DELTA,
    _assert_visual_capture_sane,
    _header_identity_delta,
    _native_region_identity_delta,
)


def _solid_image(colour: str, width: int = 240, height: int = 160) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    return image


def test_capture_identity_gate_rejects_an_unrelated_foreground_frame() -> None:
    app_header = _solid_image("#F5F7FA")
    unrelated_window = _solid_image("#090909")

    assert _header_identity_delta(app_header, app_header) == 0.0
    assert _header_identity_delta(app_header, unrelated_window) > _CAPTURE_IDENTITY_MAX_DELTA


def test_native_region_gate_rejects_an_overlay_outside_the_web_canvas() -> None:
    expected = _solid_image("#F5F7FA")
    captured = expected.copy()
    web_canvas = QRect(20, 30, 120, 100)
    # A separately composed canvas is intentionally excluded.
    for y in range(web_canvas.top(), web_canvas.bottom() + 1):
        for x in range(web_canvas.left(), web_canvas.right() + 1):
            captured.setPixelColor(x, y, QColor("#202124"))
    assert _native_region_identity_delta(expected, captured, web_canvas) == 0.0

    # A foreign popup over the native inspector/footer must fail the same gate.
    for y in range(80, 150):
        for x in range(170, 235):
            captured.setPixelColor(x, y, QColor("#202124"))
    assert (
        _native_region_identity_delta(expected, captured, web_canvas)
        > _CAPTURE_IDENTITY_MAX_DELTA
    )


def test_visual_sanity_gate_rejects_the_near_black_capture_failure() -> None:
    with pytest.raises(RuntimeError, match="visually implausible"):
        _assert_visual_capture_sane("foreign-window.png", _solid_image("#090909"))


def test_visual_sanity_gate_accepts_a_structured_application_frame() -> None:
    image = _solid_image("#F5F7FA")
    for y in range(20, 80):
        for x in range(20, 200):
            image.setPixelColor(x, y, QColor("#2C6FB6"))

    statistics = _assert_visual_capture_sane("bulkseq.png", image)
    assert statistics["near_black_fraction"] == 0.0
    assert statistics["luminance_range"] > 8.0
