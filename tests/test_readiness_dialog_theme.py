from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import readiness_dialog as readiness  # noqa: E402
from app.ui import theme  # noqa: E402


def _style_snapshot(dialog: readiness.ReadinessDialog) -> dict[str, str]:
    return {
        "dialog": dialog.styleSheet(),
        "heading": dialog.heading_label.styleSheet(),
        "summary": dialog.summary_label.styleSheet(),
        "card": dialog.card_python.styleSheet(),
        "card_title": dialog.card_python.title_label.styleSheet(),
        "card_detail": dialog.card_python.detail_label.styleSheet(),
        "pill": dialog.card_python.pill.styleSheet(),
        "card_action": dialog.card_python.action_button.styleSheet(),
        "refresh": dialog.refresh_button.styleSheet(),
        "progress": dialog.check_progress.styleSheet(),
        "details": dialog.details_button.styleSheet(),
        "log": dialog.text.styleSheet(),
        "repair": dialog.repair_button.styleSheet(),
        "close": dialog.close_button.styleSheet(),
    }


def test_open_dialog_rethemes_light_dark_light_without_losing_state_or_handlers(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    app.setPalette(theme.build_qpalette(theme.LIGHT_PALETTE))
    # Construct the real widget tree without launching environment probes or a
    # background QThread. The theming contract is independent of probe results.
    monkeypatch.setattr(readiness.ReadinessDialog, "refresh", lambda self: None)
    monkeypatch.setattr(readiness, "_current_mode", lambda: "light")

    dialog = readiness.ReadinessDialog()
    activations: list[str] = []
    dialog.card_python.update_state(
        readiness.STATE_ACTION,
        "A preserved readiness detail.",
        action_label="Repair now",
        action_handler=lambda: activations.append("called"),
        action_enabled=True,
    )
    dialog.summary_label.setText("4 of 4 ready — setup complete")
    dialog._summary_complete = True
    dialog.text.setPlainText("A preserved setup log.")
    dialog.check_progress.setVisible(True)
    dialog.apply_theme("light")
    dialog.show()
    app.processEvents()

    light = _style_snapshot(dialog)
    preserved = {
        "summary": dialog.summary_label.text(),
        "detail": dialog.card_python.detail_label.text(),
        "pill": dialog.card_python.pill.text(),
        "action": dialog.card_python.action_button.text(),
        "log": dialog.text.toPlainText(),
        "action_visible": dialog.card_python.action_button.isVisible(),
        "action_enabled": dialog.card_python.action_button.isEnabled(),
        "progress_visible": dialog.check_progress.isVisible(),
    }

    dialog.apply_theme("dark")
    app.processEvents()
    dark = _style_snapshot(dialog)

    for name in light:
        assert dark[name] != light[name], f"{name} retained its light literal style"
    assert theme.DARK_PALETTE["BACKGROUND"] in dark["dialog"]
    assert theme.DARK_PALETTE["SURFACE"] in dark["card"]
    assert theme.DARK_PALETTE["PRIMARY"] in dark["card_action"]
    assert theme.DARK_PALETTE["PRIMARY"] in dark["progress"]
    assert theme.DARK_PALETTE["SURFACE"] in dark["log"]
    assert theme.DARK_PALETTE["SUCCESS"] in dark["summary"]
    assert preserved == {
        "summary": dialog.summary_label.text(),
        "detail": dialog.card_python.detail_label.text(),
        "pill": dialog.card_python.pill.text(),
        "action": dialog.card_python.action_button.text(),
        "log": dialog.text.toPlainText(),
        "action_visible": dialog.card_python.action_button.isVisible(),
        "action_enabled": dialog.card_python.action_button.isEnabled(),
        "progress_visible": dialog.check_progress.isVisible(),
    }

    dialog.apply_theme("light")
    app.processEvents()
    assert _style_snapshot(dialog) == light
    dialog.card_python.action_button.click()
    assert activations == ["called"]
    dialog.close()
