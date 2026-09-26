from __future__ import annotations

import csv
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

_GEN = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_html_report.py"


@pytest.fixture(scope="module")
def mhr():
    spec = importlib.util.spec_from_file_location("make_html_report", _GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_csv(path: Path, cols: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow(r)


# clusterProfiler enrichGO as.data.frame column names.
_CP_COLS = ["ID", "Description", "GeneRatio", "BgRatio", "pvalue", "p.adjust",
            "qvalue", "geneID", "Count", "FoldEnrichment"]
_CP_ROWS = [["GO:0006955", "immune response", "20/200", "150/12000", "1e-8", "3e-6",
             "2e-6", "A/B/C", 20, 10.4]]

# g:Profiler gost() result column names (atomic columns written on the non-model route).
_GP_COLS = ["query", "significant", "p_value", "term_size", "query_size",
            "intersection_size", "precision", "recall", "term_id", "source", "term_name"]
_GP_ROWS = [["q1", "TRUE", 3.1e-7, 150, 200, 20, 0.10, 0.13, "GO:0006955", "GO:BP", "immune response"]]


def test_clusterprofiler_ora_table_renders(mhr, tmp_path):
    p = tmp_path / "go_ora_all.csv"
    _write_csv(p, _CP_COLS, _CP_ROWS)
    html = mhr._enrich_block("GO", p, "ora")
    assert "<table" in html
    assert "immune response" in html
    for header in ("Description", "Fold enrichment", "Genes", "p.adjust"):
        assert header in html


def _assert_responsive_panel_contract(rendered: str, basename: str) -> None:
    slug = basename.replace("_", "-")
    assert f'<figure class="panel panel--{slug}" data-figure="{basename}"' in rendered
    assert '<div class="frame' in rendered
    assert 'class="figbtn"' in rendered
    assert '<div class="cap-lead">' in rendered


def _assert_dense_panel_contract(
        mhr, source: str, rendered: str, basename: str, title: str) -> None:
    _assert_responsive_panel_contract(rendered, basename)
    minimum = mhr.DENSE_FIGURE_MIN_WIDTH_PX
    slug = basename.replace("_", "-")
    assert minimum == 760
    assert basename in mhr.DENSE_FIGURE_BASENAMES
    assert 'data-panel-layout="dense"' in rendered
    assert ".panels>.panel[data-panel-layout=\"dense\"]{grid-column:1/-1;min-width:0;width:100%;max-width:100%}" in source
    assert "grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr))" in source
    assert '<div class="frame dense-figure-viewport" tabindex="0" role="region"' in rendered
    assert f'aria-label="Scrollable {title} preview"' in rendered
    assert f'aria-describedby="{slug}-scroll-hint"' in rendered
    assert f'data-min-content-width="{minimum}"' in rendered
    assert f'style="--dense-min-width:{minimum}px"' in rendered
    assert f'class="sr-only" id="{slug}-scroll-hint"' in rendered
    assert "Use Left and Right Arrow keys to inspect this dense figure preview." in rendered
    assert ".panel[data-panel-layout=\"dense\"] .dense-figure-viewport{overflow-x:auto;overflow-y:hidden;overscroll-behavior-inline:contain}" in source
    assert ".panel[data-panel-layout=\"dense\"] .dense-figure-viewport>.figbtn>img{min-width:var(--dense-min-width);max-width:none}" in source
    assert ".panel[data-panel-layout=\"dense\"] .dense-figure-viewport{overflow:visible!important;max-width:none!important}" in source
    assert "min-width:0!important;max-width:100%!important" in source


def _assert_compact_tooltip_contract(source: str) -> None:
    assert "display:none;pointer-events:none" in source
    assert '#term-tooltip{position:fixed' in source
    assert 'tip.textContent=btn.querySelector' in source
    assert "Math.min(r.left,innerWidth-t.width-12)" in source
    assert "Math.min(r.bottom+8,innerHeight-t.height-12)" in source
    assert "btn.addEventListener('focus'" in source
    assert "btn.addEventListener('click'" in source


def _assert_compact_report_and_modal_contract(source: str) -> None:
    hero_start = source.index(".hero h1{")
    hero_rule = source[hero_start : source.index("}", hero_start) + 1]
    assert "overflow-wrap:anywhere" in hero_rule
    assert ".enrichment-coverage li{overflow-wrap:anywhere}" in source
    assert "<div class='tablewrap'><table class='checks'>" in source
    assert "table.checks{border-collapse:collapse;width:100%;table-layout:fixed}" in source
    assert ".chk-msgs li{margin:.15rem 0;overflow-wrap:anywhere}" in source
    assert "@media(max-width:800px){.brand .rmeta{display:none}}" in source
    assert '<dialog id="bsq-lb" class="lb" aria-label="Figure viewer">' in source
    assert '<button id="bsq-lb-close" class="lb-close" type="button" autofocus ' in source
    assert "lb.showModal()" in source
    assert "lb.addEventListener('close'" in source
    assert "e.key!=='Tab'" in source
    assert "trigger&&trigger.isConnected" in source
    assert ".lb[open]{display:grid;grid-template-rows:auto minmax(0,1fr) auto}" in source
    assert ".lb-stage{overflow:auto;min-width:0;min-height:0" in source
    assert "li.naturalWidth*bsqScale" in source
    for control in ("fit", "actual", "in", "out", "percent"):
        assert f'id="bsq-lb-{control}"' in source
    assert "bsqResetZoom()" in source
    assert "li.removeAttribute('src')" in source
    assert '<div id="bsq-lb" class="lb" role="dialog"' not in source


def test_simple_report_figures_use_the_responsive_panel_component(mhr, tmp_path):
    (tmp_path / "example.png").write_bytes(b"synthetic")
    rendered = mhr._fig(tmp_path, "example", "Example result")
    _assert_responsive_panel_contract(rendered, "example")
    assert 'data-panel-layout="dense"' not in rendered
    assert "dense-figure-viewport" not in rendered

    broken = rendered.replace(' class="panel panel--example"', "", 1)
    with pytest.raises(AssertionError):
        _assert_responsive_panel_contract(broken, "example")


def test_ppi_report_panel_spans_the_responsive_figure_grid(mhr, tmp_path) -> None:
    (tmp_path / "ppi_network.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 1"></svg>',
        encoding="utf-8",
    )
    rendered = mhr._panel(tmp_path, "ppi_network", "A")
    source = _GEN.read_text(encoding="utf-8")
    _assert_dense_panel_contract(
        mhr, source, rendered, "ppi_network", "STRING protein-association network")

    old_three_column_failure = source.replace(
        ".panels>.panel[data-panel-layout=\"dense\"]{grid-column:1/-1;min-width:0;width:100%;max-width:100%}",
        ".panels>.panel[data-panel-layout=\"dense\"]{grid-column:auto}",
        1,
    )
    assert old_three_column_failure != source
    with pytest.raises(AssertionError):
        _assert_dense_panel_contract(
            mhr, old_three_column_failure, rendered,
            "ppi_network", "STRING protein-association network")


def _write_hub_csv(project: Path, rows: list[list], cols: list[str] | None = None) -> Path:
    networks = project / "results" / "networks"
    networks.mkdir(parents=True, exist_ok=True)
    _write_csv(networks / "ppi_hub_genes.csv",
               cols or ["symbol", "degree", "betweenness", "module", "log2FC"], rows)
    return networks / "ppi_hub_genes.csv"


def test_ppi_hub_table_backs_the_prioritise_hubs_caption(mhr, tmp_path) -> None:
    _write_hub_csv(tmp_path, [["FKBP5", 12, 340.5, 1, -2.31],
                              ["KLF15", 9, 88.0, 1, 1.07]])
    table = mhr._ppi_hub_table(tmp_path)
    assert "Most connected proteins (network hubs)" in table
    assert "tablewrap" in table and "<table class='data enr'>" in table
    assert "FKBP5" in table and "KLF15" in table
    # R writes the file already ordered by -degree; the report must not reorder it.
    assert table.index("FKBP5") < table.index("KLF15")
    assert "<td class='num'>12</td>" in table and "<td class='num'>340.5</td>" in table
    assert "results/networks/ppi_hub_genes.csv" in table

    # The figure group carries it beside the network panel.
    figs = tmp_path / "results" / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    (figs / "ppi_network.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 1"></svg>', encoding="utf-8")
    grouped = mhr._figure_groups(figs, 1, 1, "genes", project=tmp_path)
    assert "Most connected proteins (network hubs)" in grouped
    # Without the project (or the CSV) the section simply does not appear.
    assert "Most connected proteins" not in mhr._figure_groups(figs, 1, 1, "genes")


def test_ppi_hub_table_tolerates_a_degraded_header_only_file(mhr, tmp_path) -> None:
    # skip() in build_string_network.R writes symbol,degree with no rows.
    _write_hub_csv(tmp_path, [], cols=["symbol", "degree"])
    assert mhr._ppi_hub_table(tmp_path) == ""
    _write_hub_csv(tmp_path, [["A", 2]], cols=["symbol", "degree"])
    table = mhr._ppi_hub_table(tmp_path)
    assert "Betweenness" not in table and "<td>A</td>" in table
    assert mhr._ppi_hub_table(tmp_path / "missing") == ""


def test_volcano_report_panel_uses_the_same_derived_dense_contract(mhr, tmp_path) -> None:
    (tmp_path / "volcano.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 1"></svg>',
        encoding="utf-8",
    )
    rendered = mhr._panel(tmp_path, "volcano", "A")
    source = _GEN.read_text(encoding="utf-8")
    _assert_dense_panel_contract(mhr, source, rendered, "volcano", "Volcano plot")
    assert mhr.DENSE_FIGURE_BASENAMES == frozenset({"ppi_network", "volcano"})
    old_three_column_failure = source.replace(
        ".panels>.panel[data-panel-layout=\"dense\"]{grid-column:1/-1;min-width:0;width:100%;max-width:100%}",
        ".panels>.panel[data-panel-layout=\"dense\"]{grid-column:auto}",
        1,
    )
    assert old_three_column_failure != source
    with pytest.raises(AssertionError):
        _assert_dense_panel_contract(
            mhr, old_three_column_failure, rendered, "volcano", "Volcano plot")


def test_dense_viewports_reject_the_old_compact_image_collapse(mhr, tmp_path) -> None:
    for basename in mhr.DENSE_FIGURE_BASENAMES:
        (tmp_path / f"{basename}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 1"></svg>',
            encoding="utf-8",
        )
    source = _GEN.read_text(encoding="utf-8")
    old_compact_collapse = source.replace(
        ".panel[data-panel-layout=\"dense\"] .dense-figure-viewport>.figbtn>img{min-width:var(--dense-min-width);max-width:none}",
        ".panel[data-panel-layout=\"dense\"] .dense-figure-viewport>.figbtn>img{min-width:0;max-width:100%}",
        1,
    )
    assert old_compact_collapse != source
    for basename, title in (
        ("ppi_network", "STRING protein-association network"),
        ("volcano", "Volcano plot"),
    ):
        with pytest.raises(AssertionError):
            _assert_dense_panel_contract(
                mhr, old_compact_collapse,
                mhr._panel(tmp_path, basename, "A"), basename, title)


def test_panel_identity_is_safely_derived_from_the_figure_basename(mhr) -> None:
    assert mhr._panel_attrs("PPI network") == (
        'class="panel panel--ppi-network" data-figure="PPI network"'
    )
    assert mhr._panel_attrs('unsafe/&<"figure') == (
        'class="panel panel--unsafe-figure" data-figure="unsafe/&amp;&lt;&quot;figure"'
    )
    assert mhr._panel_attrs("volcano").endswith(' data-panel-layout="dense"')
    assert mhr._panel_attrs("ppi_network").endswith(' data-panel-layout="dense"')


def test_hidden_tooltips_do_not_widen_compact_reports() -> None:
    source = _GEN.read_text(encoding="utf-8")
    _assert_compact_tooltip_contract(source)
    broken = source.replace("display:none;pointer-events:none", "visibility:hidden", 1)
    with pytest.raises(AssertionError):
        _assert_compact_tooltip_contract(broken)


def test_compact_tooltips_reject_the_old_term_specific_edge_anchor() -> None:
    source = _GEN.read_text(encoding="utf-8")
    old_failure = source.replace(
        "Math.min(r.left,innerWidth-t.width-12)",
        "r.left",
        1,
    )
    assert old_failure != source
    with pytest.raises(AssertionError):
        _assert_compact_tooltip_contract(old_failure)


def test_glossary_tooltip_keeps_accessible_escaped_markup(mhr) -> None:
    rendered = mhr._term("basemean", "baseMean", '<unsafe & "quoted">')
    assert rendered.startswith('<button class="term" type="button">baseMean')
    assert '<span class="tip" role="tooltip">' in rendered
    assert '&lt;unsafe &amp; &quot;quoted&quot;&gt;' in rendered
    assert "<unsafe" not in rendered


def test_compact_report_uses_wrapping_sanity_scroller_and_native_dialog(mhr) -> None:
    source = _GEN.read_text(encoding="utf-8")
    _assert_compact_report_and_modal_contract(source)
    sanity = mhr._sanity_section(
        "Overall: WARNING\n09_deseq2_qc: WARNING\n- WARNING: " + "X" * 180 + "\n"
    )
    assert sanity.count("<div class='tablewrap'>") == 1
    assert "<table class='checks'>" in sanity


@pytest.mark.parametrize(
    ("old", "broken"),
    [
        ("overflow-wrap:anywhere", "overflow-wrap:normal"),
        (".enrichment-coverage li{overflow-wrap:anywhere}",
         ".enrichment-coverage li{overflow-wrap:normal}"),
        ("<div class='tablewrap'><table class='checks'>", "<table class='checks'>"),
        ("table.checks{border-collapse:collapse;width:100%;table-layout:fixed}",
         "table.checks{border-collapse:collapse;width:100%}"),
        ("@media(max-width:800px){.brand .rmeta{display:none}}",
         "@media(max-width:560px){.brand .rmeta{display:none}}"),
        ('<dialog id="bsq-lb"', '<div id="bsq-lb"'),
        ("lb.showModal()", "lb.setAttribute('open','')"),
        (".lb[open]{display:grid;grid-template-rows:auto minmax(0,1fr) auto}",
         ".lb[open]{display:flex;place-content:center}"),
        ("trigger&&trigger.isConnected", "false"),
    ],
)
def test_compact_report_contract_rejects_each_previous_failure(old: str, broken: str) -> None:
    source = _GEN.read_text(encoding="utf-8")
    damaged = source.replace(old, broken, 1)
    assert damaged != source
    with pytest.raises(AssertionError):
        _assert_compact_report_and_modal_contract(damaged)


def test_real_browser_compact_geometry_focus_and_zoom(mhr, tmp_path) -> None:
    project = tmp_path / ("pasilla-" + "x" * 64)
    reports = project / "results" / "reports"
    figures = project / "results" / "figures"
    enrichment = project / "results" / "enrichment"
    checks = project / "checks"
    reports.mkdir(parents=True)
    figures.mkdir(parents=True)
    enrichment.mkdir(parents=True)
    checks.mkdir(parents=True)
    (reports / "run_summary.json").write_text(
        json.dumps({
            "app_version": "test",
            "run_date": "2026-08-11T12:00:00+03:00",
            "input": {"type": "fastq"},
            "reference": {"organism_name": "Drosophila melanogaster"},
            "deseq2": {
                "alpha": 0.05,
                "lfc_threshold": 1,
                "contrasts": [{"numerator": "treated", "denominator": "control"}],
            },
        }),
        encoding="utf-8",
    )
    (checks / "sanity_checks.txt").write_text(
        "Overall: WARNING\n01_compact_width: WARNING\n- WARNING: " + "X" * 220 + "\n",
        encoding="utf-8",
    )
    (enrichment / "enrichment_summary.txt").write_text(
        "KEGG identity verification: PASS; source=" + "/very-long-unbroken-evidence-path/" * 20 + "\n",
        encoding="utf-8",
    )
    square_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="3200" '
        'viewBox="0 0 1600 3200"><rect width="1600" height="3200" fill="white"/>'
        '<path d="M0 0H1600V3200H0Z" fill="none" stroke="black" stroke-width="12"/></svg>'
    )
    for basename in ("pca", "sample_distance"):
        (figures / f"{basename}.svg").write_text(square_svg, encoding="utf-8")
    report = reports / "results_report.html"
    report.write_text(mhr.build(project), encoding="utf-8")

    probe = textwrap.dedent(
        r'''
        import json
        import sys
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView

        REPORT_URI = __REPORT_URI__
        app = QApplication.instance() or QApplication([])
        view = QWebEngineView()
        view.resize(800, 900)
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

        def key(key_value, modifiers=Qt.KeyboardModifier.NoModifier):
            target = view.focusProxy() or app.focusWidget() or view
            QTest.keyClick(target, key_value, modifiers, delay=20)
            QTest.qWait(60)

        widths = (1440, 800, 768, 560, 360)
        geometry = []
        for width in widths:
            view.resize(width, 900)
            QTest.qWait(90)
            layout = js("""(() => {
                const root = document.documentElement;
                const hero = document.querySelector('.hero h1');
                const wrap = document.querySelector('#sanity .tablewrap');
                const rect = wrap.getBoundingClientRect();
                return {
                    viewport: root.clientWidth,
                    documentScrollWidth: root.scrollWidth,
                    heroFits: hero.scrollWidth <= hero.clientWidth + 1,
                    sanityWrapperFits: rect.left >= -1 && rect.right <= root.clientWidth + 1,
                    sanityWrapped: !!wrap.querySelector('table.checks'),
                };
            })()""")
            js("""(() => {
                const trigger = document.querySelectorAll('.figbtn')[1];
                bsqZoom(trigger);
                document.getElementById('bsq-lb-img').click();
                return true;
            })()""")
            QTest.qWait(60)
            start = js("""(() => {
                const dialog = document.getElementById('bsq-lb');
                const image = document.getElementById('bsq-lb-img');
                const stage = dialog.querySelector('.lb-stage');
                const dr = stage.getBoundingClientRect();
                const ir = image.getBoundingClientRect();
                const maxX = stage.scrollWidth - stage.clientWidth;
                const maxY = stage.scrollHeight - stage.clientHeight;
                return {
                    open: dialog.open,
                    zoomed: image.classList.contains('zoomed'),
                    startAligned: getComputedStyle(stage).display === 'block',
                    leftReachable: ir.left >= dr.left - 1,
                    topReachable: ir.top >= dr.top - 1,
                    dialogLeft: dr.left,
                    imageLeft: ir.left,
                    stageLeft: stage.getBoundingClientRect().left,
                    imageMarginLeft: getComputedStyle(image).marginLeft,
                    scrollLeft: stage.scrollLeft,
                    scrollTop: stage.scrollTop,
                    maxX, maxY,
                };
            })()""")
            js("""(() => {
                const dialog = document.getElementById('bsq-lb');
                const stage = dialog.querySelector('.lb-stage');
                stage.scrollLeft = stage.scrollWidth - stage.clientWidth;
                stage.scrollTop = stage.scrollHeight - stage.clientHeight;
                return true;
            })()""")
            QTest.qWait(60)
            end = js("""(() => {
                const dialog = document.getElementById('bsq-lb');
                const image = document.getElementById('bsq-lb-img');
                const stage = dialog.querySelector('.lb-stage');
                const dr = stage.getBoundingClientRect();
                const ir = image.getBoundingClientRect();
                const maxX = stage.scrollWidth - stage.clientWidth;
                const maxY = stage.scrollHeight - stage.clientHeight;
                return {
                    atMaxX: Math.abs(stage.scrollLeft - maxX) <= 1,
                    atMaxY: Math.abs(stage.scrollTop - maxY) <= 1,
                    rightReachable: ir.right <= dr.left + stage.clientWidth + 1,
                    bottomReachable: ir.bottom <= dr.top + stage.clientHeight + 1,
                };
            })()""")
            js("(() => { bsqClose(); return true; })()")
            QTest.qWait(60)
            closed = js("""(() => {
                const dialog = document.getElementById('bsq-lb');
                const image = document.getElementById('bsq-lb-img');
                const triggers = Array.from(document.querySelectorAll('.figbtn'));
                return {
                    closed: !dialog.open,
                    zoomCleared: !image.classList.contains('zoomed') &&
                        !dialog.classList.contains('is-zoomed'),
                    sourceCleared: !image.hasAttribute('src'),
                    scrollCleared: dialog.scrollLeft === 0 && dialog.scrollTop === 0,
                    restoredIndex: triggers.indexOf(document.activeElement),
                };
            })()""")
            geometry.append({"width": width, "layout": layout, "start": start,
                             "end": end, "closed": closed})

        view.resize(360, 900)
        QTest.qWait(60)
        js("(() => { bsqZoom(document.querySelectorAll('.figbtn')[1]); return true; })()")
        QTest.qWait(60)
        initial_focus = js("document.activeElement.id")
        key(Qt.Key.Key_Tab)
        tab_one = js("document.activeElement.id")
        key(Qt.Key.Key_Tab)
        tab_two = js("document.activeElement.id")
        key(Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        shift_tab = js("document.activeElement.id")
        background_focus = js("""(() => {
            document.querySelectorAll('.figbtn')[0].focus();
            return {
                active: document.activeElement.id,
                contained: document.getElementById('bsq-lb').contains(document.activeElement),
            };
        })()""")
        key(Qt.Key.Key_Escape)
        escaped = js("""(() => {
            const triggers = Array.from(document.querySelectorAll('.figbtn'));
            return {
                closed: !document.getElementById('bsq-lb').open,
                restoredIndex: triggers.indexOf(document.activeElement),
            };
        })()""")
        print(json.dumps({
            "geometry": geometry,
            "focus": {"initial": initial_focus, "tabOne": tab_one, "tabTwo": tab_two,
                      "shiftTab": shift_tab, "background": background_focus,
                      "escape": escaped},
        }, sort_keys=True))
        view.close()
        app.processEvents()
        '''
    ).replace("__REPORT_URI__", json.dumps(report.as_uri()))
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_GEN.parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert [item["width"] for item in result["geometry"]] == [1440, 800, 768, 560, 360]
    for item in result["geometry"]:
        assert item["layout"]["documentScrollWidth"] <= item["layout"]["viewport"] + 1, json.dumps(item)
        assert item["layout"]["heroFits"]
        assert item["layout"]["sanityWrapperFits"] and item["layout"]["sanityWrapped"]
        assert item["start"]["open"] and item["start"]["zoomed"]
        assert item["start"]["startAligned"]
        assert item["start"]["leftReachable"] and item["start"]["topReachable"], json.dumps(item)
        assert item["start"]["scrollLeft"] == 0 and item["start"]["scrollTop"] == 0
        assert item["start"]["maxX"] > 0 and item["start"]["maxY"] > 0
        assert all(item["end"].values())
        assert item["closed"] == {
            "closed": True,
            "restoredIndex": 1,
            "scrollCleared": True,
            "sourceCleared": True,
            "zoomCleared": True,
        }
    assert result["focus"] == {
        "initial": "bsq-lb-close",
        "tabOne": "bsq-lb-fit",
        "tabTwo": "bsq-lb-actual",
        "shiftTab": "bsq-lb-fit",
        "background": {"active": "bsq-lb-fit", "contained": True},
        "escape": {"closed": True, "restoredIndex": 1},
    }


def test_gprofiler_ora_table_is_not_empty(mhr, tmp_path):
    # The g:Profiler route uses term_name/p_value/intersection_size; the table must still populate
    # (previously it rendered with no columns because none of the clusterProfiler names matched).
    p = tmp_path / "go_ora_all.csv"
    _write_csv(p, _GP_COLS, _GP_ROWS)
    html = mhr._enrich_block("GO", p, "ora")
    assert "immune response" in html          # Description from term_name
    assert "<td class='num'>20</td>" in html   # Genes from intersection_size
    assert "<thead><tr></tr></thead>" not in html  # never an empty header row


def test_unrecognized_columns_suppresses_block(mhr, tmp_path):
    p = tmp_path / "go_ora_all.csv"
    _write_csv(p, ["foo", "bar"], [["x", "y"]])
    assert mhr._enrich_block("GO", p, "ora") == ""


def test_missing_file_returns_empty(mhr, tmp_path):
    assert mhr._enrich_block("GO", tmp_path / "nope.csv", "ora") == ""


def test_present_but_empty_csv_shows_ran_nothing_placeholder(mhr, tmp_path):
    # A header-only CSV means the analysis ran and nothing passed -> say so (do not vanish).
    p = tmp_path / "gsea.csv"
    _write_csv(p, ["Description", "NES", "p.adjust", "setSize"], [])
    html = mhr._enrich_block("GO GSEA", p, "gsea")
    assert "No terms passed the significance threshold" in html
    assert "enr-block empty" in html


def test_de_table_numeric_cells_and_basemean_formatting(mhr, tmp_path):
    p = tmp_path / "upregulated_genes.csv"
    _write_csv(p, ["gene_id", "symbol", "log2FoldChange", "padj", "baseMean", "biotype"],
               [["ENSG1", "FKBP5", 2.5, 1e-8, 1234.5, "protein_coding"]])
    html = mhr._de_table(p)
    # numeric columns right-align (class='num'); symbol is not numeric
    assert "<th scope='col' class='num'>log2FoldChange</th>" in html
    assert "<th scope='col'>symbol</th>" in html
    # baseMean shows a thousands-separated integer but keeps the raw float for sorting
    assert "1,234" in html
    assert "data-sort-value='1234.5'" in html
    assert "<i>FKBP5</i>" in html


def _external_run(*, p_adjustment_method: str = "Holm", confirmed: bool = True) -> dict:
    return {
        "input": {
            "type": "deseq2_results",
            "samples": "metadata/cohort.tsv",
            "deseq2_results": "config/deseq2_results.csv",
            "deseq2_results_direction": {
                "numerator": "stimulated", "denominator": "baseline", "confirmed": confirmed,
                "confirmed_at": "2026-08-10T12:00:00+03:00" if confirmed else None,
            },
            "deseq2_results_provenance": {
                "original_basename": "upstream_results.tsv",
                "imported_at": "2026-08-10T11:59:00+03:00",
                "project_copy": "config/deseq2_results.csv",
                "sha256": "b" * 64,
                "byte_size": 456,
                "row_count": 3,
                "column_names": ["gene_id", "log2FoldChange", "padj", "baseMean"],
                "gene_id_column": "gene_id",
                "log2fc_column": "log2FoldChange",
                "adjusted_p_column": "padj",
                "upstream_method": "limma",
                "lfc_shrinkage": "not_applied",
                "p_adjustment_method": p_adjustment_method,
            },
        },
        "deseq2": {
            "design_formula": "~ stale", "reference_level": "wrong",
            "contrasts": [{"numerator": "wrong", "denominator": "wrong"}],
            "alpha": 0.05, "lfc_threshold": 1,
        },
        "workflow": {"de_engine": "DESeq2", "aligner": "STAR", "quantifier": "featureCounts"},
        "reference": {},
        "software_versions": {"snakemake": "8", "STAR": "2.7"},
        "r_packages": {"DESeq2": "1.0", "apeglm": "1.0", "clusterProfiler": "4.0"},
    }


def _write_external_de_outputs(project: Path) -> None:
    deseq = project / "results" / "deseq2"
    deseq.mkdir(parents=True)
    cols = ["gene_id", "symbol", "log2FoldChange", "padj", "baseMean"]
    _write_csv(deseq / "deseq2_results.csv", cols, [
        ["g1", "UP1", 2.0, 0.001, 100],
        ["g2", "DOWN1", -2.0, 0.002, 80],
        ["g3", "NS", 0.1, 0.8, 50],
    ])
    _write_csv(deseq / "upregulated_genes.csv", cols, [["g1", "UP1", 2.0, 0.001, 100]])
    _write_csv(deseq / "downregulated_genes.csv", cols, [["g2", "DOWN1", -2.0, 0.002, 80]])


def test_uploaded_results_report_uses_full_provenance_not_stale_model_settings(mhr, tmp_path):
    run = _external_run()
    assert mhr._contrast_pair(run) == ("stimulated", "baseline")
    assert mhr._engine_name(run) == "externally supplied results (no local DE model)"
    design = mhr._study_design_section(run)
    for expected in (
        "upstream_results.tsv", "Project copy", "config/deseq2_results.csv", "b" * 64,
        "456", "Imported rows", "3", "gene_id, log2FoldChange, padj, baseMean",
        "gene ID=gene_id; log2 fold change=log2FoldChange; adjusted p-value=padj",
        "Upstream differential-expression method", "limma", "Upstream LFC shrinkage",
        "not_applied", "Upstream p-adjustment method", "Holm",
    ):
        assert expected in design
    assert "confirmed numerator" in design
    assert "confirmed denominator" in design
    assert "<b>stimulated</b>" in design and "<b>baseline</b>" in design
    assert "Result source" not in design
    assert "~ stale" not in design
    assert "DESeq2" not in design
    assert "FDR" not in design
    assert "Benjamini" not in design

    cards = mhr._meta_cards(run, tmp_path)
    assert "Externally supplied differential-expression table" in cards
    assert "adjusted p-value (Holm) &lt; 0.05" in cards
    assert "Upstream DE method" in cards and "limma" in cards
    assert "Design" not in cards
    assert "Aligner" not in cards and "STAR" not in cards
    assert "DESeq2" not in cards


def test_uploaded_results_unknown_adjustment_is_generic_in_de_reporting(mhr, tmp_path):
    run = _external_run(p_adjustment_method="unknown")
    _write_external_de_outputs(tmp_path)
    finding = mhr._key_finding(run, tmp_path, "")
    assert "adjusted p-value &lt; 0.05" in finding
    assert "FDR" not in finding
    assert "Benjamini" not in finding
    assert "stimulated" in finding and "baseline" in finding

    de = mhr._de_section(tmp_path, 1, 1, "stimulated", "baseline", "genes", run=run)
    assert "adjusted p-value (padj)" in de
    assert "source-supplied adjusted p-value" in de
    assert "FDR" not in de
    assert "Benjamini" not in de
    assert "treated" not in de.casefold()
    assert "control" not in de.casefold()

    definitions = mhr._route_gloss(run)
    assert "method was not recorded" in definitions["padj"]
    assert "Benjamini" not in definitions["padj"]
    assert "FDR" not in definitions["padj"]
    assert "confirmed numerator" in definitions["log2fc"]


def test_unconfirmed_external_direction_never_uses_group_labels_semantically(mhr, tmp_path):
    run = _external_run(p_adjustment_method="unknown", confirmed=False)
    _write_external_de_outputs(tmp_path)
    assert mhr._contrast_pair(run) == (None, None)
    design = mhr._study_design_section(run)
    assert "semantics were not confirmed" in design
    assert "higher expression in" not in design
    de = mhr._de_section(tmp_path, 1, 1, None, None, "genes", run=run)
    assert "positive log2FC" in de and "negative log2FC" in de
    assert "treated" not in de.casefold()
    assert "control" not in de.casefold()


def test_sample_composition_uses_configured_input_samples_and_skips_external_route(mhr, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "metadata").mkdir()
    (tmp_path / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\nd1\twrong\nd2\twrong\n", encoding="utf-8")
    (tmp_path / "metadata" / "cohort.tsv").write_text(
        "sample_id\tcondition\ns1\tbaseline\ns2\tbaseline\ns3\tstimulated\n",
        encoding="utf-8")
    local = {"input": {"type": "fastq", "samples": "metadata/cohort.tsv"}}
    assert mhr._sample_composition(tmp_path, "stimulated", "baseline", local) == \
        "2 baseline · 1 stimulated"
    assert mhr._sample_composition(tmp_path, "stimulated", "baseline", _external_run()) is None


def test_external_figure_copy_records_upstream_shrinkage_and_ignores_stale_count_figures(mhr, tmp_path):
    figs = tmp_path / "figures"
    figs.mkdir()
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
    for name in ("ma_plot", "pca", "top_deg_heatmap"):
        (figs / f"{name}.svg").write_text(svg, encoding="utf-8")
    rendered = mhr._figure_groups(figs, 1, 1, "genes", run=_external_run())
    assert "upstream shrinkage recorded as not applied" in rendered
    assert "Principal-component analysis" not in rendered
    assert "Top differentially-expressed genes" not in rendered
    assert "shrunken log2 fold change" not in rendered


def test_built_external_report_excludes_stale_local_route_artifacts(mhr, tmp_path):
    run = _external_run()
    _write_external_de_outputs(tmp_path)
    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    (reports / "run_summary.json").write_text(json.dumps(run), encoding="utf-8")
    (reports / "meta_analysis_summary.json").write_text(json.dumps({
        "n_meta_sig": 1, "n_studies": 2, "n_sig_up": 1, "n_sig_down": 0,
    }), encoding="utf-8")
    rendered = mhr.build(tmp_path)
    assert "Original basename" in rendered and "upstream_results.tsv" in rendered
    assert "Project copy" in rendered and "config/deseq2_results.csv" in rendered
    assert "adjusted p-value (Holm)" in rendered
    assert "~ stale" not in rendered
    assert "STAR" not in rendered
    assert "DESeq2" not in rendered
    assert "treated" not in rendered.casefold()
    assert "control" not in rendered.casefold()
    assert "FDR (padj)" not in rendered
    assert "Adjusted p-value (FDR / padj)" not in rendered


def test_microarray_headline_uses_collapsed_gene_level_unit_not_probes(
        mhr, tmp_path, monkeypatch):
    deseq = tmp_path / "results" / "deseq2"
    deseq.mkdir(parents=True)
    (deseq / "deseq2_results.csv").write_text("gene_id,padj,log2FoldChange\n", encoding="utf-8")
    monkeypatch.setattr(
        mhr, "_de_headline_stats",
        lambda project, alpha, lfc_t: (87, 67, 22180, [], []),
    )
    run = {
        "input": {"type": "microarray"},
        "reference": {"organism_name": "Arabidopsis thaliana"},
        "deseq2": {
            "alpha": 0.05, "lfc_threshold": 1,
            "contrasts": [{"numerator": "hub2_3", "denominator": "wild_type"}],
        },
        "microarray": {"gse_accession": "GSE30735"},
    }
    finding = mhr._key_finding(run, tmp_path, "")
    hero = mhr._hero_findings(run, tmp_path, "", "arabidopsis")
    for rendered in (finding, hero):
        assert "154" in rendered
        assert "22,180" in rendered
        assert "genes/features" in rendered.lower()
        assert "probe" not in rendered.lower()


def test_microarray_study_design_omits_inactive_deseq2_shrinkage_and_internal_key(mhr):
    run = {
        "input": {"type": "microarray"},
        "reference": {"organism_name": "Arabidopsis thaliana"},
        "deseq2": {
            "design_formula": "~ condition",
            "reference_level": {"condition": "wild_type"},
            "contrasts": [{"numerator": "hub2_3", "denominator": "wild_type"}],
            "lfc_shrinkage": True,
            "shrinkage_method": "apeglm",
        },
        "customized_parameters": {
            "deseq2.reference_level.condition": {"default": "control", "used": "wild_type"},
        },
    }
    rendered = mhr._study_design_section(run)
    assert "analysed with limma" in rendered
    assert "LFC shrinkage" not in rendered
    assert "apeglm" not in rendered
    assert "deseq2.reference_level.condition" not in rendered
    assert "Comparison reference level" in rendered


def test_sanity_display_uses_route_neutral_differential_expression_label(mhr):
    text = (
        "Overall: WARNING\n"
        "09_deseq2_qc: WARNING\n"
        "- WARNING: diagnostic message\n"
    )
    assert "09 differential-expression QC" in mhr._status_sentence(text)
    rendered = mhr._sanity_section(text)
    assert "09 differential-expression QC" in rendered
    assert "09 deseq2 qc" not in rendered.casefold()


def test_ppi_copy_does_not_misstate_string_associations_as_direct_interactions(mhr):
    glossary = mhr.GLOSS["ppi"]
    figure_copy = " ".join(mhr.FIG["ppi_network"])
    for rendered in (glossary, figure_copy):
        assert "functional" in rendered
        assert "physical" in rendered
        assert "necessarily" in rendered and "direct" in rendered
        assert "known interactions" not in rendered.casefold()


def test_microarray_basemean_is_expression_intensity_not_a_read_count(mhr, tmp_path):
    table_path = tmp_path / "microarray.csv"
    table_path.write_text(
        "gene_id,log2FoldChange,padj,baseMean\nAT1G01010,2.1,0.01,-3.4644\n",
        encoding="utf-8",
    )
    rendered = mhr._de_table(table_path, base_mean_kind="expression")
    assert "data-sort-value='-3.4644'" in rendered
    assert ">-3.46</td>" in rendered

    gloss = mhr._route_gloss({"input": {"type": "microarray"}})["basemean"]
    assert "normalized log2 expression intensity" in gloss
    assert "negative values are valid" in gloss
    assert "read count" not in gloss


def test_microarray_ma_copy_uses_expression_not_count_language(mhr):
    run = {"input": {"type": "microarray"}}
    glossary = mhr._route_gloss(run)["ma"]
    howto = mhr._micro_howto(mhr.FIG["ma_plot"][4])

    rendered = " ".join((glossary, howto)).casefold()
    assert "normalized log2 expression intensity" in glossary
    assert "lowest measured expression" in howto
    assert "low-count" not in rendered
    assert "lowest-count" not in rendered

    # Count-based routes retain the count-specific explanation.
    assert "low-count genes" in mhr.GLOSS["ma"]
    assert "lowest-count genes" in mhr.FIG["ma_plot"][4]


def test_recorded_contrast_replaces_treatment_language_in_glossary_and_heatmaps(mhr):
    run = {
        "input": {"type": "microarray"},
        "deseq2": {"contrasts": [{
            "factor": "condition", "numerator": "hub2_3", "denominator": "wild_type"
        }]},
    }

    gloss = mhr._route_gloss(run)["log2fc"]
    up = mhr._directional_figure_copy(
        "top_upregulated_heatmap", mhr.FIG["top_upregulated_heatmap"][4], run
    )
    down = mhr._directional_figure_copy(
        "top_downregulated_heatmap", mhr.FIG["top_downregulated_heatmap"][4], run
    )

    assert "hub2_3 (the numerator)" in gloss
    assert "wild_type (the denominator)" in gloss
    assert "hub2_3, the numerator group" in up
    assert "hub2_3, the numerator group" in down
    assert "treated" not in " ".join((gloss, up, down)).casefold()


def test_ranked_headline_does_not_call_significance_order_effect_strength(mhr, tmp_path):
    deseq = tmp_path / "results" / "deseq2"
    deseq.mkdir(parents=True)
    cols = ["gene_id", "symbol", "log2FoldChange", "padj", "baseMean"]
    _write_csv(deseq / "deseq2_results.csv", cols, [
        ["g1", "MOST_SIGNIFICANT", 1.1, 1e-8, 100],
        ["g2", "LARGEST_EFFECT", 4.0, 1e-4, 100],
        ["g3", "DOWN", -1.5, 2e-8, 100],
    ])
    _write_csv(deseq / "upregulated_genes.csv", cols, [["g1", "MOST_SIGNIFICANT", 1.1, 1e-8, 100]])
    _write_csv(deseq / "downregulated_genes.csv", cols, [["g3", "DOWN", -1.5, 2e-8, 100]])
    rendered = mhr._key_finding(
        {
            "input": {"type": "microarray"},
            "deseq2": {"alpha": 0.05, "lfc_threshold": 1.0},
        },
        tmp_path,
        "",
    )
    assert "most statistically supported increases" in rendered
    assert "strongest increases" not in rendered


def test_runtime_section_labels_pipeline_timing_as_partial_analysis_window(mhr, tmp_path):
    timing = {
        "wall_clock_approx_hms": "0:01:20",
        "cumulative_job_hms": "0:02:26",
        "timing_scope": (
            "Enabled scientific outputs through final report inputs; excludes the "
            "final_reports and html_report assembly jobs."
        ),
        "includes_report_assembly": False,
        "configured_resources": {"snakemake_cores": 10, "memory_gb": 47},
    }
    rendered = mhr._timing_section(timing)
    assert "Analysis job window (approx.)" in rendered
    assert "CPU workers" in rendered
    assert "excludes the final_reports and html_report assembly jobs" in rendered
    assert "GUI elapsed timer is the authority" in rendered
    assert "Wall-clock (approx.)" not in rendered

    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    (reports / "timing_summary.json").write_text(json.dumps(timing), encoding="utf-8")
    cards = mhr._meta_cards({"input": {"type": "microarray"}}, tmp_path)
    assert "Analysis window" in cards
    assert ">Wall-clock<" not in cards


def test_html_enrichment_discloses_mapping_coverage_and_limitations(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    _write_csv(enr / "go_ora_all.csv", _CP_COLS, _CP_ROWS)
    (enr / "enrichment_summary.txt").write_text(
        "Functional enrichment summary\n"
        "Eligible ID mapping keytypes: SYMBOL, TAIR, ENTREZID, ALIAS\n"
        "Identifier routing policy: AGI locus IDs -> TAIR; all other IDs -> configured SYMBOL.\n"
        "Tested input IDs retained after mapping/exclusion: 20760/22180 (93.6%)\n"
        "Significant input IDs retained after mapping/exclusion: 147/154 (95.5%)\n"
        "Mapped tested-gene universe (unique Entrez IDs): 20629\n"
        "GO effective annotated ORA universes: BP 15910/20629 (77.1%; LIMITED_ANNOTATION); MF 20200/20629 (97.9%; PASS); CC 20500/20629 (99.4%; PASS)\n"
        "KEGG identity verification: PASS; configured code=ath; registry code=ath; organism=Arabidopsis thaliana; taxon=3702; expected organism=Arabidopsis thaliana; expected taxon=3702\n"
        "KEGG retrieval: SUCCESS; key form=kegg; pathway collection=162; detail=none\n"
        "KEGG effective resource universe: 4748/20629 (23.0%); eligible 10-500 pathway universe=4687\n"
        "KEGG supported foreground: up 14/79 (17.7%); down 14/64 (21.9%); combined 28/143 (19.6%)\n"
        "KEGG eligible hypotheses/gene sets: 132 after 10-500 filter; foreground-overlapping ORA hypotheses adjusted=32\n"
        "KEGG adjusted results: ORA=0; GSEA=0; BH pvalueCutoff=0.05; qvalueCutoff=0.20\n"
        "KEGG resource status: LIMITED_ANNOTATION; no supported KEGG pathways met the adjusted criterion; this is not evidence that no pathway biology is present\n"
        "Ambiguous input IDs excluded: 14 (routed one-to-many: 8; unresolved cross-keytype: 6)\n"
        "Direction-conflict Entrez IDs excluded: 1; input IDs excluded: 2\n"
        "Foreground intersection (up/down Entrez) after exclusion: 0\n"
        "Mapping interpretation gate: PASS (WARNING below 80%; REVIEW_REQUIRED below 50%)\n"
        "Direction-conflict gate: REVIEW_REQUIRED (any conflict requires review)\n"
        "GO/DO annotation-resource status: LIMITED_ANNOTATION (coverage below 80% is LIMITED_ANNOTATION; zero or malformed resource universes are NOT_INTERPRETABLE; this is separate from global ID mapping)\n"
        "Universe policy: all and only unambiguously mapped, direction-conflict-free tested Entrez genes.\n"
        "ORA parameters: Benjamini-Hochberg (BH); pvalueCutoff=0.05; explicit tested-gene universe.\n"
        "ORA multiple-testing families: up, down, and combined queries are BH-corrected separately.\n"
        "GSEA ranking order: finite statistic descending; exact ties by numeric canonical ID ascending (exact digit-string order).\n"
        "GSEA exact-score ties: 2 pair(s) across 2 tie group(s), involving 4/7255 ranked genes.\n"
        "GSEA duplicate canonical-ID collapse: 0 group(s) containing 0 finite source row(s); 0 row(s) collapsed by median; 0 invalid-ID and 0 non-finite-score row(s) removed before collapse.\n"
        "Mapping limitation: enrichment tests only the mapped subset; incomplete mapping can bias terms.\n",
        encoding="utf-8",
    )
    rendered = mhr._enrichment_section(tmp_path)
    assert "Identifier mapping coverage and limitations" in rendered
    assert "20760/22180 (93.6%)" in rendered
    assert "147/154 (95.5%)" in rendered
    assert "20629" in rendered
    assert "GO effective annotated ORA universes: BP 15910/20629" in rendered
    assert "Ambiguous input IDs excluded: 14" in rendered
    assert "Foreground intersection (up/down Entrez) after exclusion: 0" in rendered
    assert "Benjamini-Hochberg (BH)" in rendered
    assert "BH-corrected separately" in rendered
    assert "GSEA ranking order: finite statistic descending" in rendered
    assert "GSEA exact-score ties: 2 pair(s)" in rendered
    assert "GSEA duplicate canonical-ID collapse: 0 group(s)" in rendered
    assert "KEGG identity verification: PASS" in rendered
    # The enrichKEGG key form is part of the retrieval evidence: the same organism can
    # return an empty collection under a different key form.
    assert "KEGG retrieval: SUCCESS; key form=kegg" in rendered
    assert "KEGG effective resource universe: 4748/20629 (23.0%)" in rendered
    assert "KEGG resource status: LIMITED_ANNOTATION" in rendered
    assert "no supported KEGG pathways met the adjusted criterion" in rendered
    assert "GO/DO annotation-resource status: LIMITED_ANNOTATION" in rendered
    assert "incomplete mapping can bias terms" in rendered


def test_html_enrichment_does_not_hide_low_mapping_review_gate(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text(
        "Tested input IDs retained after mapping/exclusion: 7874/22180 (35.5%)\n"
        "Significant input IDs retained after mapping/exclusion: 57/154 (37.0%)\n"
        "Ambiguous input IDs excluded: 409 (routed one-to-many: 409; unresolved cross-keytype: 0)\n"
        "Mapping interpretation gate: REVIEW_REQUIRED (WARNING below 80%; REVIEW_REQUIRED below 50%)\n"
        "Mapping limitation: enrichment terms are exploratory because coverage is low.\n",
        encoding="utf-8",
    )
    rendered = mhr._enrichment_section(tmp_path)
    assert "7874/22180 (35.5%)" in rendered
    assert "REVIEW_REQUIRED" in rendered
    assert "exploratory because coverage is low" in rendered


def test_html_valid_empty_kegg_is_limited_not_biological_absence(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text(
        "KEGG resource status: LIMITED_ANNOTATION; no supported KEGG pathways met the adjusted criterion; this is not evidence that no pathway biology is present\n",
        encoding="utf-8",
    )
    rendered = mhr._enrichment_section(tmp_path)
    assert "No supported KEGG pathways met the adjusted criterion" in rendered
    assert "does not establish that no pathway biology is present" in rendered
    assert "KEGG is not interpretable" not in rendered


def test_html_invalid_kegg_requires_resource_integrity_review(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text(
        "KEGG resource status: NOT_INTERPRETABLE; configured organism does not match the offline KEGG registry\n",
        encoding="utf-8",
    )
    rendered = mhr._enrichment_section(tmp_path)
    assert "KEGG is not interpretable for this run" in rendered
    assert "review the resource-integrity evidence" in rendered


def _write_custom_run_summary(project: Path, **overrides: str) -> None:
    reports = project / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    gene_sets = {
        "custom_gene_sets": "config/pathways.gmt",
        "functional_annotation_table": "",
        "background_gene_list": "config/background.txt",
    }
    gene_sets.update(overrides)
    (reports / "run_summary.json").write_text(
        json.dumps({"gene_sets": gene_sets}), encoding="utf-8")


def _assert_custom_enrichment_contract(rendered: str) -> None:
    assert "Custom gene-set enrichment" in rendered
    assert "Custom gene sets — over-representation (ORA)" in rendered
    assert "Custom gene sets — ranked-list enrichment (GSEA)" in rendered
    assert "Custom GSEA ranking order:" in rendered
    assert "Custom GSEA exact-score ties:" in rendered
    assert "Custom GSEA duplicate canonical-ID collapse:" in rendered


def test_custom_enrichment_results_evidence_and_dotplot_render(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    figs = tmp_path / "results" / "figures"
    enr.mkdir(parents=True)
    figs.mkdir(parents=True)
    _write_custom_run_summary(tmp_path)
    _write_csv(enr / "custom_ora.csv", _CP_COLS, _CP_ROWS)
    _write_csv(
        enr / "custom_gsea.csv",
        ["Description", "NES", "p.adjust", "setSize"],
        [["interferon response", 1.82, 0.004, 37]],
    )
    (enr / "custom_enrichment_summary.txt").write_text(
        "Custom gene-set enrichment summary\n"
        "Custom gene sets (terms): 24\n"
        "Universe: 7255 (background file)\n"
        "Significant genes (ORA input): 146\n"
        "Custom GSEA ranking order: finite statistic descending; exact ties by canonical gene ID.\n"
        "Custom GSEA exact-score ties: 2 pair(s) across 2 tie group(s), involving 4/7255 ranked genes.\n"
        "Custom GSEA duplicate canonical-ID collapse: 1 group(s) containing 2 finite source row(s); 1 row(s) collapsed by median.\n"
        "Custom ORA terms: 1\n"
        "Custom GSEA sets: 1\n",
        encoding="utf-8",
    )
    (figs / "custom_enrichment_dotplot.png").write_bytes(b"synthetic")

    rendered = mhr._enrichment_section(tmp_path)
    _assert_custom_enrichment_contract(rendered)
    for expected in (
        "immune response", "interferon response", "Custom gene sets (terms): 24",
        "Universe: 7255 (background file)", "Configured GMT source: config/pathways.gmt",
        "Configured ORA background source: config/background.txt", "Custom ORA terms: 1",
        "Custom GSEA sets: 1",
    ):
        assert expected in rendered
    custom_panel = rendered[rendered.index("Custom gene-set over-representation (ORA)") - 500:]
    _assert_responsive_panel_contract(custom_panel, "custom_enrichment_dotplot")

    # Negative gate: the pre-fix report had no custom result/evidence block, and this contract
    # must fail against that legacy-shaped output rather than passing vacuously.
    with pytest.raises(AssertionError):
        _assert_custom_enrichment_contract(
            "<section id='enrichment'><h2>Functional enrichment</h2></section>")


def test_custom_enrichment_empty_results_are_scoped_to_supplied_collection(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    _write_custom_run_summary(tmp_path)
    (enr / "custom_ora.csv").write_text("\n", encoding="utf-8")
    _write_csv(enr / "custom_gsea.csv", ["Description", "NES", "p.adjust", "setSize"], [])
    (enr / "custom_enrichment_summary.txt").write_text(
        "Custom gene sets (terms): 12\nCustom ORA terms: 0\nCustom GSEA sets: 0\n",
        encoding="utf-8",
    )

    rendered = mhr._enrichment_section(tmp_path)
    assert "No supplied custom gene set met the adjusted ORA criterion" in rendered
    assert "configured collection and tested-gene universe" in rendered
    assert "No supplied custom gene set met the adjusted GSEA criterion" in rendered
    assert "configured collection and ranked genes" in rendered
    assert "does not establish biological absence outside that collection" in rendered


def test_custom_enrichment_missing_and_malformed_tables_are_not_reported_as_null(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "custom_enrichment_summary.txt").write_text(
        "Custom gene sets (terms): 7\nCustom enrichment failed: synthetic parse failure\n",
        encoding="utf-8",
    )
    _write_csv(enr / "custom_ora.csv", ["unexpected", "schema"], [["x", "y"]])

    rendered = mhr._enrichment_section(tmp_path)
    assert "ORA result table could not be interpreted" in rendered
    assert "GSEA result table is unavailable" in rendered
    assert "do not interpret the missing artifact as an empty" in rendered
    assert "Custom enrichment failed: synthetic parse failure" in rendered
    assert "Custom GSEA reproducibility evidence" in rendered
    assert "record is incomplete" in rendered
    assert "No supplied custom gene set met" not in rendered


def test_custom_enrichment_missing_or_unrecognized_summary_is_disclosed(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    _write_csv(enr / "custom_ora.csv", _CP_COLS, _CP_ROWS)
    (enr / "custom_gsea.csv").write_text("\n", encoding="utf-8")

    missing = mhr._enrichment_section(tmp_path)
    assert "Custom enrichment reproducibility summary: unavailable" in missing
    assert "custom_enrichment_summary.txt before interpretation" in missing

    (enr / "custom_enrichment_summary.txt").write_text(
        "unrecognized <script>alert(1)</script>\n", encoding="utf-8")
    malformed = mhr._enrichment_section(tmp_path)
    assert "present but no recognized set, source, result, or deterministic-ranking evidence" in malformed
    assert "<script>alert(1)</script>" not in malformed


def test_custom_enrichment_escapes_tables_summary_and_configured_sources(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    payload = "<img src=x onerror=alert(1)>"
    _write_custom_run_summary(tmp_path, custom_gene_sets=f"config/{payload}.gmt")
    row = list(_CP_ROWS[0])
    row[1] = payload
    _write_csv(enr / "custom_ora.csv", _CP_COLS, [row])
    (enr / "custom_gsea.csv").write_text("\n", encoding="utf-8")
    (enr / "custom_enrichment_summary.txt").write_text(
        f"Custom gene sets (terms): {payload}\n", encoding="utf-8")

    rendered = mhr._enrichment_section(tmp_path)
    assert payload not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert "config/&lt;img src=x onerror=alert(1)&gt;.gmt" in rendered


def test_custom_enrichment_absent_route_and_explicitly_unconfigured_stale_outputs_stay_hidden(
        mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text(
        "KEGG resource status: NOT_RUN\n", encoding="utf-8")
    assert "Custom gene-set enrichment" not in mhr._enrichment_section(tmp_path)

    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    (reports / "run_summary.json").write_text(
        json.dumps({"gene_sets": {}}), encoding="utf-8")
    _write_csv(enr / "custom_ora.csv", _CP_COLS, _CP_ROWS)
    assert "Custom gene-set enrichment" not in mhr._enrichment_section(tmp_path)

    (reports / "run_summary.json").write_text(
        json.dumps({"gene_sets": {"background_gene_list": "config/background.txt"}}),
        encoding="utf-8",
    )
    assert "Custom gene-set enrichment" not in mhr._enrichment_section(tmp_path)


# Annotation-transfer ORA/GSEA carry a category (and, for ORA, a foreground) column ahead of
# the usual clusterProfiler columns; run_transfer_enrichment.R already maps geneID back to
# gene_id, so the same recognized column set as _CP_COLS applies.
_TRANSFER_ORA_COLS = ["category", "foreground"] + _CP_COLS
_TRANSFER_GSEA_COLS = ["category", "Description", "NES", "p.adjust", "setSize"]


def test_transfer_enrichment_results_evidence_and_dotplot_render(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment" / "transfer"
    figs = tmp_path / "results" / "figures"
    enr.mkdir(parents=True)
    figs.mkdir(parents=True)
    # Written up, then combined, matching run_transfer_enrichment.R's per-category foreground
    # loop order — the report must still show the combined row first.
    up_row = ["GO BP", "up"] + _CP_ROWS[0]
    combined_row = ["GO BP", "combined", "GO:0006956", "complement activation", "20/100",
                    "180/12000", "1e-9", "1e-7", "1e-7", "A/B/C/D", 20, 8.1]
    _write_csv(enr / "transfer_ora.csv", _TRANSFER_ORA_COLS, [up_row, combined_row])
    _write_csv(
        enr / "transfer_gsea.csv", _TRANSFER_GSEA_COLS,
        [["Reactome", "cytokine signalling", 1.7, 0.01, 42]],
    )
    (enr / "transfer_summary.txt").write_text(
        "Annotation-transfer enrichment summary\n"
        "STRING version 12.0, taxon 7227 (orthology-transferred annotation; Szklarczyk et al. 2023, CC BY 4.0).\n"
        "Files: 7227.protein.aliases.v12.0.txt.gz (retrieved 2026-09-20), 7227.protein.enrichment.terms.v12.0.txt.gz (retrieved 2026-09-20).\n"
        "Tested genes mapped: 900/1000 (90.0%); keys used: gene_id=900.\n"
        "Universe: 900 STRING proteins from the tested genes. Foregrounds: up 40, down 35, combined 75; 0 proteins carried genes in both directions.\n"
        "GSEA: 950 ranked proteins (ranked on stat); 0 proteins with sign-discordant genes excluded.\n"
        "GO sets are used as STRING propagated them under its own GO release; they are not re-propagated.\n"
        "GO BP: 12 sets of 10-500 genes; annotated tested proteins 88.0% (proteome 80.0%); ORA combined 1, GSEA 0 adjusted terms.\n"
        "Reactome: 4 sets of 10-500 genes; annotated tested proteins 70.0% (proteome 60.0%); ORA combined 0, GSEA 1 adjusted terms.\n"
        "Check 25 status: PASS\n",
        encoding="utf-8",
    )
    (figs / "transfer_enrichment_dotplot.png").write_bytes(b"synthetic")

    rendered = mhr._enrichment_section(tmp_path)
    assert "Annotation-transfer enrichment" in rendered
    assert "Annotation transfer — over-representation (ORA)" in rendered
    assert "Annotation transfer — ranked-list enrichment (GSEA)" in rendered
    assert "Terms are transferred by orthology from STRING" in rendered
    assert "not curated annotation for this organism" in rendered
    for expected in (
        "Category", "Foreground", "GO BP", "cytokine signalling",
        "STRING version 12.0, taxon 7227", "Tested genes mapped: 900/1000 (90.0%)",
        "Check 25 status: PASS",
    ):
        assert expected in rendered
    # The combined row is written second but must render first (combined-foreground-first sort).
    assert rendered.index("complement activation") < rendered.index("immune response")
    transfer_panel = rendered[rendered.index("Annotation-transfer over-representation") - 500:]
    _assert_responsive_panel_contract(transfer_panel, "transfer_enrichment_dotplot")

    # Negative gate: a report with no transfer section must fail this contract.
    with pytest.raises(AssertionError):
        assert "Annotation-transfer enrichment" in "<section id='enrichment'></section>"


def test_transfer_enrichment_empty_tables_render_the_empty_message(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment" / "transfer"
    enr.mkdir(parents=True)
    # run_transfer_enrichment.R's empty convention is write.csv(data.frame()), which R renders
    # as a single quoted-empty header field, not a truly blank line.
    (enr / "transfer_ora.csv").write_text('""\n', encoding="utf-8")
    (enr / "transfer_gsea.csv").write_text('""\n', encoding="utf-8")
    (enr / "transfer_summary.txt").write_text(
        "Annotation-transfer enrichment summary\nCheck 25 status: PASS\n", encoding="utf-8")

    rendered = mhr._enrichment_section(tmp_path)
    assert "No annotation-transfer term met the adjusted ORA criterion" in rendered
    assert "No annotation-transfer term met the adjusted GSEA criterion" in rendered
    assert "could not be interpreted" not in rendered


def test_transfer_enrichment_absent_when_no_artifacts_exist(mhr, tmp_path):
    enr = tmp_path / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text(
        "KEGG resource status: NOT_RUN\n", encoding="utf-8")
    assert mhr._transfer_enrichment_section(tmp_path) == ""
    assert "Annotation-transfer enrichment" not in mhr._enrichment_section(tmp_path)


# ---- Figure captions follow the realized assay kind, not the input type ------

def _svg_figs(tmp_path: Path, *names: str) -> Path:
    figs = tmp_path / "figures"
    figs.mkdir(exist_ok=True)
    for name in names:
        (figs / f"{name}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>',
            encoding="utf-8")
    return figs


def _count_run(engine: str) -> dict:
    return {"input": {"type": "fastq"}, "workflow": {"de_engine": engine}}


def test_assay_kind_is_derived_from_the_route_and_the_de_engine(mhr) -> None:
    assert mhr._assay_kind(_count_run("DESeq2")) == "vst"
    assert mhr._assay_kind(_count_run("limma-voom")) == "log2_cpm"
    assert mhr._assay_kind(_count_run("edgeR")) == "log2_cpm"
    assert mhr._assay_kind({"input": {"type": "count_matrix"},
                            "workflow": {"de_engine": "edgeR"}}) == "log2_cpm"
    # Microarray runs limma whatever the engine field says; an upload fits no local model.
    assert mhr._assay_kind({"input": {"type": "microarray"},
                            "workflow": {"de_engine": "limma-voom"}}) == "log2_intensity"
    assert mhr._assay_kind({"input": {"type": "deseq2_results"}}) == "results_only"
    assert mhr._assay_kind({}) == "vst"


@pytest.mark.parametrize("engine", ["limma-voom", "edgeR"])
def test_count_captions_match_the_log2_cpm_axes_the_figures_draw(mhr, tmp_path, engine) -> None:
    figs = _svg_figs(tmp_path, "ma_plot", "pca", "top_deg_heatmap")
    rendered = mhr._figure_groups(figs, 3, 2, "genes", run=_count_run(engine))

    # make_figures.R labels the MA x axis "average log2 expression" on these backends and
    # neither run_voom.R nor run_edger.R shrinks the effect (resLFC <- res).
    assert "average log2 expression" in rendered
    assert "log2 fold change (unshrunken)" in rendered
    assert "log2 CPM" in rendered
    assert "shrunken log2 fold change" not in rendered
    assert "variance-stabilised counts" not in rendered
    assert "mean normalised counts (log)" not in rendered
    # voom and edgeR are count-based, so the count wording in the how-to stays accurate.
    assert "lowest-count genes" in rendered


def test_deseq2_and_microarray_captions_are_unchanged(mhr, tmp_path) -> None:
    figs = _svg_figs(tmp_path, "ma_plot", "pca")
    deseq2 = mhr._figure_groups(figs, 3, 2, "genes", run=_count_run("DESeq2"))
    assert "mean normalised counts (log)" in deseq2
    assert "shrunken log2 fold change" in deseq2
    assert "variance-stabilised counts" in deseq2
    assert "log2 CPM" not in deseq2

    micro_run = {"input": {"type": "microarray"}}
    micro = mhr._figure_groups(figs, 3, 2, "genes", is_micro=True, run=micro_run)
    assert "log2 fold change (limma, unshrunken)" in micro
    assert "log2 expression (array intensity)" in micro
    assert "genes with the lowest measured expression" in micro
    assert "log2 CPM" not in micro
    # The caption path is reachable without a run payload (older callers pass is_micro only).
    assert "log2 expression (array intensity)" in mhr._panel(figs, "pca", "A", is_micro=True)


def test_basemean_glossary_names_the_scale_the_engine_actually_wrote(mhr) -> None:
    voom = mhr._route_gloss(_count_run("limma-voom"))
    assert "log2 counts per million" in voom["basemean"]
    assert "read count" not in voom["basemean"]
    assert "log2 counts per million" in voom["ma"]
    # DESeq2 keeps the count wording; microarray keeps the intensity wording.
    assert "read count" in mhr._route_gloss(_count_run("DESeq2"))["basemean"]
    assert "expression intensity" in mhr._route_gloss({"input": {"type": "microarray"}})["basemean"]
