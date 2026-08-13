"""Drive bundled benchmark create, validation, and dry-run flows through the GUI.

This is local QA evidence, not application code. It deliberately uses the real
MainWindow fields, buttons, background fingerprint worker, and Snakemake runner.
Any unexpected dialog, stale/invalid fingerprint, failed check, or non-zero
dry-run exits the process without continuing to the next benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Callable


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# Use the native Windows compositor for reviewable text and control rendering.
# The off-screen plugin is functionally adequate but can emit tofu glyphs on
# Windows when DirectWrite cannot resolve the application font.
os.environ.setdefault(
    "QT_QPA_PLATFORM", "windows" if sys.platform.startswith("win") else "offscreen"
)
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import __version__ as PYSIDE_VERSION  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

# A Chromium process is irrelevant to Project, Pre-run validation, and Run
# Monitor, and makes deterministic off-screen teardown needlessly fragile. The
# real PPI integration has its own dedicated test; this harness still constructs
# the complete MainWindow and uses its ordinary static PPI fallback.
from app.ui import ppi_viewer  # noqa: E402

ppi_viewer.WEBENGINE_AVAILABLE = False

from app.constants import APP_NAME, APP_VERSION, WORKFLOW_VERSION  # noqa: E402
from app.core.benchmark_datasets import load_benchmark_catalog  # noqa: E402
from app.core.paths import wsl_recommended_workdir  # noqa: E402
from app.core.resources import recommend_profile, recommend_rule_threads  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import apply_theme  # noqa: E402


EVIDENCE_ROOT = Path(__file__).resolve().parent
PRESET_RESOURCE_PROFILES = frozenset({"low", "balanced", "high"})
RESOURCE_PROFILES = PRESET_RESOURCE_PROFILES | {"custom"}


class HarnessFailure(RuntimeError):
    pass


def utc_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pump(milliseconds: int = 20) -> None:
    QTest.qWait(milliseconds)
    QApplication.processEvents()


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    label: str,
) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                pump(30)
                return
        except Exception as exc:  # preserve the most useful failure at timeout
            last_error = exc
        pump(30)
    detail = f"; last predicate error: {last_error}" if last_error else ""
    raise HarnessFailure(f"Timed out after {timeout_s:.1f}s waiting for {label}{detail}")


def dialog_text(dialog: QDialog) -> str:
    labels = [label.text().strip() for label in dialog.findChildren(QLabel) if label.text().strip()]
    if isinstance(dialog, QMessageBox) and dialog.text().strip():
        labels.insert(0, dialog.text().strip())
    return " | ".join(dict.fromkeys(labels))


class DialogGuard(QObject):
    """Permit only the one expected benchmark picker; reject everything else."""

    def __init__(self) -> None:
        super().__init__()
        self.expected_index: int | None = None
        self.expected_screenshot: Path | None = None
        self.expected_seen = False
        self.unexpected: list[dict[str, str]] = []

    def expect_picker(self, index: int, screenshot: Path) -> None:
        if self.expected_index is not None:
            raise HarnessFailure("A benchmark picker expectation is already active")
        self.expected_index = index
        self.expected_screenshot = screenshot
        self.expected_seen = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.Show or not isinstance(watched, QDialog):
            return False
        picker_prompt = any(
            label.text().strip() == "Choose a benchmark dataset:"
            for label in watched.findChildren(QLabel)
        )
        if (
            isinstance(watched, QInputDialog)
            and self.expected_index is not None
            and watched.windowTitle() == APP_NAME
            and picker_prompt
        ):
            self.expected_seen = True
            QTimer.singleShot(0, lambda dialog=watched: self._accept_picker(dialog))
            return False
        payload = {
            "class": type(watched).__name__,
            "title": watched.windowTitle(),
            "text": dialog_text(watched),
        }
        self.unexpected.append(payload)
        QTimer.singleShot(0, watched.reject)
        return False

    def _accept_picker(self, dialog: QInputDialog) -> None:
        index = self.expected_index
        screenshot = self.expected_screenshot
        if index is None or screenshot is None:
            self.unexpected.append({
                "class": type(dialog).__name__,
                "title": dialog.windowTitle(),
                "text": "Picker appeared without an active expectation",
            })
            dialog.reject()
            return
        combo = dialog.findChild(QComboBox)
        buttons = dialog.findChild(QDialogButtonBox)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok) if buttons is not None else None
        if combo is None or ok is None or not (0 <= index < combo.count()):
            self.unexpected.append({
                "class": type(dialog).__name__,
                "title": dialog.windowTitle(),
                "text": f"Picker controls unavailable for index {index}",
            })
            dialog.reject()
            return
        combo.setCurrentIndex(index)
        pump(30)
        save_widget_screenshot(dialog, screenshot)
        QTest.mouseClick(ok, Qt.MouseButton.LeftButton)
        self.expected_index = None
        self.expected_screenshot = None

    def assert_clean(self, context: str) -> None:
        if self.unexpected:
            raise HarnessFailure(
                f"Unexpected modal dialog during {context}: "
                + json.dumps(self.unexpected[-1], ensure_ascii=False)
            )


def save_widget_screenshot(widget, path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    pump(80)
    # Force a complete settled repaint before QWidget.grab(). A background-worker
    # completion can invalidate child controls without repainting every rail item
    # in the same event turn, producing a misleading partially blank QA frame.
    widget.repaint()
    QApplication.processEvents()
    pixmap = widget.grab()
    if pixmap.isNull() or pixmap.width() < 100 or pixmap.height() < 80:
        raise HarnessFailure(
            f"Invalid screenshot surface for {path.name}: {pixmap.width()}x{pixmap.height()}"
        )
    if not pixmap.save(str(path), "PNG") or not path.exists() or path.stat().st_size < 1000:
        raise HarnessFailure(f"Could not save a non-trivial screenshot to {path}")
    return {
        "path": str(path),
        "width": pixmap.width(),
        "height": pixmap.height(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def find_button(window: MainWindow, text: str) -> QPushButton:
    matches = [button for button in window.findChildren(QPushButton) if button.text() == text]
    if len(matches) != 1:
        raise HarnessFailure(f"Expected exactly one visible control labelled {text!r}, found {len(matches)}")
    return matches[0]


def validate_resource_request(
    profile: str,
    threads: int | None,
    memory_gb: int | None,
) -> None:
    """Fail closed unless CLI resource arguments form one unambiguous request."""
    if profile not in RESOURCE_PROFILES:
        raise HarnessFailure(f"Unsupported resource profile: {profile!r}")
    if profile == "custom":
        if threads is None or memory_gb is None:
            raise HarnessFailure(
                "The custom resource profile requires explicit --resource-threads "
                "and --resource-memory-gb values"
            )
        if threads <= 0 or memory_gb <= 0:
            raise HarnessFailure(
                "Custom --resource-threads and --resource-memory-gb values must be positive"
            )
    elif threads is not None or memory_gb is not None:
        raise HarnessFailure(
            "--resource-threads and --resource-memory-gb are valid only with "
            "--resource-profile custom"
        )


def qtest_select_combo_text(combo: QComboBox, text: str, *, label: str) -> None:
    """Select a combo entry through keyboard events and prove the visible result."""
    index = combo.findText(text, Qt.MatchFlag.MatchExactly)
    if index < 0:
        raise HarnessFailure(f"{label} does not offer {text!r}")
    combo.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(combo, Qt.Key.Key_Home)
    for _ in range(index):
        QTest.keyClick(combo, Qt.Key.Key_Down)
    pump(40)
    if combo.currentText() != text:
        raise HarnessFailure(
            f"Could not select {text!r} through the {label}: {combo.currentText()!r}"
        )


def qtest_set_spin_value(spinbox, value: int, *, label: str) -> None:
    """Enter an integer through the spinbox editor rather than mutating its model."""
    editor = spinbox.lineEdit()
    editor.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(
        editor,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier,
    )
    QTest.keyClicks(editor, str(value))
    QTest.keyClick(editor, Qt.Key.Key_Return)
    pump(40)
    if spinbox.value() != value:
        raise HarnessFailure(
            f"Could not enter {value} through the {label}: {spinbox.value()}"
        )


def _resource_values(resources) -> tuple[str, int, int]:
    return (
        str(resources.profile),
        int(resources.total_threads),
        int(resources.total_memory_gb),
    )


def _assert_resource_values(
    source: str,
    observed: tuple[str, int, int],
    expected: tuple[str, int, int],
) -> None:
    if observed != expected:
        raise HarnessFailure(
            f"{source} resources do not match exactly: "
            f"profile={observed[0]!r}/{expected[0]!r}, "
            f"threads={observed[1]}/{expected[1]}, "
            f"memory_gb={observed[2]}/{expected[2]}"
        )


def assert_resource_state(
    window: MainWindow,
    project_root: Path,
    *,
    expected_profile: str,
    expected_threads: int,
    expected_memory_gb: int,
) -> None:
    """Check the visible controls, live model, and a fresh config.yaml reload."""
    expected = (expected_profile, expected_threads, expected_memory_gb)
    _assert_resource_values(
        "GUI",
        (window.profile.currentText(), window.cores.value(), window.ram.value()),
        expected,
    )
    if window.config is None:
        raise HarnessFailure("Project config disappeared while saving resources")
    _assert_resource_values("In-memory config", _resource_values(window.config.resources), expected)
    persisted_config = window.manager.load_config(project_root)
    _assert_resource_values(
        "Freshly reloaded disk config", _resource_values(persisted_config.resources), expected
    )
    expected_rule_threads = recommend_rule_threads(expected_threads)
    for source, observed in (
        ("In-memory config", window.config.rule_threads.model_dump()),
        ("Freshly reloaded disk config", persisted_config.rule_threads.model_dump()),
    ):
        mismatches = {
            name: (observed.get(name), expected_value)
            for name, expected_value in expected_rule_threads.items()
            if observed.get(name) != expected_value
        }
        if mismatches:
            raise HarnessFailure(
                f"{source} per-rule threads do not match the derived CPU allocation: "
                f"{mismatches}"
            )


def configure_and_verify_resources(
    window: MainWindow,
    project_root: Path,
    *,
    resource_profile: str,
    resource_threads: int | None = None,
    resource_memory_gb: int | None = None,
) -> dict[str, object]:
    """Exercise resource controls and return exact request plus host/WSL evidence."""
    validate_resource_request(resource_profile, resource_threads, resource_memory_gb)
    window.tabs.setCurrentIndex(5)
    pump(100)
    qtest_select_combo_text(window.profile, resource_profile, label="resource profile control")

    detect_button = find_button(window, "Detect and Recommend")
    if not detect_button.isVisible() or not detect_button.isEnabled():
        raise HarnessFailure("Detect and Recommend is not visible and enabled")
    QTest.mouseClick(detect_button, Qt.MouseButton.LeftButton)
    wait_until(
        lambda: not (
            getattr(window, "_detect_worker", None)
            and window._detect_worker.isRunning()
        ),
        timeout_s=60,
        label="resource detection",
    )
    if getattr(window, "_last_system", None) is None:
        raise HarnessFailure("Resource detection completed without a system profile")
    detected_system = window._last_system

    if resource_profile in PRESET_RESOURCE_PROFILES:
        expected_resources = recommend_profile(detected_system, resource_profile)
        expected_threads = int(expected_resources["total_threads"])
        expected_memory_gb = int(expected_resources["total_memory_gb"])
        if (
            window.profile.currentText() != resource_profile
            or window.cores.value() != expected_threads
            or window.ram.value() != expected_memory_gb
        ):
            raise HarnessFailure(
                "Detected GUI resources disagree with the requested live recommendation: "
                f"profile={window.profile.currentText()!r}/{resource_profile!r}, "
                f"threads={window.cores.value()}/{expected_threads}, "
                f"memory_gb={window.ram.value()}/{expected_memory_gb}"
            )
        selection_basis = "live_recommendation"
    else:
        assert resource_threads is not None and resource_memory_gb is not None
        expected_threads = resource_threads
        expected_memory_gb = resource_memory_gb
        qtest_set_spin_value(window.cores, expected_threads, label="CPU workers control")
        qtest_set_spin_value(window.ram, expected_memory_gb, label="memory control")
        selection_basis = "explicit_custom"

    _assert_resource_values(
        "GUI before Save Resources",
        (window.profile.currentText(), window.cores.value(), window.ram.value()),
        (resource_profile, expected_threads, expected_memory_gb),
    )
    save_resources = window.save_resources_button
    if not save_resources.isVisible() or not save_resources.isEnabled():
        raise HarnessFailure("Save Resources is not visible and enabled")
    QTest.mouseClick(save_resources, Qt.MouseButton.LeftButton)
    assert_resource_state(
        window,
        project_root,
        expected_profile=resource_profile,
        expected_threads=expected_threads,
        expected_memory_gb=expected_memory_gb,
    )

    derived_rule_threads = recommend_rule_threads(expected_threads)
    return {
        "requested_profile": resource_profile,
        "requested_total_threads": resource_threads,
        "requested_total_memory_gb": resource_memory_gb,
        "profile": resource_profile,
        "total_threads": expected_threads,
        "total_memory_gb": expected_memory_gb,
        "rule_threads": derived_rule_threads,
        "selection_basis": selection_basis,
        "host_physical_cores": int(getattr(detected_system, "physical_cores", 0) or 0),
        "host_logical_threads": int(getattr(detected_system, "logical_threads", 0) or 0),
        "host_total_ram_gb": float(getattr(detected_system, "total_ram_gb", 0.0) or 0.0),
        "wsl_physical_cores": int(
            getattr(detected_system, "wsl_physical_cores", 0) or 0
        ),
        "wsl_logical_cpus": int(getattr(detected_system, "wsl_cpus", 0) or 0),
        "wsl_ram_gb": float(getattr(detected_system, "wsl_ram_gb", 0.0) or 0.0),
    }


def file_manifest(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            ".snakemake" in relative.parts
            or "__pycache__" in relative.parts
            or path.suffix.casefold() in {".pyc", ".pyo"}
        ):
            continue
        try:
            if not path.is_file():
                continue
            files.append({
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
        except OSError as exc:
            raise HarnessFailure(
                f"Could not fingerprint stable project file {relative.as_posix()!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return files


def source_manifest() -> list[dict[str, object]]:
    paths = [
        REPO / "app" / "ui" / "main_window.py",
        REPO / "app" / "ui" / "task_navigator.py",
        REPO / "app" / "ui" / "theme.py",
        REPO / "app" / "core" / "preflight.py",
        REPO / "app" / "core" / "benchmark_datasets.py",
        REPO / "app" / "core" / "snakemake_runner.py",
        REPO / "app" / "data" / "benchmark_datasets.yaml",
        Path(__file__).resolve(),
    ]
    return [
        {
            "path": path.relative_to(REPO).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in paths
    ]


def create_window(app: QApplication) -> MainWindow:
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    pump(250)
    wait_until(
        lambda: not (
            getattr(window, "_wsl_autodetect_worker", None)
            and window._wsl_autodetect_worker.isRunning()
        ),
        timeout_s=30,
        label="startup WSL work-directory probe",
    )
    apply_theme(app, mode="light")
    pump(100)
    return window


def exercise_benchmark(
    window: MainWindow,
    guard: DialogGuard,
    benchmark: dict,
    benchmark_index: int,
    project_workdir: Path,
    evidence_dir: Path,
    ordinal: int,
    *,
    inject_fingerprint_change: bool = False,
    configure_resources: bool = False,
    resource_profile: str = "balanced",
    resource_threads: int | None = None,
    resource_memory_gb: int | None = None,
    resource_evidence_callback: Callable[[dict[str, object]], None] | None = None,
    project_seed_callback: Callable[[Path], dict[str, object]] | None = None,
    validation_timeout_s: float = 300.0,
) -> dict[str, object]:
    benchmark_id = str(benchmark["id"])
    project_name = f"guiqa_{ordinal:02d}_{benchmark_id}"
    expected_root = (project_workdir / project_name).resolve()
    if expected_root.exists():
        raise HarnessFailure(f"Refusing to overwrite pre-existing benchmark project: {expected_root}")
    stage_times: dict[str, float] = {}
    screenshots: list[dict[str, object]] = []
    resource_evidence: dict[str, object] | None = None
    project_seed_evidence: dict[str, object] | None = None
    started = time.monotonic()

    print(json.dumps({"event": "benchmark_start", "id": benchmark_id, "project": str(expected_root)}), flush=True)

    window.tabs.setCurrentIndex(0)
    pump(100)
    window.workdir.setText(str(project_workdir))
    window.project_name.setFocus(Qt.FocusReason.OtherFocusReason)
    window.project_name.selectAll()
    QTest.keyClicks(window.project_name, project_name)
    if window.project_name.text() != project_name or window.workdir.text() != str(project_workdir):
        raise HarnessFailure("Project fields did not retain the requested name and WSL working directory")

    picker_path = evidence_dir / f"{ordinal:02d}_{benchmark_id}_benchmark-picker.png"
    guard.expect_picker(benchmark_index, picker_path)
    create_button = find_button(window, "Create Benchmark Project")
    if not create_button.isVisible() or not create_button.isEnabled():
        raise HarnessFailure("Create Benchmark Project is not visible and enabled")
    create_start = time.monotonic()
    QTest.mouseClick(create_button, Qt.MouseButton.LeftButton)
    stage_times["create_seconds"] = time.monotonic() - create_start
    guard.assert_clean(f"{benchmark_id} project creation")
    if not guard.expected_seen or guard.expected_index is not None:
        raise HarnessFailure("The visible benchmark picker was not successfully exercised")
    if window.project_root is None or window.project_root.resolve() != expected_root:
        raise HarnessFailure(
            f"GUI created/opened the wrong project: expected {expected_root}, got {window.project_root}"
        )
    if "Created benchmark project:" not in window.project_status.toPlainText():
        raise HarnessFailure(f"Project status did not confirm creation: {window.project_status.toPlainText()}")
    screenshots.append(save_widget_screenshot(
        window, evidence_dir / f"{ordinal:02d}_{benchmark_id}_project-created.png"))

    if configure_resources:
        resource_start = time.monotonic()
        resource_evidence = configure_and_verify_resources(
            window,
            expected_root,
            resource_profile=resource_profile,
            resource_threads=resource_threads,
            resource_memory_gb=resource_memory_gb,
        )
        if resource_evidence_callback is not None:
            resource_evidence_callback(resource_evidence)
        stage_times["resource_detection_seconds"] = time.monotonic() - resource_start
        screenshots.append(save_widget_screenshot(
            window, evidence_dir / f"{ordinal:02d}_{benchmark_id}_resources-saved.png"))

    if project_seed_callback is not None:
        project_seed_evidence = project_seed_callback(expected_root)

    wait_until(
        lambda: not (
            getattr(window, "_phase_refresh_worker", None)
            and window._phase_refresh_worker.isRunning()
        ),
        timeout_s=60,
        label=f"{benchmark_id} saved-check refresh",
    )
    window.tabs.setCurrentIndex(7)
    pump(100)
    window.sanity_run_button.setFocus(Qt.FocusReason.OtherFocusReason)
    screenshots.append(save_widget_screenshot(
        window, evidence_dir / f"{ordinal:02d}_{benchmark_id}_validation-before.png"))
    if not window.sanity_run_button.isVisible() or not window.sanity_run_button.isEnabled():
        raise HarnessFailure("Validate current run inputs is not visible and enabled")
    validation_start = time.monotonic()
    QTest.mouseClick(window.sanity_run_button, Qt.MouseButton.LeftButton)
    wait_until(
        lambda: (
            getattr(window, "_sanity_worker", None) is None
            and not window.sanity_busy.isVisible()
        ),
        timeout_s=validation_timeout_s,
        label=f"{benchmark_id} GUI pre-run validation worker",
    )
    stage_times["validation_seconds"] = time.monotonic() - validation_start
    guard.assert_clean(f"{benchmark_id} pre-run validation")
    check_path = expected_root / "checks" / "01_input_validation.json"
    if not check_path.exists():
        raise HarnessFailure("GUI validation did not write checks/01_input_validation.json")
    check_payload = json.loads(check_path.read_text(encoding="utf-8"))
    check_status = check_payload.get("status")
    if check_status not in {"PASS", "WARNING", "REVIEW_REQUIRED"}:
        raise HarnessFailure(f"GUI validation status is blocking: {check_status!r}")
    fingerprint = check_payload.get("preflight_fingerprint")
    if not isinstance(fingerprint, dict) or len(str(fingerprint.get("value", ""))) != 64:
        raise HarnessFailure("GUI validation did not persist a complete SHA-256 preflight fingerprint")
    if inject_fingerprint_change:
        config_path = expected_root / "config" / "config.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\n# QA-only fingerprint invalidation\n",
            encoding="utf-8",
        )
    fingerprint_value = str(fingerprint.get("value", ""))
    if len(fingerprint_value) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint_value.casefold()
    ):
        raise HarnessFailure("GUI validation persisted a malformed fingerprint value")
    if not window.sanity_go_run.isVisible() or not window.sanity_go_run.isEnabled():
        raise HarnessFailure(
            "GUI fingerprint worker finished without exposing the validated Continue action: "
            + window.sanity_state_label.text()
        )
    window.sanity_go_run.setFocus(Qt.FocusReason.OtherFocusReason)
    screenshots.append(save_widget_screenshot(
        window, evidence_dir / f"{ordinal:02d}_{benchmark_id}_validation-passed.png"))

    if window.sanity_go_run.isVisible() and window.sanity_go_run.isEnabled():
        QTest.mouseClick(window.sanity_go_run, Qt.MouseButton.LeftButton)
    else:
        window.tabs.setCurrentIndex(8)
    pump(120)
    if window.tabs.currentIndex() != 8:
        raise HarnessFailure("Could not navigate from Pre-run validation to Run Monitor")
    dry_button = window.run_action_buttons["dry-run"]
    if not dry_button.isVisible() or not dry_button.isEnabled():
        raise HarnessFailure("Dry Run is not visible and enabled after GUI validation")
    window.command_text.clear()
    window.log_text.clear()
    dry_start = time.monotonic()
    QTest.mouseClick(dry_button, Qt.MouseButton.LeftButton)
    wait_until(
        lambda: bool(window.command_text.text().strip()),
        timeout_s=validation_timeout_s,
        label=f"{benchmark_id} dry-run command construction",
    )
    wait_until(
        lambda: (
            window._run_mode is None
            and "Process finished with exit code" in window.log_text.toPlainText()
        ),
        timeout_s=validation_timeout_s + 600.0,
        label=f"{benchmark_id} GUI Snakemake dry-run completion",
    )
    stage_times["dry_run_seconds"] = time.monotonic() - dry_start
    guard.assert_clean(f"{benchmark_id} dry run")
    dry_log = window.log_text.toPlainText()
    dry_command = window.command_text.text()
    if window.status_label.text() != "Dry run completed":
        raise HarnessFailure(
            f"Dry run did not complete successfully: status={window.status_label.text()!r}"
        )
    if window.phase_label.text() != "Plan checked — no analysis steps were executed.":
        raise HarnessFailure(
            "Dry-run completion did not clearly distinguish planning from analysis: "
            f"phase={window.phase_label.text()!r}"
        )
    if "Process finished with exit code 0" not in dry_log:
        raise HarnessFailure("Dry-run log does not end in exit code 0")
    if getattr(window, "_run_error_detected", False):
        raise HarnessFailure("Dry-run output matched a Snakemake failure signature")
    screenshots.append(save_widget_screenshot(
        window, evidence_dir / f"{ordinal:02d}_{benchmark_id}_dry-run-completed.png"))

    # Technical evidence is recorded verbatim in text files, while the clean UI
    # screenshot keeps the disclosure collapsed and the common controls visible.
    command_path = evidence_dir / f"{ordinal:02d}_{benchmark_id}_dry-run-command.txt"
    log_path = evidence_dir / f"{ordinal:02d}_{benchmark_id}_dry-run-log.txt"
    command_path.write_text(dry_command + "\n", encoding="utf-8")
    log_path.write_text(dry_log + "\n", encoding="utf-8")
    screenshots.insert(0, {
        "path": str(picker_path),
        "bytes": picker_path.stat().st_size,
        "sha256": sha256(picker_path),
    })

    result = {
        "benchmark_id": benchmark_id,
        "benchmark_name": benchmark.get("name"),
        "project_root": str(expected_root),
        "project_status": window.project_status.toPlainText(),
        "input_type": window.config.input.type if window.config is not None else None,
        "sample_rows": window.metadata_table.rowCount(),
        "resources": resource_evidence,
        "project_seed": project_seed_evidence,
        "check_status": check_status,
        "sanity_state": window.sanity_state_label.text(),
        "sanity_detail": window.sanity_text.toPlainText(),
        "fingerprint": {
            "valid": True,
            "reason": "current",
            "recorded": fingerprint_value,
            "current": fingerprint_value,
            "validated_by": "GUI background fingerprint-and-validation worker",
        },
        "dry_run": {
            "status": window.status_label.text(),
            "command_path": str(command_path),
            "command_sha256": sha256(command_path),
            "log_path": str(log_path),
            "log_sha256": sha256(log_path),
            "exit_zero_marker": "Process finished with exit code 0" in dry_log,
            "failure_signature": bool(getattr(window, "_run_error_detected", False)),
        },
        "timing": stage_times | {"total_seconds": time.monotonic() - started},
        "screenshots": screenshots,
        "project_files": file_manifest(expected_root),
    }
    write_json(evidence_dir / f"{ordinal:02d}_{benchmark_id}_evidence.json", result)
    print(json.dumps({
        "event": "benchmark_pass",
        "id": benchmark_id,
        "check": check_status,
        "dry_run": window.status_label.text(),
        "seconds": round(result["timing"]["total_seconds"], 3),
    }), flush=True)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=utc_run_id())
    parser.add_argument(
        "--benchmarks",
        nargs="*",
        help="Benchmark ids to run in catalog order; default is every bundled preset.",
    )
    parser.add_argument(
        "--inject-unexpected-dialog",
        action="store_true",
        help="Negative QA: prove that an unexpected modal makes the harness fail.",
    )
    parser.add_argument(
        "--inject-fingerprint-change",
        action="store_true",
        help="Negative QA: alter the first project's config after validation and require failure.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = EVIDENCE_ROOT / args.run_id
    if run_dir.exists():
        raise HarnessFailure(f"Refusing to overwrite existing evidence directory: {run_dir}")
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "run_manifest.json"
    manifest: dict[str, object] = {
        "status": "RUNNING",
        "run_id": args.run_id,
        "argv": sys.argv,
        "python": sys.executable,
        "platform": platform.platform(),
        "pyside": PYSIDE_VERSION,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
        "app_version": APP_VERSION,
        "workflow_version": WORKFLOW_VERSION,
    }
    write_json(manifest_path, manifest)

    try:
        recommended = wsl_recommended_workdir(
            f"bulkseq-gui-benchmark-qa\\{args.run_id}"
        )
        if not recommended:
            raise HarnessFailure("No working WSL-native directory is available")
        project_workdir = Path(recommended)
        if project_workdir.exists():
            raise HarnessFailure(f"Refusing to reuse existing WSL work directory: {project_workdir}")

        catalog = load_benchmark_catalog()
        selected_ids = args.benchmarks or [str(item["id"]) for item in catalog]
        unknown = sorted(set(selected_ids) - {str(item["id"]) for item in catalog})
        if unknown:
            raise HarnessFailure(f"Unknown benchmark ids: {unknown}")
        selected = [item for item in catalog if str(item["id"]) in selected_ids]
    except Exception as exc:
        manifest["status"] = "FAIL"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        write_json(manifest_path, manifest)
        print(json.dumps({
            "event": "harness_fail", "run_dir": str(run_dir),
            "error": manifest["error"],
        }), flush=True)
        return 1

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = None

    manifest.update({
        "git_head": revision,
        "source_files": source_manifest(),
        "source_worktree_policy": "read-only; only this ignored harness/evidence directory is written",
        "webengine_policy": "static PPI fallback; WebEngine is outside this Project/Validation/Run Monitor scope",
        "project_workdir": str(project_workdir),
        "benchmarks": selected_ids,
        "results": [],
        "unexpected_dialogs": [],
    })
    write_json(manifest_path, manifest)

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("BulkSeqStudioQA")
    app.setApplicationName(f"gui-preflight-{args.run_id}")
    QSettings().clear()
    QSettings().setValue("theme_mode", "light")
    guard = DialogGuard()
    app.installEventFilter(guard)
    window: MainWindow | None = None
    try:
        window = create_window(app)
        if args.inject_unexpected_dialog:
            QMessageBox.warning(
                window,
                "Injected QA warning",
                "This dialog must be rejected and reported by the fail-closed harness.",
            )
            guard.assert_clean("injected unexpected-dialog negative control")
        results: list[dict[str, object]] = []
        for ordinal, benchmark in enumerate(selected, start=1):
            benchmark_index = catalog.index(benchmark)
            result = exercise_benchmark(
                window,
                guard,
                benchmark,
                benchmark_index,
                project_workdir,
                run_dir,
                ordinal,
                inject_fingerprint_change=(
                    args.inject_fingerprint_change and ordinal == 1
                ),
            )
            results.append(result)
            manifest["results"] = results
            manifest["unexpected_dialogs"] = guard.unexpected
            write_json(manifest_path, manifest)
        manifest["status"] = "PASS"
        manifest["unexpected_dialogs"] = guard.unexpected
        write_json(manifest_path, manifest)
        print(json.dumps({
            "event": "harness_pass",
            "run_dir": str(run_dir),
            "project_workdir": str(project_workdir),
            "benchmarks": [item["benchmark_id"] for item in results],
        }), flush=True)
        return 0
    except Exception as exc:
        manifest["status"] = "FAIL"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        manifest["unexpected_dialogs"] = guard.unexpected
        write_json(manifest_path, manifest)
        print(json.dumps({
            "event": "harness_fail",
            "run_dir": str(run_dir),
            "error": manifest["error"],
        }), flush=True)
        return 1
    finally:
        app.removeEventFilter(guard)
        if window is not None:
            window.close()
            pump(100)


if __name__ == "__main__":
    raise SystemExit(main())
