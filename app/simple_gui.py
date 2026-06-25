"""A simple, native cross-platform GUI for BulkSeq Studio.

The full Windows GUI drives the pipeline through WSL2. This simple GUI runs the
same Snakemake pipeline DIRECTLY in the current environment (no WSL), which is
the natural mode on Linux and macOS where the bulkseq micromamba environment is
local. Launch it from an activated environment that has snakemake on PATH:

    micromamba activate bulkseq      # or: conda activate bulkseq
    python -m app.simple_gui

It loads an existing project (a folder with config/config.yaml and workflow/),
shows a short summary, and runs / dry-runs / unlocks the pipeline, streaming the
log. It reuses the same core as the full app (config model + Snakemake runner),
so results are identical to a Windows WSL run.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import APP_NAME, APP_VERSION
from app.core.config_models import AppConfig
from app.core.snakemake_runner import SnakemakeRunner, build_snakemake_command

APP_TITLE = f"{APP_NAME} (simple) {APP_VERSION}"
MODES = ["run", "dry-run", "unlock"]


class _RunThread(QThread):
    """Streams the runner's stdout line by line, then reports the exit code."""

    line = Signal(str)
    done = Signal(int)

    def __init__(self, runner: SnakemakeRunner) -> None:
        super().__init__()
        self.runner = runner

    def run(self) -> None:  # noqa: D401 - QThread entry point
        proc = self.runner.start()
        if proc.stdout is not None:
            for raw in proc.stdout:
                self.line.emit(raw.rstrip("\n"))
        proc.wait()
        self.done.emit(proc.returncode if proc.returncode is not None else -1)


class SimpleWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.project_root: Path | None = None
        self.config: AppConfig | None = None
        self.runner: SnakemakeRunner | None = None
        self.thread: _RunThread | None = None

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "Project folder (contains config/config.yaml and workflow/)"
        )
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        load = QPushButton("Load")
        load.clicked.connect(lambda: self._load(Path(self.path_edit.text().strip())))
        row.addWidget(QLabel("Project:"))
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        row.addWidget(load)
        layout.addLayout(row)

        self.summary = QLabel("No project loaded.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._run)
        self.run_btn.setEnabled(False)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        controls.addWidget(QLabel("Mode:"))
        controls.addWidget(self.mode)
        controls.addStretch(1)
        controls.addWidget(self.run_btn)
        controls.addWidget(self.stop_btn)
        layout.addLayout(controls)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        self.resize(860, 580)

    # ---- project loading -------------------------------------------------
    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select project folder")
        if chosen:
            self.path_edit.setText(chosen)
            self._load(Path(chosen))

    def _load(self, root: Path) -> None:
        cfg_path = root / "config" / "config.yaml"
        if not cfg_path.is_file():
            QMessageBox.warning(self, APP_TITLE, f"No config/config.yaml found in:\n{root}")
            return
        try:
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            self.config = AppConfig.model_validate(raw)
        except Exception as exc:  # surface any parse/validation error plainly
            QMessageBox.critical(self, APP_TITLE, f"Could not load the configuration:\n{exc}")
            return
        self.project_root = root
        organism = getattr(self.config.reference, "organism_name", None) or "—"
        self.summary.setText(
            f"Loaded: {self.config.project.name}    "
            f"input: {self.config.input.type}    organism: {organism}"
        )
        self.run_btn.setEnabled(True)
        self._append(f"Loaded project: {root}")

    # ---- running ---------------------------------------------------------
    def _run(self) -> None:
        if self.config is None or self.project_root is None:
            return
        if shutil.which("snakemake") is None:
            QMessageBox.warning(
                self,
                APP_TITLE,
                "snakemake was not found on PATH.\n\nActivate the bulkseq environment first, "
                "for example:\n    micromamba activate bulkseq\nthen relaunch this app.",
            )
            return
        mode = self.mode.currentText()
        command = build_snakemake_command(self.project_root, self.config, mode=mode, use_wsl=False)
        self._append("$ " + command.display)
        self.runner = SnakemakeRunner(self.project_root, command)
        self.thread = _RunThread(self.runner)
        self.thread.line.connect(self._append)
        self.thread.done.connect(self._finished)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.thread.start()

    def _stop(self) -> None:
        if self.runner is not None:
            self.runner.stop()
        self._append("[stop requested]")
        self.stop_btn.setEnabled(False)

    def _finished(self, code: int) -> None:
        self._append(f"[finished, exit code {code}]")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    window = SimpleWindow()

    # Headless self-test for CI / packaging gates (run with QT_QPA_PLATFORM=offscreen):
    # construct the window, optionally load a project + build the native command, optionally render a
    # screenshot, write a sentinel, and exit.
    if os.environ.get("BULKSEQ_SIMPLE_SELFTEST"):
        ok = True
        native_cmd_ok = None
        project = os.environ.get("BULKSEQ_SIMPLE_SELFTEST_PROJECT")
        if project:
            try:
                window._load(Path(project))
                ok = window.config is not None
                if window.config is not None and window.project_root is not None:
                    # Functional check: the GUI must build a NATIVE snakemake command (no WSL wrapper).
                    cmd = build_snakemake_command(
                        window.project_root, window.config, mode="dry-run", use_wsl=False
                    )
                    native_cmd_ok = (not cmd.use_wsl) and cmd.command[0] == "snakemake"
                    ok = ok and native_cmd_ok
            except Exception:
                ok = False
        shot = os.environ.get("BULKSEQ_SIMPLE_SCREENSHOT_OUT")
        if shot:
            for line in ("$ snakemake --snakefile workflow/Snakefile --cores 8 ...",
                         "Building DAG of jobs...", "rule deseq2:", "Finished job 0.",
                         "[finished, exit code 0]"):
                window._append(line)
            window.resize(900, 600)
            window.grab().save(shot)
        result = {
            "constructed": True,
            "config_loaded": window.config is not None,
            "native_command_ok": native_cmd_ok,
            "screenshot": bool(shot),
            "version": APP_VERSION,
            "pass": ok,
        }
        out = os.environ.get("BULKSEQ_SIMPLE_SELFTEST_OUT")
        if out:
            Path(out).write_text(json.dumps(result), encoding="utf-8")
        print(json.dumps(result))
        return 0 if ok else 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
