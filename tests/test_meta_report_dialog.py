from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"
RULES = ROOT / "workflow" / "rules" / "reports.smk"
sys.path.insert(0, str(SCRIPTS))
MAIN = importlib.import_module("make_html_report")
META = importlib.import_module("make_meta_report")


def _write_meta_project(project: Path) -> None:
    reports = project / "results" / "reports"
    meta = project / "results" / "meta"
    figures = project / "results" / "figures"
    reports.mkdir(parents=True)
    meta.mkdir(parents=True)
    figures.mkdir(parents=True)
    (reports / "run_summary.json").write_text(json.dumps({"app_version": "test"}), encoding="utf-8")
    (reports / "meta_analysis_summary.json").write_text(json.dumps({
        "n_studies": 2,
        "n_shared_genes": 4,
        "n_meta_sig": 2,
        "n_sig_up": 1,
        "n_sig_down": 1,
        "n_discordant": 0,
        "direction_concordance_pct": 100,
        "pooling": "fixed-effect",
    }), encoding="utf-8")
    (meta / "meta_convergent_genes.csv").write_text(
        "gene_id,gene_symbol,log2FC,padj\nGeneA,A,1.5,0.001\nGeneB,B,-1.2,0.003\n",
        encoding="utf-8",
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1200" '
        'viewBox="0 0 1600 1200"><rect width="1600" height="1200" fill="white"/>'
        '<rect x="50" y="50" width="1500" height="1100" fill="none" stroke="black" '
        'stroke-width="10"/><text x="130" y="240" font-size="96">Synthetic meta figure</text></svg>'
    )
    for name in ("meta_volcano", "meta_forest"):
        (figures / f"{name}.svg").write_text(svg, encoding="utf-8")


def test_meta_report_distinguishes_adjustment_families_and_legacy_results(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    meta = tmp_path / "results" / "meta"
    reports = tmp_path / "results" / "reports"
    summary = json.loads((reports / "meta_analysis_summary.json").read_text(encoding="utf-8"))
    summary["alpha"] = 0.05
    summary["n_meta_sig"] = 1
    (reports / "meta_analysis_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (meta / "meta_analysis_results.csv").write_text(
        "gene_id,combined_padj,rem_log2FC,combined_pvalue,meta_sig,common_direction,study_A_log2FC,study_B_log2FC\n"
        "g1,0.02,0.5,0.01,TRUE,up,0.3,0.7\n"
        "g2,NA,0.2,0.3,FALSE,discordant,-0.1,0.5\n",
        encoding="utf-8",
    )
    (meta / "per_study_A.csv").write_text("gene_id,log2FoldChange\ng1,0.3\n", encoding="utf-8")
    (meta / "per_study_B.csv").write_text("gene_id,log2FoldChange\ng1,0.7\n", encoding="utf-8")
    figure_svg = (tmp_path / "results" / "figures" / "meta_volcano.svg").read_text(encoding="utf-8")
    for name in ("meta_concordance_scatter", "meta_integration_gain"):
        (tmp_path / "results" / "figures" / f"{name}.svg").write_text(figure_svg, encoding="utf-8")
    (meta / "meta_convergent_genes.csv").write_text(
        "gene_id,gene_symbol,n_studies_sig,combined_padj,rem_padj\ng1,G1,0,0.02,0.1\n", encoding="utf-8")
    ledger = {
        "method": META.METHOD,
        "families": {
            "combined": {"id": "direction_concordant_combined_bh", "size": 1},
            "pooled": {"id": "all_estimable_pooled_bh", "size": 2},
        },
        "execution": {"combined_alpha": 0.05},
        "genes": {
            "identifier_intersection": 3, "exclusion_union": 1,
            "complete_case_retained": 2, "pooled_fit_failures": 0,
            "direction": {"up": 1, "down": 0, "opposite_sign": 1, "neutral": 0},
        },
    }
    (meta / "meta_eligibility.json").write_text(json.dumps(ledger), encoding="utf-8")
    rendered = META.build(tmp_path)
    assert "Combined-p BH: 1 matching-sign test" in rendered
    assert "Pooled-effect BH: 2 estimable tests across all directions" in rendered
    assert "Legacy pooled-effect adjustment requires recomputation" not in rendered
    assert "run FDR &lt; 0.05" in rendered
    assert "<th scope='col'>rem_padj</th>" in rendered
    assert "1 of 2 retained rows appear in the bars" in rendered
    assert "A versus B: 2 plotted points" in rendered
    assert "../../results/meta/meta_analysis_results.csv" in rendered
    assert "../../results/meta/per_study_A.csv" in rendered

    (meta / "meta_eligibility.json").unlink()
    legacy = META.build(tmp_path)
    assert "Legacy pooled-effect adjustment requires recomputation" in legacy
    assert "Pooled-effect BH: 2" not in legacy
    assert "<th scope='col'>rem_padj</th>" not in legacy


def test_meta_report_empty_result_uses_recorded_loss_reason(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    meta = tmp_path / "results" / "meta"
    reports = tmp_path / "results" / "reports"
    (reports / "meta_analysis_summary.json").write_text(
        json.dumps({"n_shared_genes": 0, "n_meta_sig": 0}), encoding="utf-8")
    (meta / "meta_eligibility.json").write_text(json.dumps({
        "method": META.METHOD,
        "genes": {"identifier_intersection": 4, "complete_case_retained": 0},
    }), encoding="utf-8")
    rendered = META.build(tmp_path)
    assert "Shared identifiers exist, but no complete-case" in rendered
    assert "No shared genes across studies" not in rendered


def test_meta_report_leads_with_executed_comparison_and_study_set(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    meta = tmp_path / "results" / "meta"
    ledger = {
        "method": META.METHOD,
        "families": {
            "combined": {"id": "direction_concordant_combined_bh", "size": 2},
            "pooled": {"id": "all_estimable_pooled_bh", "size": 4},
        },
        "execution": {"contrast_factor": "treatment", "numerator": "drug", "denominator": "vehicle",
                      "per_study_formula": "~ treatment", "requested_meta_design_formula": "~ treatment",
                      "dataset_column": "dataset", "combined_alpha": 0.05},
        "studies": {
            "input": [
                {"study": "A", "numerator_samples": 2, "denominator_samples": 3},
                {"study": "B", "numerator_samples": 2, "denominator_samples": 2},
            ],
            "included": [{"study": "A", "post_filter_rows": 5}],
            "excluded": [{"study": "B", "reason": "one arm not replicated"}],
        },
    }
    (meta / "meta_eligibility.json").write_text(json.dumps(ledger), encoding="utf-8")
    rendered = META.build(tmp_path)
    assert rendered.index("Cross-study analysis: drug versus vehicle (treatment)") < rendered.index("Multi-study meta-analysis")
    assert "Positive log2 fold change means higher expression in drug than vehicle." in rendered
    assert "A (2 drug, 3 vehicle; 5 post-filter gene rows)" in rendered
    assert "B (2 drug, 2 vehicle; one arm not replicated)" in rendered
    assert "The recorded per-study formula was <code>~ treatment</code>" in rendered
    assert '<details id=\'field-guide\'><summary>How to read the result fields</summary>' in rendered
    assert '<details id=\'methods\'><summary>Testing families and retained rows</summary>' in rendered
    assert "comparison orientation not recorded" not in rendered.lower()


def test_recorded_result_alpha_is_not_replaced_by_figure_setting(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    reports = tmp_path / "results" / "reports"
    meta = tmp_path / "results" / "meta"
    summary = json.loads((reports / "meta_analysis_summary.json").read_text(encoding="utf-8"))
    summary["alpha"] = 0.1
    (reports / "meta_analysis_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (meta / "meta_analysis_results.csv").write_text(
        "gene_id,meta_sig,study_A_padj,study_B_padj,study_A_log2FC,study_B_log2FC\n"
        "g1,TRUE,0.08,0.2,0.6,0.7\n", encoding="utf-8")
    (meta / "meta_convergent_genes.csv").write_text(
        "gene_id,gene_symbol,n_studies_sig\ng1,G1,0\n", encoding="utf-8")
    (meta / "meta_eligibility.json").write_text(json.dumps({
        "method": META.METHOD,
        "families": {
            "combined": {"id": "direction_concordant_combined_bh", "size": 1},
            "pooled": {"id": "all_estimable_pooled_bh", "size": 1},
        },
        "execution": {"combined_alpha": 0.05},
    }), encoding="utf-8")
    figure_svg = (tmp_path / "results" / "figures" / "meta_volcano.svg").read_text(encoding="utf-8")
    (tmp_path / "results" / "figures" / "meta_integration_gain.svg").write_text(figure_svg, encoding="utf-8")
    rendered = META.build(tmp_path)
    assert "recorded 0.05 threshold" in rendered
    assert "run FDR &lt; 0.05" in rendered
    assert "figure-setting alpha 0.1 differs from the recorded result-call alpha 0.05" in rendered


@pytest.mark.parametrize("ledger", [
    [], None, 3, {"families": [1]},
    {"method": META.METHOD, "families": {"combined": [1], "pooled": {"id": "all_estimable_pooled_bh", "size": 2}}},
    {"method": META.METHOD, "families": {"combined": {"id": "direction_concordant_combined_bh", "size": 1}, "pooled": None}},
    {"method": META.METHOD, "families": {"combined": {"id": "direction_concordant_combined_bh", "size": 1},
                                          "pooled": {"id": "all_estimable_pooled_bh", "size": 1}},
     "genes": [1]},
    {"method": META.METHOD, "families": {"combined": {"id": "direction_concordant_combined_bh", "size": 1},
                                          "pooled": {"id": "all_estimable_pooled_bh", "size": 1}},
     "studies": [1]},
])
def test_malformed_ledger_warns_in_both_reports(tmp_path: Path, ledger) -> None:
    _write_meta_project(tmp_path)
    (tmp_path / "results" / "meta" / "meta_eligibility.json").write_text(json.dumps(ledger), encoding="utf-8")
    dedicated = META.build(tmp_path)
    main = MAIN._meta_analysis_link(tmp_path)
    assert "Legacy pooled-effect adjustment requires recomputation" in dedicated
    assert "Legacy pooled-effect adjustment requires recomputation" in main
    assert "Comparison orientation not recorded" in dedicated


def test_meta_report_main_link_tracks_stored_file_without_rule_dependency(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    html_without = META.build(tmp_path)
    assert "href='results_report.html'" not in html_without
    assert "main results report is unavailable" in html_without
    (tmp_path / "results" / "reports" / "results_report.html").write_text("<html></html>", encoding="utf-8")
    assert "href='results_report.html'" in META.build(tmp_path)
    source = RULES.read_text(encoding="utf-8")
    rule = source[source.index("rule meta_report:"):]
    assert '"per_study_manifest": "results/meta/per_study/manifest.json"' in source
    assert 'results/reports/results_report.html' not in rule


@pytest.mark.parametrize("payload", [
    {"check": "17_meta_analysis_qc", "status": "FAIL",
     "messages": [{"status": "PASS", "message": "child passed"}]},
    {"check": "17_meta_analysis_qc", "status": "PASS",
     "messages": [{"status": "PASS", "message": "first passed"},
                  {"status": "FAIL", "message": "later failed"}]},
    {"check": "17_meta_analysis_qc", "status": "PASS", "messages": [1]},
    {"check": "17_meta_analysis_qc", "status": "PASS", "messages": None},
])
def test_meta_report_uses_canonical_check_severity(tmp_path: Path, payload) -> None:
    _write_meta_project(tmp_path)
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "17_meta_analysis_qc.json").write_text(json.dumps(payload), encoding="utf-8")
    rendered = META.build(tmp_path)
    hero = rendered[rendered.index("id='findings'"):]
    assert "badge fail'>FAIL" in hero.split("</section>", 1)[0]
    assert "badge ok'>PASS" not in hero.split("</section>", 1)[0]
    if not isinstance(payload["messages"], list) or not all(isinstance(item, dict) for item in payload["messages"]):
        assert "Meta check evidence is unavailable or malformed" in rendered


def test_missing_meta_check_is_reported_as_failed_evidence(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    rendered = META.build(tmp_path)
    assert "Meta check evidence is unavailable or malformed" in rendered
    assert "badge fail'>FAIL" in rendered
    assert str(tmp_path) not in rendered


def test_main_report_sanity_summary_does_not_lower_child_failure() -> None:
    overall, checks = MAIN._parse_sanity(
        "Overall: PASS\n17_meta_analysis_qc: PASS\n  - PASS: initial review\n  - FAIL: later failure\n")
    assert overall == "FAIL" and checks[0]["status"] == "FAIL"
    assert MAIN._parse_sanity("Overall: FAIL\n17_meta_analysis_qc: PASS\n  - PASS: child\n")[0] == "FAIL"
    assert MAIN._parse_sanity("Overall: PASS\n")[0] == "FAIL"


@pytest.mark.parametrize("mode", ("script", "spec", "package"))
def test_report_imports_its_sibling_check_contract_in_fresh_process(tmp_path: Path, mode: str) -> None:
    source = SCRIPTS / "make_html_report.py"
    if mode == "script":
        command = [sys.executable, "-I", str(source), "--help"]
    elif mode == "spec":
        code = (
            "import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('isolated_html_report', {str(source)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "assert module.read_check and module.PRIORITY['FAIL'] > module.PRIORITY['PASS']\n"
            "print('IMPORT_OK')\n"
        )
        command = [sys.executable, "-I", "-c", code]
    else:
        code = (
            "import importlib, sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "module = importlib.import_module('workflow.scripts.make_html_report')\n"
            "assert module.read_check and module.PRIORITY['FAIL'] > module.PRIORITY['PASS']\n"
            "print('IMPORT_OK')\n"
        )
        command = [sys.executable, "-I", "-c", code]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("usage:" if mode == "script" else "IMPORT_OK") in result.stdout


def test_meta_report_reuses_the_main_native_dialog_component(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    main = MAIN.build(tmp_path)
    meta = META.build(tmp_path)

    assert MAIN.figure_dialog() in main
    assert MAIN.figure_dialog() in meta
    assert '<div id="bsq-lb" class="lb" role="dialog"' not in meta
    assert "classList.add('open')" not in meta
    assert "lb.showModal()" in meta
    assert "lb.addEventListener('close'" in meta


def test_meta_report_rule_tracks_both_generator_sources() -> None:
    source = RULES.read_text(encoding="utf-8")
    meta_rule = source[source.index("rule meta_report:"):]
    required = (
        'generator="workflow/scripts/make_meta_report.py"',
        'shared_dialog_generator="workflow/scripts/make_html_report.py"',
    )
    for item in required:
        assert item in meta_rule

    damaged = meta_rule.replace(required[1], "", 1)
    assert damaged != meta_rule
    with pytest.raises(AssertionError):
        assert required[1] in damaged


def _assert_sortable_table_contract(main_source: str, meta_source: str) -> None:
    assert "def sortable_table_script()" in main_source
    assert "{sortable_table_script()}" in main_source
    assert "{R.sortable_table_script()}" in meta_source
    assert "th.setAttribute('role','button')" not in main_source
    assert "header.appendChild(button)" in main_source
    assert "button.type='button'" in main_source
    assert "button.addEventListener('click'" in main_source
    assert "header.setAttribute('aria-sort'" in main_source
    assert "other.removeAttribute('aria-sort')" in main_source
    assert ".sort-button:focus-visible" in main_source
    assert "<th scope='col'>{html.escape(h)}</th>" in meta_source
    assert "th.tabIndex=0" not in meta_source


def test_sortable_table_headers_keep_semantics_and_reject_the_legacy_role_override() -> None:
    main_source = (SCRIPTS / "make_html_report.py").read_text(encoding="utf-8")
    meta_source = (SCRIPTS / "make_meta_report.py").read_text(encoding="utf-8")
    _assert_sortable_table_contract(main_source, meta_source)

    legacy_main = main_source.replace(
        "header.appendChild(button)",
        "header.setAttribute('role','button')",
        1,
    )
    assert legacy_main != main_source
    with pytest.raises(AssertionError):
        _assert_sortable_table_contract(legacy_main, meta_source)


def test_key_value_report_tables_use_row_headers() -> None:
    machine = MAIN._timing_section({"detected_resources": {"os": "synthetic Linux"}})
    imported = MAIN._study_design_section({"input": {"type": "deseq2_results"}})
    configuration = MAIN._study_design_section({
        "deseq2": {"design_formula": "~ condition"},
        "reference": {"mode": "synthetic"},
    })
    for rendered in (machine, imported, configuration):
        assert "<th scope='row'>" in rendered


def test_generated_reports_sort_with_native_header_buttons(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    deseq2 = tmp_path / "results" / "deseq2"
    deseq2.mkdir()
    table = (
        "gene_id,symbol,log2FoldChange,padj,baseMean\nGeneB,B,1.5,0.001,12\n"
        "GeneA,A,1.5,0.003,3\nGeneC,A,-1.2,0.004,6\nGeneD,D,0,0.005,1200\n"
        "GeneE,E,0,0.006,2\n"
    )
    for name in ("deseq2_results.csv", "upregulated_genes.csv", "downregulated_genes.csv"):
        (deseq2 / name).write_text(table, encoding="utf-8")
    (tmp_path / "results" / "meta" / "meta_convergent_genes.csv").write_text(
        "gene_id,gene_symbol,log2FC,padj\nGeneB,B,1.5,0.001\n"
        "GeneA,A,1.5,0.003\nGeneC,A,-1.2,0.004\nGeneD,D,0,0.005\nGeneE,E,0,0.006\n",
        encoding="utf-8",
    )
    reports = tmp_path / "results" / "reports"
    main_report = reports / "results_report.html"
    meta_report = reports / "meta_analysis_report.html"
    main_report.write_text(MAIN.build(tmp_path), encoding="utf-8")
    meta_report.write_text(META.build(tmp_path), encoding="utf-8")
    probe = textwrap.dedent(
        r'''
        import json
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView

        REPORTS = __REPORTS__
        app = QApplication.instance() or QApplication([])
        view = QWebEngineView()
        view.resize(900, 700)
        view.show()

        def load(uri):
            loaded = []
            loop = QEventLoop()
            def done(ok):
                loaded.append(bool(ok))
                loop.quit()
            view.loadFinished.connect(done)
            QTimer.singleShot(8000, loop.quit)
            view.load(QUrl(uri))
            loop.exec()
            view.loadFinished.disconnect(done)
            assert loaded == [True], loaded

        def js(expression):
            values = []
            wait = QEventLoop()
            view.page().runJavaScript(
                "JSON.stringify(" + expression + ")",
                lambda value: (values.append(value), wait.quit()),
            )
            QTimer.singleShot(3000, wait.quit)
            wait.exec()
            assert values, expression
            assert values[0], (expression, values)
            return json.loads(values[0])

        def key(value):
            target = view.focusProxy() or app.focusWidget() or view
            QTest.keyClick(target, value, delay=20)
            QTest.qWait(80)

        results = []
        for uri in REPORTS:
            load(uri)
            initial = js("""(() => { const header = document.querySelector('table.sortable thead th');
                const button = header.querySelector('button.sort-button');
                return {scope: header.getAttribute('scope'), role: header.getAttribute('role'),
                    buttons: header.querySelectorAll('button').length,
                    buttonType: button && button.type, sort: header.getAttribute('aria-sort')}; })()""")
            clicked = js("""(() => { const header = document.querySelector('table.sortable thead th');
                const button = header.querySelector('button.sort-button'); button.focus(); button.click();
                return {focused: document.activeElement === button, sort: header.getAttribute('aria-sort'),
                    rows: Array.from(header.closest('table').tBodies[0].rows, row => row.cells[0].textContent) }; })()""")
            key(Qt.Key.Key_Return)
            entered = js("""(() => { const header = document.querySelector('table.sortable thead th');
                return {sort: header.getAttribute('aria-sort'), rows: Array.from(header.closest('table').tBodies[0].rows,
                    row => row.cells[0].textContent)}; })()""")
            key(Qt.Key.Key_Space)
            spaced = js("""(() => { const header = document.querySelector('table.sortable thead th');
                return {sort: header.getAttribute('aria-sort'), rows: Array.from(header.closest('table').tBodies[0].rows,
                    row => row.cells[0].textContent)}; })()""")
            js("""(() => { const button = document.querySelector('table.sortable thead th button');
                button.click(); button.click(); return true; })()""")
            switched = js("""(() => { const header = document.querySelectorAll('table.sortable thead th')[1];
                header.querySelector('button.sort-button').click();
                return {sort: header.getAttribute('aria-sort'), rows: Array.from(header.closest('table').tBodies[0].rows,
                    row => row.cells[0].textContent)}; })()""")
            numeric = js("""(() => { const headers = Array.from(document.querySelectorAll('table.sortable thead th'));
                const header = headers.find(item => item.textContent.trim() === 'baseMean'); if (!header) return null;
                header.querySelector('button.sort-button').click(); const table = header.closest('table');
                const geneD = Array.from(table.tBodies[0].rows).find(row => row.cells[0].textContent === 'GeneD');
                return {rows: Array.from(table.tBodies[0].rows, row => row.cells[0].textContent),
                    displayed: geneD.cells[headers.indexOf(header)].textContent,
                    value: geneD.cells[headers.indexOf(header)].getAttribute('data-sort-value')}; })()""")
            results.append({"initial": initial, "clicked": clicked, "entered": entered, "spaced": spaced,
                            "switched": switched, "numeric": numeric})
        print(json.dumps(results, sort_keys=True))
        view.close()
        app.processEvents()
        '''
    ).replace("__REPORTS__", json.dumps([main_report.as_uri(), meta_report.as_uri()]))
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=40, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert len(result) == 2
    for report in result:
        assert report["initial"] == {
            "scope": "col", "role": None, "buttons": 1, "buttonType": "button", "sort": None,
        }
        assert report["clicked"] == {"focused": True, "sort": "ascending", "rows": ["GeneA", "GeneB", "GeneC", "GeneD", "GeneE"]}
        assert report["entered"] == {"sort": "descending", "rows": ["GeneE", "GeneD", "GeneC", "GeneB", "GeneA"]}
        assert report["spaced"] == {"sort": None, "rows": ["GeneB", "GeneA", "GeneC", "GeneD", "GeneE"]}
        assert report["switched"] == {"sort": "ascending", "rows": ["GeneC", "GeneA", "GeneB", "GeneD", "GeneE"]}
    assert result[0]["numeric"] == {
        "rows": ["GeneE", "GeneA", "GeneC", "GeneB", "GeneD"], "displayed": "1,200", "value": "1200.0",
    }
    assert result[1]["numeric"] is None


def test_meta_report_native_dialog_in_the_offscreen_browser(tmp_path: Path) -> None:
    _write_meta_project(tmp_path)
    report = tmp_path / "results" / "reports" / "meta_analysis_report.html"
    report.write_text(META.build(tmp_path), encoding="utf-8")
    probe = textwrap.dedent(
        r'''
        import json
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView

        REPORT_URI = __REPORT_URI__
        app = QApplication.instance() or QApplication([])
        view = QWebEngineView()
        view.resize(360, 720)
        view.show()
        loaded = []
        loop = QEventLoop()
        view.loadFinished.connect(lambda ok: (loaded.append(bool(ok)), loop.quit()))
        QTimer.singleShot(8000, loop.quit)
        view.load(QUrl(REPORT_URI))
        loop.exec()
        assert loaded == [True], loaded

        def js(expression):
            values = []
            wait = QEventLoop()
            view.page().runJavaScript(
                "JSON.stringify(" + expression + ")",
                lambda value: (values.append(value), wait.quit()),
            )
            QTimer.singleShot(3000, wait.quit)
            wait.exec()
            assert values, expression
            return json.loads(values[0])

        def key(value, modifiers=Qt.KeyboardModifier.NoModifier):
            target = view.focusProxy() or app.focusWidget() or view
            QTest.keyClick(target, value, modifiers, delay=20)
            QTest.qWait(80)

        initial = js("""(() => {
            const dialog = document.getElementById('bsq-lb');
            return {open: dialog.open, display: getComputedStyle(dialog).display};
        })()""")
        opened = js("""(() => {
            const trigger = document.querySelector('.figbtn');
            trigger.click();
            const dialog = document.getElementById('bsq-lb');
            return {open: dialog.open, title: document.getElementById('bsq-lb-title').textContent,
                    source: document.getElementById('bsq-lb-img').getAttribute('src'),
                    active: document.activeElement.id,
                    documentFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
                    dialogFits: dialog.getBoundingClientRect().width <= innerWidth};
        })()""")
        QTest.qWait(100)
        zoom = js("""(() => {
            document.getElementById('bsq-lb-in').click();
            const image = document.getElementById('bsq-lb-img');
            return {percent: document.getElementById('bsq-lb-percent').textContent,
                    width: image.style.width, zoomed: image.classList.contains('zoomed')};
        })()""")
        fit = js("""(() => {
            document.getElementById('bsq-lb-fit').click();
            const image = document.getElementById('bsq-lb-img');
            return {percent: document.getElementById('bsq-lb-percent').textContent,
                    zoomed: image.classList.contains('zoomed')};
        })()""")
        key(Qt.Key.Key_Tab)
        tab = js("document.activeElement.id")
        contained = js("""(() => {
            document.querySelectorAll('.figbtn')[1].focus();
            const dialog = document.getElementById('bsq-lb');
            return dialog.contains(document.activeElement);
        })()""")
        key(Qt.Key.Key_Escape)
        escaped = js("""(() => {
            const dialog = document.getElementById('bsq-lb');
            return {closed: !dialog.open, sourceCleared: !document.getElementById('bsq-lb-img').hasAttribute('src'),
                    focusReturned: document.activeElement === document.querySelector('.figbtn')};
        })()""")
        js("(() => { document.querySelector('.figbtn').click(); return true; })()")
        QTest.qWait(80)
        js("(() => { document.getElementById('bsq-lb-close').click(); return true; })()")
        QTest.qWait(80)
        closed = js("""(() => ({closed: !document.getElementById('bsq-lb').open,
            focusReturned: document.activeElement === document.querySelector('.figbtn')}))()""")
        print(json.dumps({"initial": initial, "opened": opened, "zoom": zoom, "fit": fit, "tab": tab,
                          "contained": contained, "escaped": escaped, "closed": closed}, sort_keys=True))
        view.close()
        app.processEvents()
        '''
    ).replace("__REPORT_URI__", json.dumps(report.as_uri()))
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["initial"] == {"open": False, "display": "none"}
    assert result["opened"]["open"]
    assert result["opened"]["title"].startswith("Combined evidence")
    assert result["opened"]["source"].startswith("data:image/svg+xml;base64,")
    assert result["opened"]["active"] == "bsq-lb-close"
    assert result["opened"]["documentFits"] and result["opened"]["dialogFits"], result["opened"]
    assert int(result["zoom"]["percent"].rstrip("%")) > 0
    assert result["zoom"]["width"] and result["zoom"]["zoomed"]
    assert int(result["fit"]["percent"].rstrip("%")) < int(result["zoom"]["percent"].rstrip("%"))
    assert not result["fit"]["zoomed"]
    assert result["tab"] == "bsq-lb-fit"
    assert result["contained"]
    assert result["escaped"] == {"closed": True, "sourceCleared": True, "focusReturned": True}
    assert result["closed"] == {"closed": True, "focusReturned": True}
