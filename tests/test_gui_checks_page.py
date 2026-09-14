from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import get_args

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

import app.ui.main_window as main_window_module
from app.constants import APP_VERSION
from app.core.config_models import InputConfig, default_config
from app.core.preflight import write_input_validation_with_fingerprint
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow" / "Snakefile"
HTML_REPORT = ROOT / "workflow" / "scripts" / "make_html_report.py"
UNCHANGED = "results/deseq2/unchanged_genes.csv"
COUNTS = "results/counts/counts.txt"
# The Snakefile lines the guard walk anchors on, verbatim.
UNCHANGED_TARGET = 'targets.append("results/deseq2/unchanged_genes.csv")'
COUNTS_TARGET = 'targets.insert(0, "results/counts/counts.txt")'
# Routes the workflow writes counts.txt for that the Outputs picker still withholds.
# count_matrix is the one: there the file is the user's own uploaded matrix copied to the
# canonical path, so offering it back adds nothing. One named constant, so a deliberate
# product divergence from the workflow cannot grow silently into an unnoticed omission.
COUNTS_PICKER_EXCLUSIONS = {"count_matrix"}


def _window(theme: str = "light", size: tuple[int, int] = (1366, 768)) -> MainWindow:
    app = QApplication.instance() or QApplication([])
    QSettings().setValue("theme_mode", theme)
    apply_theme(app, theme)
    window = MainWindow()
    window.resize(*size)
    window.show()
    app.processEvents()
    return window


def _accepted_de_engines() -> tuple[str, ...]:
    """Engine values the Snakefile accepts for workflow.de_engine."""
    text = SNAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^_VALID_DE_ENGINES\s*=\s*\(([^)]*)\)", text, re.M)
    assert match, "the Snakefile no longer declares _VALID_DE_ENGINES"
    engines = tuple(re.findall(r'"([^"]+)"', match.group(1)))
    assert len(engines) > 1
    return engines


def _alt_de_engines() -> set[str]:
    """Engines ALT_DE_MODE covers, resolved through the *_MODE names it is built from.

    Derived rather than listed so a third alternative engine joining the ``or`` chain
    is picked up here instead of silently diverging from the GUI.
    """
    text = SNAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^ALT_DE_MODE\s*=\s*(.+)$", text, re.M)
    assert match, "the Snakefile no longer declares ALT_DE_MODE"
    mode_names = re.findall(r"\b([A-Z0-9_]+_MODE)\b", match.group(1))
    assert mode_names, "ALT_DE_MODE is no longer a combination of *_MODE flags"
    engines = set()
    for mode in mode_names:
        flag = re.search(rf'^{mode}\s*=\s*DE_ENGINE\s*==\s*"([^"]+)"', text, re.M)
        assert flag, f"{mode} is no longer defined as a DE_ENGINE equality"
        engines.add(flag.group(1))
    return engines


def _accepted_input_routes() -> tuple[str, ...]:
    """Route values the configuration accepts for input.type."""
    routes = get_args(InputConfig.model_fields["type"].annotation)
    assert len(routes) > 2, routes
    return routes


def _guard_modes(anchor: str) -> list[str]:
    """The *_MODE flags of every block enclosing a Snakefile target declaration.

    Anchored on the target line and walked outwards by indentation: the guard text
    ``not (MICROARRAY_MODE or DE_RESULTS_MODE)`` also appears for META_MODE and in
    reports.smk, so matching the condition alone would resolve the wrong block.
    """
    lines = SNAKEFILE.read_text(encoding="utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if anchor in line]
    assert len(hits) == 1, f"the Snakefile no longer declares exactly one: {anchor}"
    indent = len(lines[hits[0]]) - len(lines[hits[0]].lstrip())
    modes: list[str] = []
    for line in reversed(lines[:hits[0]]):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        here = len(line) - len(line.lstrip())
        if here >= indent:
            continue
        indent = here
        assert stripped.startswith("if not "), f"unexpected enclosing block: {stripped}"
        modes += re.findall(r"\b([A-Z0-9_]+_MODE)\b", stripped)
        if here == 4:  # function body: the outermost guard on the target
            break
    assert modes, f"the target is no longer guarded by *_MODE flags: {anchor}"
    return modes


def _guard_sets(anchor: str) -> tuple[set[str], set[str]]:
    """(routes, engines) the Snakefile withholds the anchored target for.

    Each guarding flag is resolved through the Snakefile to the input.type or
    de_engine value it tests, following one level of alias (_DE_RESULTS_AT_PARSE).
    A flag that resolves to neither fails the assertion rather than being dropped.
    """
    text = SNAKEFILE.read_text(encoding="utf-8")
    routes: set[str] = set()
    engines: set[str] = set()
    pending = list(_guard_modes(anchor))
    seen: set[str] = set()
    while pending:
        flag = pending.pop()
        if flag in seen:
            continue
        seen.add(flag)
        declared = re.search(rf"^{flag}\s*=\s*(.+)$", text, re.M)
        assert declared, f"the Snakefile no longer declares {flag}"
        expr = declared.group(1)
        route = re.search(r'\.get\("type"\)\s*==\s*"([^"]+)"', expr)
        engine = re.search(r'DE_ENGINE\s*==\s*"([^"]+)"', expr)
        if route:
            routes.add(route.group(1))
        elif engine:
            engines.add(engine.group(1))
        else:
            names = re.findall(r"\b(_?[A-Z0-9_]+(?:_MODE|_AT_PARSE))\b", expr)
            assert names, f"{flag} tests neither input.type nor de_engine: {expr}"
            pending += names
    # Engines only appear for an engine-gated target; callers assert their own half.
    assert routes, (anchor, routes)
    return routes, engines


def _workflow_check_stems() -> set[str]:
    stems = set()
    for path in ROOT.joinpath("workflow").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".smk", ".R", ""} and path.name != "Snakefile.lock":
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            stems.update(re.findall(r"checks/([0-9]{2}_[A-Za-z0-9_]+)\.json", text))
    assert len(stems) > 10, "no phase-check identifiers were found in the workflow tree"
    return stems


def _load_html_report():
    spec = importlib.util.spec_from_file_location("make_html_report_parity", HTML_REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _picker_items(window: MainWindow) -> list[str]:
    return [window.output_table_pick.itemText(i) for i in range(window.output_table_pick.count())]


def _project(window: MainWindow, root: Path, engine: str = "DESeq2") -> None:
    config = default_config("engine-test", root)
    config.input.type = "sra"
    config.workflow.de_engine = engine
    window.project_root = root
    window.config = config


# --- GUI-1: the equivalence table follows the Snakefile's ALT_DE_MODE ---------


def test_unchanged_table_is_offered_exactly_for_the_engines_the_snakefile_produces_it_for(
    tmp_path: Path,
) -> None:
    excluded_routes, alt = _guard_sets(UNCHANGED_TARGET)
    accepted = _accepted_de_engines()
    accepted_routes = _accepted_input_routes()
    # Two independent derivations of the engine half must agree.
    assert alt == _alt_de_engines(), (alt, _alt_de_engines())
    assert alt < set(accepted) and alt, (alt, accepted)
    assert excluded_routes < set(accepted_routes) and excluded_routes, (
        excluded_routes, accepted_routes)
    window = _window()
    try:
        _project(window, tmp_path)
        for route in accepted_routes:
            window.config.input.type = route
            baseline: dict[str, list[str]] = {}
            for engine in accepted:
                window.config.workflow.de_engine = engine
                window._refresh_output_table_pick()
                items = _picker_items(window)
                produced = engine not in alt and route not in excluded_routes
                assert (UNCHANGED in items) is produced, (route, engine, items)
                baseline[engine] = [i for i in items if i != UNCHANGED]
            # Within one route, only the equivalence table differs between engines.
            assert len({tuple(v) for v in baseline.values()}) == 1, (route, baseline)
    finally:
        window.close()


def test_single_literal_engine_gate_fails_the_derived_expectation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the pre-change gate compared against one engine literal."""
    alt = _alt_de_engines()
    monkeypatch.setattr(main_window_module, "ALT_DE_ENGINES", ("limma-voom",))
    window = _window()
    try:
        _project(window, tmp_path, "edgeR")
        window._refresh_output_table_pick()
        items = _picker_items(window)
        assert "edgeR" in alt
        assert UNCHANGED in items  # the defect: edgeR still offered a DESeq2-only table
    finally:
        window.close()


def test_derived_route_set_follows_the_snakefile_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the route half is parsed, not a literal that happens to agree."""
    import sys

    module = sys.modules[__name__]
    text = SNAKEFILE.read_text(encoding="utf-8")
    guard = "    if not (MICROARRAY_MODE or DE_RESULTS_MODE):"
    assert text.count(guard + "\n") == 1, "the unchanged_genes guard text moved"
    edited = tmp_path / "Snakefile"
    edited.write_text(
        text.replace(guard, "    if not (MICROARRAY_MODE or DE_RESULTS_MODE or COUNT_MATRIX_MODE):"),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "SNAKEFILE", edited)
    routes, engines = _guard_sets(UNCHANGED_TARGET)
    assert routes == {"microarray", "deseq2_results", "count_matrix"}, routes
    assert engines == {*_alt_de_engines()}, engines


def test_counts_table_is_offered_exactly_for_the_routes_the_snakefile_produces_it_for(
    tmp_path: Path,
) -> None:
    """The counts.txt entry follows the Snakefile guard, less the deliberate exclusion."""
    excluded_routes, engines = _guard_sets(COUNTS_TARGET)
    assert not engines, engines  # the counts guard tests input.type only
    accepted_routes = set(_accepted_input_routes())
    assert excluded_routes < accepted_routes and excluded_routes, (
        excluded_routes, accepted_routes)
    produced = accepted_routes - excluded_routes
    # A deliberate exclusion must name a route the workflow really does write the file
    # for, and must not swallow every such route: strict, non-empty subset.
    assert COUNTS_PICKER_EXCLUSIONS < produced, (COUNTS_PICKER_EXCLUSIONS, produced)
    offered = produced - COUNTS_PICKER_EXCLUSIONS
    window = _window()
    try:
        _project(window, tmp_path)
        for route in sorted(accepted_routes):
            window.config.input.type = route
            window._refresh_output_table_pick()
            items = _picker_items(window)
            assert (COUNTS in items) is (route in offered), (route, items)
    finally:
        window.close()


def test_engine_tooltip_states_what_each_engine_produces() -> None:
    window = _window()
    try:
        tip = window.de_engine.toolTip()
        assert UNCHANGED in tip and "check 13" in tip
        for engine, test in main_window_module.DE_ENGINE_EFFECT_SIZE_TESTS.items():
            assert f"{engine}: {test}" in tip
        assert set(main_window_module.DE_ENGINE_EFFECT_SIZE_TESTS) == set(_accepted_de_engines())
        assert "check 23" in tip
    finally:
        window.close()


# --- GUI-2: the permanent version label --------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_status_bar_keeps_a_permanent_version_label_through_messages(theme: str) -> None:
    window = _window(theme, (900, 600))
    try:
        bar = window.statusBar()
        label = window.version_label
        assert label.parentWidget() is bar
        assert APP_VERSION in label.text()
        assert label.accessibleName().startswith("Application version")
        window.statusBar().showMessage("anything")
        QApplication.instance().processEvents()
        assert label.isVisible()
        assert APP_VERSION in label.text()
        assert label.accessibleName().startswith("Application version")
        assert bar.currentMessage() == "anything"
        # The hand-painted message must not run under the permanent widget.
        assert not bar.messageRect().intersects(label.geometry())
    finally:
        window.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_fixed_reserve_message_rect_overlaps_the_version_label(theme: str) -> None:
    """Negative control: the pre-change rect reserved a constant 24px for the grip."""
    window = _window(theme, (900, 600))
    try:
        bar = window.statusBar()
        rect = bar.contentsRect()
        rect.adjust(0, 0, -24, 0)
        assert rect.intersects(window.version_label.geometry())
    finally:
        window.close()


# --- GUI-3: the pre-run checks page ------------------------------------------


def _write_check(root: Path, stem: str, status: str, message: str) -> None:
    (root / "checks").mkdir(parents=True, exist_ok=True)
    (root / "checks" / f"{stem}.json").write_text(
        json.dumps({"check": stem, "status": status,
                    "messages": [{"status": status, "message": message}]}, indent=2),
        encoding="utf-8")


REVIEW_TEXT = "Sample S3 read orientation disagrees with the configured strandedness."


def test_checks_page_shows_a_plain_language_name_and_the_finding_text(tmp_path: Path) -> None:
    window = _window()
    try:
        _write_check(tmp_path, "21_strandedness_qc", "REVIEW_REQUIRED", REVIEW_TEXT)
        window.project_root = tmp_path
        window._update_sanity_state()
        rendered = window.sanity_text.toPlainText()
        assert "21 strandedness qc" in rendered
        assert "21_strandedness_qc" not in rendered
        assert REVIEW_TEXT in rendered
    finally:
        window.close()


def test_raw_identifier_renderer_fails_the_plain_language_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: without the display map the raw stem is the label."""
    monkeypatch.setattr(main_window_module, "pretty_check_name", lambda name: name)
    window = _window()
    try:
        _write_check(tmp_path, "21_strandedness_qc", "REVIEW_REQUIRED", REVIEW_TEXT)
        window.project_root = tmp_path
        window._update_sanity_state()
        assert "21_strandedness_qc" in window.sanity_text.toPlainText()
    finally:
        window.close()


def test_status_only_renderer_drops_the_finding_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the pre-change page listed statuses without the messages."""
    monkeypatch.setattr(MainWindow, "_check_messages", lambda self, name: [])
    window = _window()
    try:
        _write_check(tmp_path, "21_strandedness_qc", "REVIEW_REQUIRED", REVIEW_TEXT)
        window.project_root = tmp_path
        window._update_sanity_state()
        assert REVIEW_TEXT not in window.sanity_text.toPlainText()
    finally:
        window.close()


def test_app_and_report_name_every_phase_check_identically() -> None:
    report = _load_html_report()
    stems = _workflow_check_stems()
    assert set(main_window_module.CHECK_DISPLAY_NAMES) <= stems
    # Both sides fall back to the same underscore substitution, so only the specially
    # named checks discriminate. Assert they are in the derived set or the loop below
    # compares identical fallbacks and can never fail.
    assert set(main_window_module.CHECK_DISPLAY_NAMES) & stems
    for stem in sorted(stems):
        assert main_window_module.pretty_check_name(stem) == report._pretty_check_name(stem), stem


def test_divergent_display_name_breaks_report_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: a GUI-only relabelling must not pass the parity check."""
    report = _load_html_report()
    stem = sorted(_workflow_check_stems())[0]
    monkeypatch.setattr(main_window_module, "CHECK_DISPLAY_NAMES",
                        {**main_window_module.CHECK_DISPLAY_NAMES, stem: "a nicer label"})
    assert main_window_module.pretty_check_name(stem) != report._pretty_check_name(stem)


def _configure_current_project(window: MainWindow, root: Path, status: str) -> None:
    import yaml

    config = default_config("gate-test", root)
    config.input.type = "count_matrix"
    config.input.count_matrix = "inputs/counts.tsv"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "counts.tsv").write_text(
        "gene_id\tS1\tS2\nGENE1\t10\t12\n", encoding="utf-8")
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\nS1\tcontrol\nS2\ttreated\n", encoding="utf-8")
    (root / "config" / "config.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    window.project_root = root
    window.config = config
    write_input_validation_with_fingerprint(
        root,
        {"check": "01_input_validation", "status": status,
         "messages": [{"status": status, "message": "Synthetic gate finding"}]},
    )


def test_launch_gate_still_reads_only_the_input_validation_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: refused.append(str(args[2])))
    window = _window()
    try:
        _configure_current_project(window, tmp_path, "REVIEW_REQUIRED")
        _write_check(tmp_path, "21_strandedness_qc", "REVIEW_REQUIRED", REVIEW_TEXT)
        window._update_sanity_state({"01_input_validation": "REVIEW_REQUIRED"})
        assert window._run_gate_ok() is False
        assert refused and "review-required" in refused[-1]

        # Only a downstream check under review: the gate reads check 01 and allows launch.
        _configure_current_project(window, tmp_path, "PASS")
        refused.clear()
        assert window._run_gate_ok() is True
        assert not refused
        assert window._phase_check_statuses()["21_strandedness_qc"] == "REVIEW_REQUIRED"
    finally:
        window.close()


def test_completed_run_reloads_the_phase_checks_and_counts_the_findings(
    tmp_path: Path,
) -> None:
    window = _window()
    try:
        _write_check(tmp_path, "21_strandedness_qc", "REVIEW_REQUIRED", REVIEW_TEXT)
        _write_check(tmp_path, "09_deseq2_qc", "WARNING", "Few genes pass the threshold.")
        _write_check(tmp_path, "05_reference_validation", "PASS", "Reference matches the annotation.")
        window.project_root = tmp_path
        window._run_mode = "run"
        window._stop_in_progress = False
        window._run_error_detected = False
        window._active_estimate = None
        window._on_run_finished(0)
        QApplication.instance().processEvents()

        message = window.statusBar().currentMessage()
        assert "Explore results > Figures and tables" in message
        assert "2 phase checks reported warnings or review-required findings" in message
        rendered = window.sanity_text.toPlainText()
        assert "09 differential-expression QC" in rendered
        assert REVIEW_TEXT in rendered
    finally:
        worker = getattr(window, "_phase_refresh_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(5000)
        window.close()
