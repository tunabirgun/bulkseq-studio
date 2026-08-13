"""Capture a deterministic, synthetic BulkSeq Studio GUI QA matrix.

The script uses the real Qt widgets and native platform plugin. It never opens a
user project: a temporary count-matrix project and synthetic result artefacts are
created solely for screenshots, then removed on exit.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QCheckBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
)

from app.core.project import ProjectManager
from app.ui.main_window import MainWindow
from app.ui.readiness_dialog import ReadinessDialog, STATE_ACTION, STATE_OPTIONAL, STATE_READY
from app.ui.theme import apply_theme


PAGE_NAMES = (
    "project",
    "input",
    "metadata",
    "reference",
    "workflow",
    "resources",
    "runtime",
    "pre-run-checks",
    "run-monitor",
    "reports",
    "outputs",
    "ppi-network",
)

_CAPTURE_HEADER_HEIGHT = 120
_CAPTURE_IDENTITY_MAX_DELTA = 24.0
_CAPTURE_IDENTITY_STRICT_DELTA = 4.0
_CAPTURE_MAX_NEAR_BLACK_FRACTION = 0.85


def _settle(milliseconds: int = 180) -> None:
    QApplication.processEvents()
    QTest.qWait(milliseconds)
    QApplication.processEvents()


def _write_tsv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _safe_capture_label(value: str) -> str:
    """Make UI text safe for a portable, deterministic screenshot filename."""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "unnamed"


def _synthetic_figure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(1200, 720, QImage.Format.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#1F2933"), 3))
    painter.drawLine(110, 620, 1120, 620)
    painter.drawLine(110, 620, 110, 90)
    painter.setFont(QFont("Segoe UI", 22, QFont.Weight.DemiBold))
    painter.drawText(420, 52, "Synthetic differential-expression overview")
    painter.setFont(QFont("Segoe UI", 14))
    painter.drawText(500, 690, "log2 fold change")
    painter.save()
    for index in range(96):
        x = 145 + ((index * 89) % 930)
        y = 580 - ((index * 47) % 430)
        significant = index % 7 == 0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2C6FB6" if significant else "#A7B1BD"))
        painter.drawEllipse(x, y, 11 if significant else 7, 11 if significant else 7)
    painter.restore()
    painter.end()
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not write synthetic figure: {path}")


def _create_synthetic_project(workspace: Path) -> Path:
    manager = ProjectManager()
    root = manager.create_project("Synthetic_UI_QA", workspace)
    config = manager.load_config(root)
    config.input.type = "count_matrix"
    config.input.count_matrix = "data/counts.tsv"
    config.project.name = "Synthetic UI QA"
    config.deseq2.contrasts[0].numerator = "treated"
    config.deseq2.contrasts[0].denominator = "control"
    manager.save_config(root, config)
    _write_tsv(
        root / "config" / "samples.tsv",
        ["sample_id", "condition", "layout", "fastq_1", "fastq_2", "replicate", "batch"],
        [
            ["control_1", "control", "single", "", "", 1, "A"],
            ["control_2", "control", "single", "", "", 2, "B"],
            ["treated_1", "treated", "single", "", "", 1, "A"],
            ["treated_2", "treated", "single", "", "", 2, "B"],
        ],
    )
    _write_tsv(
        root / "data" / "counts.tsv",
        ["gene_id", "control_1", "control_2", "treated_1", "treated_2"],
        [[f"GENE{index:03d}", 20 + index, 18 + index, 26 + index * 2, 24 + index * 2]
         for index in range(1, 31)],
    )
    de_rows = [
        [
            f"GENE{index:03d}",
            f"Gene {index}",
            80 + index * 3,
            round((index - 15) / 5, 3),
            round(0.001 + index * 0.002, 4),
        ]
        for index in range(1, 31)
    ]
    _write_csv(
        root / "results" / "deseq2" / "deseq2_results.csv",
        ["gene_id", "symbol", "baseMean", "log2FoldChange", "padj"],
        de_rows,
    )
    _write_csv(
        root / "results" / "export" / "normalized_expression_matrix.csv",
        ["gene_id", "control_1", "control_2", "treated_1", "treated_2"],
        [[f"GENE{index:03d}", 4.1, 4.3, 5.0 + index / 20, 5.2 + index / 20]
         for index in range(1, 13)],
    )
    nodes = [
        [f"GENE{index:03d}", round((index - 6) / 2, 2), 2 + index, round(index / 40, 3), 1 + index % 3]
        for index in range(1, 13)
    ]
    edges = [
        [f"GENE{index:03d}", f"GENE{index + 1:03d}", round(0.4 + index / 30, 3)]
        for index in range(1, 12)
    ]
    _write_csv(
        root / "results" / "networks" / "string_ppi_nodes.csv",
        ["id", "log2FC", "degree", "betweenness", "module"],
        nodes,
    )
    _write_csv(
        root / "results" / "networks" / "string_ppi_edges.csv",
        ["source", "target", "weight"],
        edges,
    )
    _synthetic_figure(root / "results" / "figures" / "synthetic_volcano.png")
    reports = root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_summary.txt").write_text(
        "BulkSeq Studio synthetic QA run\n"
        "Input: count matrix\n"
        "Comparison: treated versus control\n"
        "Status: completed\n",
        encoding="utf-8",
    )
    (reports / "timing_summary.txt").write_text(
        "Total elapsed: 00:18:42\nPeak memory: synthetic display value\n",
        encoding="utf-8",
    )
    (reports / "results_report.html").write_text(
        "<!doctype html><title>Synthetic results report</title>", encoding="utf-8",
    )
    multiqc = root / "results" / "qc" / "multiqc" / "multiqc_report.html"
    multiqc.parent.mkdir(parents=True, exist_ok=True)
    multiqc.write_text("<!doctype html><title>Synthetic MultiQC</title>", encoding="utf-8")
    return root


def _sanitize_visible_paths(window: MainWindow, *, project_open: bool) -> None:
    window.project_status.setPlainText(
        "Synthetic example project is open. Input, settings and saved outputs are ready for review."
        if project_open else
        "Create a new project or open an existing one. Status and next steps appear here.",
    )
    window.workdir.setText(r"C:\BulkSeqProjects")
    window.recent_pick.clear()
    if project_open:
        window.recent_pick.addItem(r"C:\BulkSeqProjects\Synthetic_UI_QA")
        window.recent_pick.setVisible(True)
        window.recent_open.setVisible(True)
        window.recent_empty_label.setVisible(False)
    else:
        window.recent_pick.setVisible(False)
        window.recent_open.setVisible(False)
        window.recent_empty_label.setVisible(True)
    window.command_text.setText("bulkseq run --project Synthetic_UI_QA")
    window.log_text.setPlainText("Synthetic run log is collapsed by default.")


def _prepare_loaded_views(window: MainWindow) -> dict[str, int]:
    window._refresh_gallery()
    target = window.output_table_pick.findText("results/deseq2/deseq2_results.csv")
    if target >= 0:
        window.output_table_pick.setCurrentIndex(target)
        window._load_output_table()
    window._display_reports()
    # WebEngine must have real on-screen geometry before graph injection. Loading
    # into a hidden tab was the source of a zero-sized Cytoscape canvas in an
    # earlier matrix, so deliberately expose PPI here and restore the caller's
    # active page after the probe.
    previous_page = window.tabs.currentIndex()
    window.tabs.setCurrentIndex(PAGE_NAMES.index("ppi-network"))
    _settle(140)
    window._load_ppi_network()
    probe: dict[str, int] = {"nodes": 0, "edges": 0}
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and probe["nodes"] == 0:
        def receive(raw: object) -> None:
            try:
                parsed = json.loads(str(raw))
            except (TypeError, ValueError):
                return
            probe["nodes"] = int(parsed.get("nodes", 0) or 0)
            probe["edges"] = int(parsed.get("edges", 0) or 0)

        window.ppi_viewer.stats(receive)
        _settle(120)
    _settle(500)
    window.tabs.setCurrentIndex(previous_page)
    return probe


def _set_theme(window: MainWindow, mode: str, *, settle: bool = True) -> float:
    """Apply a theme and return synchronous dispatch time (not a settle delay)."""
    started = time.perf_counter()
    if window._current_theme_mode() != mode:
        window._toggle_theme()
    QApplication.processEvents()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if settle:
        _settle(220)
    return elapsed_ms


def _navigate(window: MainWindow, index: int, *, settle: bool = True) -> float:
    """Change a page and return dispatch time before fixed visual settling."""
    started = time.perf_counter()
    window.tabs.setCurrentIndex(index)
    QApplication.processEvents()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if settle:
        _settle(180)
    return elapsed_ms


def _visible_scroll_diagnostics(root: QWidget) -> list[dict[str, object]]:
    """Record every visible scroll surface, including inspector-local scrollbars."""
    diagnostics: list[dict[str, object]] = []
    for ordinal, area in enumerate(root.findChildren(QAbstractScrollArea)):
        if not area.isVisible():
            continue
        bar_h = area.horizontalScrollBar()
        bar_v = area.verticalScrollBar()
        geometry = area.geometry()
        diagnostics.append({
            "ordinal": ordinal,
            "class": type(area).__name__,
            "object_name": area.objectName(),
            "x": geometry.x(),
            "y": geometry.y(),
            "width": geometry.width(),
            "height": geometry.height(),
            "horizontal_max": bar_h.maximum(),
            "vertical_max": bar_v.maximum(),
        })
    return diagnostics


def _text_layout_diagnostics(root: QWidget) -> list[dict[str, object]]:
    """Report wrapped labels and clipped checkbox or push-button captions.

    Scrollbar maxima do not reveal awkward two-line labels or a child clipped by
    an ancestor.  These are visual failures even when every control remains
    technically reachable, so the screenshot harness records and rejects them.
    """
    findings: list[dict[str, object]] = []
    for label in root.findChildren(QLabel):
        if not label.isVisible() or label.property("uiRole") != "formLabel":
            continue
        text_value = " ".join(label.text().split())
        if not text_value or len(text_value) > 40 or "\n" in label.text():
            continue
        available = max(1, label.contentsRect().width())
        required = label.fontMetrics().horizontalAdvance(text_value)
        if required > available:
            findings.append({
                "kind": "short_form_label_wrap_or_clip",
                "text": text_value,
                "available_width": available,
                "required_width": required,
            })
    for checkbox in root.findChildren(QCheckBox):
        if not checkbox.isVisible() or not checkbox.text().strip() or "\n" in checkbox.text():
            continue
        visible = checkbox.visibleRegion().boundingRect()
        if visible.isEmpty():
            continue
        horizontally_clipped = (
            visible.left() > checkbox.rect().left()
            or visible.right() < checkbox.rect().right()
        )
        if horizontally_clipped or checkbox.width() < checkbox.sizeHint().width():
            findings.append({
                "kind": "checkbox_caption_clipped",
                "text": " ".join(checkbox.text().split()),
                "visible_width": visible.width(),
                "control_width": checkbox.width(),
                "required_width": checkbox.sizeHint().width(),
            })
    for button in root.findChildren(QPushButton):
        if not button.isVisible() or not button.text().strip():
            continue
        visible = button.visibleRegion().boundingRect()
        if visible.isEmpty():
            continue
        # Qt ampersands declare mnemonics rather than visible glyphs.  Measure
        # each explicit line and retain modest native-style horizontal padding.
        display_text = button.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
        required_text = max(
            (button.fontMetrics().horizontalAdvance(line) for line in display_text.splitlines()),
            default=0,
        )
        required = required_text + 20
        horizontally_clipped = (
            visible.left() > button.rect().left()
            or visible.right() < button.rect().right()
        )
        if horizontally_clipped or button.width() < required:
            findings.append({
                "kind": "push_button_caption_clipped",
                "text": " ".join(display_text.split()),
                "visible_width": visible.width(),
                "control_width": button.width(),
                "required_width": required,
            })
    return findings


def _ppi_accessibility_state(window: MainWindow) -> dict[str, object]:
    """Exercise the real WebEngine key handler and record its AT-visible result."""
    viewer = getattr(window, "ppi_viewer", None)
    view = getattr(viewer, "view", None)
    if view is None:
        return {"available": False}
    outcome: dict[str, object] = {"available": True, "received": False}

    def receive(raw: object) -> None:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            outcome["raw"] = str(raw)
            return
        outcome.update(parsed)
        outcome["received"] = True

    script = """
        (() => {
          const canvas = document.getElementById('cy');
          if (!canvas) return JSON.stringify({error: 'missing-canvas'});
          canvas.focus();
          canvas.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
          canvas.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
          const active = canvas.getAttribute('aria-activedescendant') || '';
          const selected = [...document.querySelectorAll('#node-list [aria-selected="true"]')]
            .map((node) => node.textContent || '');
          return JSON.stringify({
            canvas_focused: document.activeElement === canvas,
            active_descendant: active,
            selected_nodes: selected,
            status: (document.getElementById('network-status') || {}).textContent || '',
            listed_nodes: document.querySelectorAll('#node-list [role="option"]').length,
          });
        })()
    """
    view.page().runJavaScript(script, receive)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not outcome.get("received"):
        _settle(40)
    return outcome


def _sampled_image_statistics(image: QImage) -> dict[str, float]:
    """Return cheap saved-pixel sanity statistics for a screenshot gate."""
    if image.isNull() or image.width() < 1 or image.height() < 1:
        return {"near_black_fraction": 1.0, "luminance_range": 0.0}
    near_black = 0
    count = 0
    minimum = 255.0
    maximum = 0.0
    step = max(1, min(image.width(), image.height()) // 160)
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            colour = image.pixelColor(x, y)
            red, green, blue = colour.red(), colour.green(), colour.blue()
            near_black += int(max(red, green, blue) < 20)
            luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            minimum = min(minimum, luminance)
            maximum = max(maximum, luminance)
            count += 1
    return {
        "near_black_fraction": near_black / max(count, 1),
        "luminance_range": maximum - minimum,
    }


def _header_identity_delta(expected: QImage, captured: QImage) -> float:
    """Measure whether a compositor frame still belongs to our Qt window.

    The HTML canvas is absent from QWidget.grab() on Windows, but the native
    application header is present in both images. Comparing that stable band
    detects foreground theft without making assumptions about graph pixels.
    """
    if expected.isNull() or captured.isNull():
        return 255.0
    if expected.size() != captured.size():
        expected = expected.scaled(captured.size())
    width = min(expected.width(), captured.width())
    height = min(_CAPTURE_HEADER_HEIGHT, expected.height(), captured.height())
    if width < 1 or height < 1:
        return 255.0
    total = 0
    samples = 0
    step = 5
    for y in range(0, height, step):
        for x in range(0, width, step):
            left = expected.pixelColor(x, y)
            right = captured.pixelColor(x, y)
            total += abs(left.red() - right.red())
            total += abs(left.green() - right.green())
            total += abs(left.blue() - right.blue())
            samples += 3
    return total / max(samples, 1)


def _native_region_identity_delta(
    expected: QImage,
    captured: QImage,
    excluded: QRect | None = None,
) -> float:
    """Compare every native pixel outside the separately composed web canvas."""
    if expected.isNull() or captured.isNull():
        return 255.0
    original_width = max(expected.width(), 1)
    original_height = max(expected.height(), 1)
    if expected.size() != captured.size():
        expected = expected.scaled(captured.size())
    scaled_excluded = QRect()
    if excluded is not None and not excluded.isNull():
        scaled_excluded = QRect(
            round(excluded.x() * captured.width() / original_width),
            round(excluded.y() * captured.height() / original_height),
            round(excluded.width() * captured.width() / original_width),
            round(excluded.height() * captured.height() / original_height),
        )
    total = 0
    samples = 0
    step = 6
    for y in range(0, captured.height(), step):
        for x in range(0, captured.width(), step):
            if not scaled_excluded.isNull() and scaled_excluded.contains(x, y):
                continue
            left = expected.pixelColor(x, y)
            right = captured.pixelColor(x, y)
            total += abs(left.red() - right.red())
            total += abs(left.green() - right.green())
            total += abs(left.blue() - right.blue())
            samples += 3
    return total / max(samples, 1)


def _activate_native_window(widget) -> bool:
    """Raise ``widget`` and report whether its top-level HWND owns focus."""
    widget.raise_()
    widget.activateWindow()
    widget.repaint()
    if not sys.platform.startswith("win"):
        return True
    try:
        import ctypes

        user32 = ctypes.windll.user32
        target = int(widget.window().winId())
        user32.BringWindowToTop(target)
        user32.SetForegroundWindow(target)
        foreground = int(user32.GetForegroundWindow())
        root = int(user32.GetAncestor(foreground, 2))  # GA_ROOT
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(process_id))
        return (
            foreground == target
            or root == target
            or int(process_id.value) == os.getpid()
        )
    except Exception:
        return False


def _verified_compositor_grab(
    widget,
    screen,
    width: int,
    height: int,
    excluded_web_rect: QRect,
):
    """Capture a WebEngine-bearing window only after identity verification."""
    last_reason = "foreground ownership could not be established"
    for _attempt in range(3):
        owns_foreground = _activate_native_window(widget)
        _settle(140)
        expected = widget.grab().toImage()
        origin = widget.mapToGlobal(QPoint(0, 0))
        pixmap = screen.grabWindow(0, origin.x(), origin.y(), width, height)
        captured = pixmap.toImage()
        header_delta = _header_identity_delta(expected, captured)
        native_delta = _native_region_identity_delta(
            expected, captured, excluded_web_rect)
        # Windows may refuse SetForegroundWindow while another application is
        # receiving input. In that case accept only an effectively exact match
        # to BulkSeq Studio's independently grabbed header; a merely similar
        # frame still fails. Foreground-owned frames retain a small allowance
        # for compositor antialiasing.
        identity_limit = (
            _CAPTURE_IDENTITY_MAX_DELTA
            if owns_foreground else _CAPTURE_IDENTITY_STRICT_DELTA
        )
        if header_delta <= identity_limit and native_delta <= identity_limit:
            return pixmap, header_delta, native_delta, owns_foreground
        last_reason = (
            f"owns_foreground={owns_foreground}, header_delta={header_delta:.2f}, "
            f"native_delta={native_delta:.2f}"
        )
        _settle(180)
    raise RuntimeError(f"Rejected compositor screenshot: {last_reason}")


def _assert_visual_capture_sane(filename: str, image: QImage) -> dict[str, float]:
    """Fail closed on the near-black foreground frames seen in native QA."""
    statistics = _sampled_image_statistics(image)
    if statistics["near_black_fraction"] > _CAPTURE_MAX_NEAR_BLACK_FRACTION:
        raise RuntimeError(
            f"Rejected visually implausible screenshot {filename}: {statistics}"
        )
    if statistics["luminance_range"] < 8.0:
        raise RuntimeError(f"Rejected near-uniform screenshot {filename}: {statistics}")
    return statistics


def _capture(
    window: MainWindow,
    output: Path,
    name: str,
    manifest: list[dict[str, object]],
    **state: object,
) -> None:
    _settle(220)
    focus = QApplication.focusWidget()
    if focus is not None and not bool(state.get("capture_focus")):
        focus.clearFocus()
        _settle(40)
    actual_width = window.width()
    actual_height = window.height()
    filename = f"{name}-{actual_width}x{actual_height}.png"
    path = output / filename
    # QWidget.grab() cannot be stolen by another foreground application and is
    # authoritative for every native page. Only a visible QWebEngine PPI canvas
    # needs the desktop compositor; that exceptional path is foreground- and
    # pixel-identity checked before its frame is accepted.
    ppi_visible = (
        window.tabs.currentIndex() == PAGE_NAMES.index("ppi-network")
        and getattr(window, "ppi_viewer", None) is not None
        and window.ppi_viewer.available
        and not window.ppi_viewer.empty_state_visible
    )
    identity_delta: float | None = None
    if ppi_visible:
        screen = window.screen() or QApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("No screen is available for native GUI capture")
        web_view = window.ppi_viewer.view
        web_origin = web_view.mapTo(window, QPoint(0, 0))
        excluded_web_rect = QRect(web_origin, web_view.size())
        pixmap, identity_delta, native_identity_delta, foreground_owned = (
            _verified_compositor_grab(
                window, screen, actual_width, actual_height, excluded_web_rect)
        )
        capture_method = "compositor-verified"
    else:
        window.repaint()
        _settle(80)
        pixmap = window.grab()
        capture_method = "widget-grab"
        foreground_owned = None
        native_identity_delta = None
    visual_statistics = _assert_visual_capture_sane(filename, pixmap.toImage())
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save screenshot: {path}")
    current = window.tabs.currentIndex()
    page = window.tabs.widget(current)
    entry = {
        "file": filename,
        "width": actual_width,
        "height": actual_height,
        "theme": window._current_theme_mode(),
        "page_index": current,
        "page": PAGE_NAMES[current],
        "page_enabled": page.isEnabled(),
        "visible_scroll_surfaces": _visible_scroll_diagnostics(window),
        "text_layout_findings": _text_layout_diagnostics(window),
        "capture_method": capture_method,
        "capture_identity_mean_delta": identity_delta,
        "capture_native_identity_mean_delta": native_identity_delta,
        "capture_foreground_owned": foreground_owned,
        "visual_statistics": visual_statistics,
        **state,
    }
    if hasattr(page, "horizontalScrollBar"):
        entry["horizontal_scroll_max"] = page.horizontalScrollBar().maximum()
        entry["vertical_scroll_max"] = page.verticalScrollBar().maximum()
    manifest.append(entry)


def _capture_dialog(
    dialog: ReadinessDialog,
    window: MainWindow,
    output: Path,
    name: str,
    manifest: list[dict[str, object]],
    **state: object,
) -> None:
    """Capture a visible auxiliary dialog with the same native compositor path."""
    _settle(220)
    actual_width = dialog.width()
    actual_height = dialog.height()
    filename = f"{name}-{actual_width}x{actual_height}.png"
    path = output / filename
    dialog.repaint()
    _settle(80)
    # The readiness dialog has no WebEngine surface. A widget grab is complete
    # and cannot accidentally record another foreground application.
    pixmap = dialog.grab()
    visual_statistics = _assert_visual_capture_sane(filename, pixmap.toImage())
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save screenshot: {path}")
    manifest.append({
        "file": filename,
        "width": actual_width,
        "height": actual_height,
        "theme": window._current_theme_mode(),
        "page_index": -1,
        "page": "environment-setup",
        "page_enabled": dialog.isEnabled(),
        "visible_scroll_surfaces": _visible_scroll_diagnostics(dialog),
        "text_layout_findings": _text_layout_diagnostics(dialog),
        "capture_method": "widget-grab",
        "capture_identity_mean_delta": None,
        "capture_native_identity_mean_delta": None,
        "capture_foreground_owned": None,
        "visual_statistics": visual_statistics,
        **state,
    })


def _validate_manifest(output: Path, manifest: list[dict[str, object]], ppi_probe: dict[str, int]) -> None:
    """Fail closed if a matrix overwrote files or emitted unusable captures."""
    filenames = [str(entry["file"]) for entry in manifest]
    duplicates = sorted({name for name in filenames if filenames.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"Duplicate screenshot filenames: {duplicates}")
    missing = [name for name in filenames if not (output / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing screenshots listed in manifest: {missing}")
    empty = [name for name in filenames if (output / name).stat().st_size == 0]
    if empty:
        raise RuntimeError(f"Empty screenshot files: {empty}")
    if ppi_probe.get("nodes", 0) < 1:
        raise RuntimeError(f"PPI probe did not observe a synthetic graph: {ppi_probe}")
    text_failures = [
        {"file": entry["file"], "findings": entry.get("text_layout_findings")}
        for entry in manifest if entry.get("text_layout_findings")
    ]
    if text_failures:
        raise RuntimeError(f"Awkward or clipped UI text detected: {text_failures}")
    unknown_capture_methods = [
        entry["file"] for entry in manifest
        if entry.get("capture_method") not in {"widget-grab", "compositor-verified"}
    ]
    if unknown_capture_methods:
        raise RuntimeError(f"Unverified screenshot capture methods: {unknown_capture_methods}")


def capture_matrix(output: Path, *, quick: bool = False) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("BulkSeq Studio QA")
    app.setApplicationName("BulkSeq Studio GUI Matrix")
    QSettings().clear()
    QSettings().setValue("theme_mode", "light")
    apply_theme(app, "light")

    with tempfile.TemporaryDirectory(prefix="bulkseq-ui-qa-") as temporary:
        root = _create_synthetic_project(Path(temporary))
        window = MainWindow()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.resize(1093, 640)
        window.move(40, 40)
        window.show()
        _settle(300)
        _sanitize_visible_paths(window, project_open=False)

        # Purposeful empty states before a project exists.
        for mode in ("light", "dark"):
            _set_theme(window, mode)
            for index in (0, 7, 8, 9, 10, 11):
                _navigate(window, index)
                _capture(window, output, f"empty-{mode}-{PAGE_NAMES[index]}", manifest, state="no_project")

        _set_theme(window, "light")
        window._load_project(root)
        _sanitize_visible_paths(window, project_open=True)
        ppi_probe = _prepare_loaded_views(window)

        modes = ("light",) if quick else ("light", "dark")
        desktop_sizes = ((1093, 640),) if quick else ((1093, 640), (1366, 768), (1920, 1080))
        inspector_sizes = ((1093, 640),) if quick else ((1093, 640), (1366, 768))

        # Every page, every target desktop size, both modes.
        for width, height in desktop_sizes:
            window.resize(width, height)
            _settle(260)
            for mode in modes:
                theme_dispatch_ms = _set_theme(window, mode)
                previous = window.tabs.currentIndex()
                for index, page_name in enumerate(PAGE_NAMES):
                    elapsed_ms = _navigate(window, index, settle=False)
                    _capture(
                        window,
                        output,
                        f"page-{mode}-{page_name}",
                        manifest,
                        state="loaded",
                        transition_from=previous,
                        navigation_dispatch_ms=round(elapsed_ms, 2),
                        theme_dispatch_ms=round(theme_dispatch_ms, 2),
                    )
                    previous = index

        # Workflow subsections and Outputs/PPI inspectors at compact and wide sizes.
        for width, height in inspector_sizes:
            window.resize(width, height)
            for mode in modes:
                _set_theme(window, mode)
                _navigate(window, 4)
                for index in range(window.workflow_section_tabs.count()):
                    window.workflow_section_tabs.setCurrentIndex(index)
                    label = _safe_capture_label(window.workflow_section_tabs.tabText(index))
                    _capture(window, output, f"workflow-{mode}-{label}", manifest, state="workflow_section")

                # External differential-expression tables carry an immutable signed direction;
                # capture the route-specific Comparison surface so inert local-model controls can
                # never regress back into view unnoticed.
                original_input_type = window.config.input.type
                direction = window.config.input.deseq2_results_direction
                window.config.input.type = "deseq2_results"
                direction.numerator = "treated"
                direction.denominator = "control"
                direction.confirmed = True
                direction.confirmed_at = "2026-08-10T12:00:00Z"
                window._apply_input_mode_ui()
                comparison_index = next(
                    (index for index in range(window.workflow_section_tabs.count())
                     if window.workflow_section_tabs.tabText(index) == "Comparison"),
                    0,
                )
                window.workflow_section_tabs.setCurrentIndex(comparison_index)
                _capture(
                    window,
                    output,
                    f"workflow-{mode}-comparison-external-results",
                    manifest,
                    state="workflow_external_results",
                )
                window.config.input.type = original_input_type
                window._apply_input_mode_ui()

                _navigate(window, 10)
                figure_tabs = window.findChild(QTabWidget, "figureStyleSections")
                if figure_tabs is not None:
                    window.results_inspector.setCurrentIndex(0)
                    for index in range(figure_tabs.count()):
                        figure_tabs.setCurrentIndex(index)
                        label = _safe_capture_label(figure_tabs.tabText(index))
                        _capture(window, output, f"outputs-{mode}-figure-{label}", manifest, state="figure_style")
                for index in range(window.results_inspector.count()):
                    window.results_inspector.setCurrentIndex(index)
                    label = _safe_capture_label(window.results_inspector.itemText(index))
                    _capture(window, output, f"outputs-{mode}-inspector-{label}", manifest, state="results_inspector")

                _navigate(window, 11)
                ppi_inspector = getattr(window, "ppi_inspector", None)
                if ppi_inspector is not None:
                    for index in range(ppi_inspector.count()):
                        ppi_inspector.setCurrentIndex(index)
                        label = _safe_capture_label(ppi_inspector.itemText(index))
                        _capture(window, output, f"ppi-{mode}-{label}", manifest, state="ppi_inspector")

        # Run and validation states start from a clean baseline each time.
        window.resize(1366, 768)
        for mode in modes:
            _set_theme(window, mode)
            _navigate(window, 7)
            for status in ("PASS", "WARNING", "REVIEW_REQUIRED", "FAIL", "STALE"):
                window._update_sanity_state({"01_input_validation": status}, reset_approval=True)
                _capture(window, output, f"checks-{mode}-{status.lower()}", manifest, state="validation")

            _navigate(window, 8)
            run_states = (
                ("ready", False, "Ready to start the synthetic analysis.", ""),
                ("validating", False, "Checking current scientific inputs before launch...", "Verifying saved inputs"),
                ("running", True, "Running synthetic analysis...", "Current step: differential expression"),
                ("failed", False, "Run stopped after a synthetic validation failure.", "Review Pre-run checks"),
                ("completed", False, "Synthetic analysis completed.", "Reports and outputs are ready"),
            )
            for label, active, status_text, phase_text in run_states:
                window.execution_details_toggle.setChecked(False)
                window.run_options_toggle.setChecked(False)
                window.elapsed_label.setText("Elapsed: 00:00:00")
                window.elapsed_label.setVisible(active)
                window.progress.setRange(0, 100)
                window.progress.setValue(55 if active else (100 if label == "completed" else 0))
                window.status_label.setText(status_text)
                window.phase_label.setText(phase_text)
                window._set_running_ui(active)
                _capture(window, output, f"run-{mode}-{label}", manifest, state="run_state")
            window._set_running_ui(False)
            window.status_label.setText("Interrupted run detected; completed steps can be reused.")
            window.phase_label.setText("Resume from the last completed workflow step")
            window.progress.setRange(0, 100)
            window.progress.setValue(0)
            window.resume_banner.setText(
                "This project has an unfinished run. Resume continues from where it stopped and reuses completed steps.")
            window.resume_banner.setVisible(True)
            window.resume_button.setVisible(True)
            window.run_action_buttons["resume"].setVisible(True)
            window.run_action_buttons["unlock"].setVisible(True)
            _capture(window, output, f"run-{mode}-resumable", manifest, state="run_resumable")
            window.resume_banner.setVisible(False)
            window.resume_button.setVisible(False)
            window.run_action_buttons["resume"].setVisible(False)
            window.run_action_buttons["unlock"].setVisible(False)
            window.execution_details_toggle.setChecked(True)
            window.command_text.setText("bulkseq run --project Synthetic_UI_QA")
            window.log_text.setPlainText("Building the workflow plan...\nSynthetic execution detail line.")
            _capture(window, output, f"run-{mode}-details-open", manifest, state="run_details")

        # Input routes and each frequently-hidden advanced surface. These are
        # deliberately captured at compact and wide desktop widths rather than
        # relying on a default-only gallery.
        for width, height in inspector_sizes:
            window.resize(width, height)
            _settle(180)
            for mode in modes:
                _set_theme(window, mode)
                _navigate(window, 1)
                for route_index in range(window.input_route_tabs.count()):
                    window.input_route_tabs.setCurrentIndex(route_index)
                    route_name = _safe_capture_label(window.input_route_tabs.tabText(route_index))
                    _capture(window, output, f"input-{mode}-route-{route_index + 1}-{route_name}", manifest,
                             state="input_route", route_index=route_index)

                _navigate(window, 2)
                window.metadata_more_toggle.setChecked(True)
                _capture(window, output, f"metadata-{mode}-more-tools-expanded", manifest,
                         state="metadata_more_tools", expanded=True)
                window.metadata_more_toggle.setChecked(False)

                _navigate(window, 3)
                window.reference_custom_toggle.setChecked(True)
                _capture(window, output, f"reference-{mode}-custom-expanded", manifest,
                         state="reference_custom", expanded=True)
                window.reference_custom_toggle.setChecked(False)

                _navigate(window, 4)
                advanced_index = next(
                    (index for index in range(window.workflow_section_tabs.count())
                     if window.workflow_section_tabs.tabText(index) == "Advanced"),
                    window.workflow_section_tabs.count() - 1,
                )
                window.workflow_section_tabs.setCurrentIndex(advanced_index)
                window.adv_toggle.setChecked(True)
                _capture(window, output, f"workflow-{mode}-advanced-expanded", manifest,
                         state="workflow_advanced", expanded=True)
                window.adv_toggle.setChecked(False)

                output_index = next(
                    (index for index in range(window.workflow_section_tabs.count())
                     if window.workflow_section_tabs.tabText(index) == "Output options"),
                    0,
                )
                window.workflow_section_tabs.setCurrentIndex(output_index)
                custom_toggle = getattr(window, "workflow_custom_gene_sets_toggle", None)
                if custom_toggle is None:
                    custom_toggle = next(
                        (toggle for toggle in window.findChildren(QToolButton)
                         if toggle.text().startswith("Custom gene sets")),
                        None,
                    )
                if custom_toggle is not None:
                    custom_toggle.setChecked(True)
                    _settle(80)
                    output_page = window.workflow_section_tabs.currentWidget()
                    if isinstance(output_page, QScrollArea):
                        output_page.ensureWidgetVisible(window.custom_gmt, 12, 12)
                        _settle(80)
                _capture(window, output, f"workflow-{mode}-custom-gene-sets-expanded", manifest,
                         state="workflow_custom_gene_sets", expanded=custom_toggle is not None)
                if custom_toggle is not None:
                    custom_toggle.setChecked(False)

                _navigate(window, 5)
                window.resource_manual_toggle.setChecked(True)
                _capture(window, output, f"resources-{mode}-manual-expanded", manifest,
                         state="resource_manual", expanded=True)
                window.resource_manual_toggle.setChecked(False)

                _navigate(window, 10)
                window.results_inspector.setCurrentIndex(0)
                figure_tabs = window.findChild(QTabWidget, "figureStyleSections")
                if figure_tabs is not None:
                    appearance_index = next(
                        (index for index in range(figure_tabs.count())
                         if figure_tabs.tabText(index) == "Appearance"), 0)
                    figure_tabs.setCurrentIndex(appearance_index)
                    window.figure_appearance_advanced_toggle.setChecked(True)
                    _capture(window, output, f"outputs-{mode}-figure-appearance-advanced", manifest,
                             state="figure_style_advanced", section="appearance")
                    window.figure_appearance_advanced_toggle.setChecked(False)
                    detail_index = next(
                        (index for index in range(figure_tabs.count())
                         if figure_tabs.tabText(index) == "Detail"), 0)
                    figure_tabs.setCurrentIndex(detail_index)
                    window.figure_detail_advanced_toggle.setChecked(True)
                    _capture(window, output, f"outputs-{mode}-figure-detail-advanced", manifest,
                             state="figure_style_advanced", section="detail")
                    window.figure_detail_advanced_toggle.setChecked(False)

                _navigate(window, 11)
                ppi_accessibility = _ppi_accessibility_state(window)
                _capture(window, output, f"ppi-{mode}-keyboard-selected-node", manifest,
                         state="ppi_keyboard_accessibility", capture_focus=True,
                         ppi_accessibility=ppi_accessibility)

        # Resize and rapid-navigation transition endpoints. Derive the exact
        # TaskNavigator breakpoint from the UI implementation rather than
        # maintaining a second, drift-prone constant in the test harness.
        _set_theme(window, "light")
        breakpoint = int(window.tabs.COMPACT_BREAKPOINT)
        for step, width in enumerate((breakpoint - 1, breakpoint, breakpoint - 1), start=1):
            window.resize(width, 720)
            _navigate(window, 4)
            _capture(window, output, f"transition-resize-{step}-{width}", manifest,
                     state="breakpoint", breakpoint=breakpoint, step=step)
        for index in (0, 4, 10, 11):
            _navigate(window, index, settle=False)
        _capture(window, output, "transition-rapid-project-workflow-outputs-ppi", manifest, state="rapid_navigation")

        # Environment setup is a first-run workflow outside the 12 task pages.
        # Populate it with synthetic states so capture never probes or mutates the
        # real machine while still exercising action-needed, ready and log views.
        original_refresh = ReadinessDialog.refresh
        ReadinessDialog.refresh = lambda self: None
        try:
            readiness = ReadinessDialog(window)
        finally:
            ReadinessDialog.refresh = original_refresh
        window.readiness_dialog = readiness
        readiness.show()
        for mode in modes:
            _set_theme(window, mode)
            readiness.card_python.update_state(
                STATE_READY, "All Python GUI and core packages are installed.")
            readiness.card_wsl.update_state(
                STATE_READY, "WSL2 and the Ubuntu distribution are available.")
            readiness.card_core.update_state(
                STATE_ACTION,
                "The core bioinformatics environment needs installation before a workflow can run.",
                action_label="Install core environment",
                action_handler=lambda: None,
            )
            readiness.card_r.update_state(
                STATE_OPTIONAL,
                "Add the R and Bioconductor analysis stack after the core environment.",
                action_label="Install R / DESeq2 stack",
                action_handler=lambda: None,
                action_enabled=False,
            )
            readiness._update_summary()
            _capture_dialog(readiness, window, output, f"environment-{mode}-action-needed", manifest,
                            state="environment_action_needed")
            for card, detail in (
                (readiness.card_python, "All Python GUI and core packages are installed."),
                (readiness.card_wsl, "WSL2 and the Ubuntu distribution are available."),
                (readiness.card_core, "The pinned core bioinformatics environment is ready."),
                (readiness.card_r, "R, DESeq2, enrichment and figure packages are ready."),
            ):
                card.update_state(STATE_READY, detail)
            readiness._update_summary()
            _capture_dialog(readiness, window, output, f"environment-{mode}-ready", manifest,
                            state="environment_ready")
            readiness.text.setPlainText(
                "Synthetic setup log\nPython: ready\nCore environment: ready\nR / DESeq2: ready")
            if not readiness._details_visible:
                readiness._toggle_details()
            _capture_dialog(readiness, window, output, f"environment-{mode}-details-open", manifest,
                            state="environment_details")
            if readiness._details_visible:
                readiness._toggle_details()
        readiness.close()
        window.readiness_dialog = None

        window.close()
        _settle(100)

    _validate_manifest(output, manifest, ppi_probe)
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps({"ppi_probe": ppi_probe, "screenshots": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Output directory for PNGs and manifest.json")
    parser.add_argument("--quick", action="store_true",
                        help="Capture one light compact smoke matrix while retaining all state coverage")
    args = parser.parse_args()
    manifest = capture_matrix(args.out.expanduser().resolve(), quick=args.quick)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
