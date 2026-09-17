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
    assert result["opened"]["title"].startswith("Meta-volcano")
    assert result["opened"]["source"].startswith("data:image/svg+xml;base64,")
    assert result["opened"]["active"] == "bsq-lb-close"
    assert result["opened"]["documentFits"] and result["opened"]["dialogFits"]
    assert int(result["zoom"]["percent"].rstrip("%")) > 0
    assert result["zoom"]["width"] and result["zoom"]["zoomed"]
    assert int(result["fit"]["percent"].rstrip("%")) < int(result["zoom"]["percent"].rstrip("%"))
    assert not result["fit"]["zoomed"]
    assert result["tab"] == "bsq-lb-fit"
    assert result["contained"]
    assert result["escaped"] == {"closed": True, "sourceCleared": True, "focusReturned": True}
    assert result["closed"] == {"closed": True, "focusReturned": True}
