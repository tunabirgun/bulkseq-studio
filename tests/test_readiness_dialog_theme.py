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

def test_stop_install_kills_the_whole_install_tree_not_just_the_relay(monkeypatch) -> None:
    # Terminating wsl.exe leaves the setup script running inside the distribution, holding the
    # lock for up to 30 minutes. Stop must go through the installer's tree-kill, carrying the
    # run tag that the launch exported.
    from app.core import setup_installer

    launched: dict[str, object] = {}

    class _FakeProcess:
        stdout = iter(())

        def poll(self):
            return None

        def wait(self):
            return 0

        def terminate(self):
            launched["terminated"] = True

    def _fake_launch(**kwargs):
        launched["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(readiness, "launch_wsl_bioenv_install", _fake_launch)
    stopped: list[dict] = []
    monkeypatch.setattr(
        setup_installer, "stop_bioenv_install",
        lambda process, run_tag=None, native=False, **kw: stopped.append(
            {"run_tag": run_tag, "native": native}))

    thread = readiness.WslBioenvInstallThread(profile="core")
    assert thread.run_tag and thread.run_tag.startswith(setup_installer.RUN_TAG_PREFIX)
    thread.run()
    assert launched["kwargs"]["run_tag"] == thread.run_tag
    thread.stop()
    assert stopped == [{"run_tag": thread.run_tag, "native": False}]


def test_readiness_summary_states_that_it_counts_requirement_groups(monkeypatch) -> None:
    # "4 of 4 ready" beside a red item in the details read as a contradiction. The count is over
    # requirement groups (cards); the text must say so.
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(readiness.ReadinessDialog, "refresh", lambda self: None)
    dialog = readiness.ReadinessDialog()
    dialog._update_summary()
    assert "requirement groups ready" in dialog.summary_label.text()
    assert dialog.summary_label.text().split(" of ")[1].startswith(str(len(dialog._active_cards)))
    dialog.close()


def _rearm_calls(monkeypatch, status: str) -> list[str]:
    """Run _on_check_done over the R/WSL gate items and record the QSettings keys it clears."""
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(readiness.ReadinessDialog, "refresh", lambda self: None)
    for name in ("_update_python_card", "_update_wsl_card", "_update_core_card",
                 "_update_r_card", "_update_summary"):
        monkeypatch.setattr(readiness.ReadinessDialog, name,
                            lambda self, *a, **k: None, raising=True)
    removed: list[str] = []

    class _FakeSettings:
        def remove(self, key: str) -> None:
            removed.append(key)

        def value(self, _key: str, default: object = None) -> object:
            return default

    monkeypatch.setattr(readiness, "QSettings", _FakeSettings)
    dialog = readiness.ReadinessDialog()
    dialog._on_check_done([
        readiness.ReadinessItem(name, status, "detail", "purpose")
        for name in ("WSL distribution", "WSL R packages", "R packages")
    ])
    dialog.close()
    return removed


def test_core_profile_warning_does_not_rearm_the_first_run_environment_prompt(monkeypatch) -> None:
    # A core-profile install reports the full-only R stack as WARNING by design
    # (readiness.FULL_ONLY_TOOLS), which is a supported steady state — re-arming on it would
    # reopen Check Environment on every launch.
    from app.core.readiness import FULL_ONLY_TOOLS

    assert "Rscript" in FULL_ONLY_TOOLS
    assert _rearm_calls(monkeypatch, "WARNING") == []


def test_a_broken_r_stack_still_rearms_the_first_run_environment_prompt(monkeypatch) -> None:
    # Negative control for the test above: a genuine failure must still clear the stamp.
    assert _rearm_calls(monkeypatch, "REVIEW_REQUIRED") == ["env_check_prompted_version"]
    assert _rearm_calls(monkeypatch, "FAIL") == ["env_check_prompted_version"]
