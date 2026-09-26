from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

import app.ui.ppi_viewer as ppi_viewer_module
from app.ui.ppi_viewer import PpiViewer


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def static_viewer(monkeypatch, qapp):
    monkeypatch.setattr(ppi_viewer_module, "WEBENGINE_AVAILABLE", False)
    viewer = PpiViewer()
    viewer.resize(520, 360)
    viewer.show()
    qapp.processEvents()
    yield viewer
    viewer.close()
    viewer.deleteLater()
    qapp.processEvents()


def _empty_label(viewer: PpiViewer) -> QLabel:
    label = viewer.findChild(QLabel, "ppiEmptyState")
    assert label is not None
    return label


def test_empty_state_is_visible_and_non_interactive_before_load(static_viewer):
    label = _empty_label(static_viewer)

    assert static_viewer.empty_state_visible
    assert static_viewer._empty_card.width() >= 380
    assert label.isVisibleTo(static_viewer)
    assert "No PPI network loaded" in label.text()
    assert "Network construction" in label.text()
    assert label.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_static_network_hides_empty_state_and_clear_restores_it(static_viewer, tmp_path, qapp):
    figure = tmp_path / "synthetic_ppi.png"
    image = QImage(48, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor("#2c6fb6"))
    assert image.save(str(figure))

    static_viewer.load_static(figure)
    qapp.processEvents()

    label = _empty_label(static_viewer)
    assert not static_viewer.empty_state_visible
    assert not label.isVisibleTo(static_viewer)

    static_viewer.clear_network("No network is available for this project.")
    qapp.processEvents()

    assert static_viewer.empty_state_visible
    assert label.isVisibleTo(static_viewer)
    assert label.text() == "No network is available for this project."
    assert not static_viewer._fallback._has_image


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _FakeSettings:
    def setAttribute(self, *_args) -> None:
        pass


class _FakePage:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def runJavaScript(self, script: str, callback=None) -> None:
        self.scripts.append(script)
        if callback is not None:
            callback("")


class _FakeWebEngineView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.loadFinished = _Signal()
        self._settings = _FakeSettings()
        self._page = _FakePage()
        self.url = None

    def settings(self) -> _FakeSettings:
        return self._settings

    def setUrl(self, url) -> None:
        self.url = url

    def page(self) -> _FakePage:
        return self._page


def test_web_network_hides_empty_state_and_keeps_canvas_interactive(monkeypatch, tmp_path, qapp):
    html = tmp_path / "viewer.html"
    html.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(ppi_viewer_module, "WEBENGINE_AVAILABLE", True)
    monkeypatch.setattr(ppi_viewer_module, "QWebEngineView", _FakeWebEngineView)
    monkeypatch.setattr(ppi_viewer_module, "viewer_html_path", lambda: html)

    viewer = PpiViewer()
    viewer.resize(520, 360)
    viewer.show()
    qapp.processEvents()
    label = _empty_label(viewer)
    assert viewer.empty_state_visible

    elements = {"nodes": [{"data": {"id": "GENE1"}}], "edges": []}
    viewer.load_graph(elements)
    viewer._on_loaded(True)
    qapp.processEvents()

    assert not viewer.empty_state_visible
    assert not label.isVisibleTo(viewer)
    assert viewer._stack.currentWidget() is viewer.view
    assert viewer.view.isVisibleTo(viewer)
    assert viewer.view.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert viewer.view.accessibleName() == "Interactive protein interaction network"
    assert "arrow keys" in viewer.view.accessibleDescription()
    assert any("PPI.render" in script and "GENE1" in script for script in viewer.view.page().scripts)
    assert label.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    viewer.clear_network()
    qapp.processEvents()
    assert viewer.empty_state_visible
    assert label.isVisibleTo(viewer)
    assert '"nodes": []' in viewer.view.page().scripts[-1]

    viewer.close()
    viewer.deleteLater()
    qapp.processEvents()


def test_load_graph_forwards_the_post_prune_render_callback(monkeypatch, tmp_path, qapp):
    # I2: PPI.render() returns the post-prune {nodes, edges} counts actually drawn (viewer.js
    # prunes above its node-display budget); load_graph's on_rendered callback must receive
    # that JSON string via runJavaScript's own callback, both on the immediate (ready) path
    # and the queued (not-yet-ready) path.
    html = tmp_path / "viewer.html"
    html.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(ppi_viewer_module, "WEBENGINE_AVAILABLE", True)
    monkeypatch.setattr(ppi_viewer_module, "QWebEngineView", _FakeWebEngineView)
    monkeypatch.setattr(ppi_viewer_module, "viewer_html_path", lambda: html)

    elements = {
        "nodes": [{"data": {"id": "A"}}, {"data": {"id": "B"}}],
        "edges": [{"data": {"source": "A", "target": "B"}}],
    }

    # Ready path: the page is already loaded, so _inject runs immediately.
    viewer = PpiViewer()
    viewer._on_loaded(True)
    qapp.processEvents()
    viewer.view.page().runJavaScript = lambda script, callback=None: (
        callback('{"nodes": 2, "edges": 1}') if callback else None)
    received: list[str] = []
    viewer.load_graph(elements, on_rendered=received.append)
    assert received == ['{"nodes": 2, "edges": 1}']

    # Queued path: load_graph is called before the page reports loadFinished, so the
    # callback must be stashed and fired once _on_loaded actually injects it.
    viewer2 = PpiViewer()
    qapp.processEvents()
    viewer2.view.page().runJavaScript = lambda script, callback=None: (
        callback('{"nodes": 2, "edges": 1}') if callback else None)
    received2: list[str] = []
    viewer2.load_graph(elements, on_rendered=received2.append)
    assert received2 == []  # not yet injected -- the page has not loaded
    viewer2._on_loaded(True)
    qapp.processEvents()
    assert received2 == ['{"nodes": 2, "edges": 1}']

    for v in (viewer, viewer2):
        v.close()
        v.deleteLater()
    qapp.processEvents()


def test_web_assets_expose_keyboard_and_assistive_technology_route():
    asset_root = Path(__file__).parents[1] / "app" / "assets" / "web" / "ppi"
    html = (asset_root / "viewer.html").read_text(encoding="utf-8")
    script = (asset_root / "viewer.js").read_text(encoding="utf-8")

    assert 'id="cy" tabindex="0" role="application"' in html
    assert 'aria-describedby="keyboard-help network-status"' in html
    assert 'aria-controls="node-list"' in html
    assert 'aria-owns="node-list"' in html
    assert 'id="node-list"' in html and 'role="listbox"' in html
    assert 'id="network-status"' in html and 'aria-live="polite"' in html
    assert 'container.addEventListener("keydown"' in script
    for key in ("ArrowRight", "ArrowLeft", "Home", "End", "Enter", "Escape"):
        assert f'key === "{key}"' in script
    assert 'aria-activedescendant' in script
    assert 'rebuildAccessibleNodeList()' in script
    assert '"opacity": 0.82' in script


def test_real_webengine_keyboard_traversal_updates_accessible_state():
    """Exercise the actual vendored page in an isolated Chromium process."""
    project_root = Path(__file__).parents[1]
    probe = textwrap.dedent(
        '''
        import json
        from PySide6.QtCore import QEventLoop, QTimer, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget
        from app.ui.ppi_viewer import PpiViewer

        app = QApplication.instance() or QApplication([])
        host = QWidget()
        layout = QVBoxLayout(host)
        before = QPushButton("Before network")
        viewer = PpiViewer()
        after = QPushButton("After network")
        layout.addWidget(before)
        layout.addWidget(viewer, 1)
        layout.addWidget(after)
        host.resize(900, 600)
        host.show()
        ready_loop = QEventLoop()
        QTimer.singleShot(5000, ready_loop.quit)
        if viewer.view:
            viewer.view.loadFinished.connect(lambda ok: ready_loop.quit() if ok else None)
        ready_loop.exec()
        assert viewer.available and viewer._ready
        viewer.load_graph({
            "nodes": [
                {"data": {"id": "A", "symbol": "GENEA", "log2FoldChange": 1.2,
                           "degree": 1, "module": 1, "meanExpr": 20, "padj": 0.01}},
                {"data": {"id": "B", "symbol": "GENEB", "log2FoldChange": -0.8,
                           "degree": 1, "module": 1, "meanExpr": 18, "padj": 0.03}},
            ],
            "edges": [{"data": {"id": "A-B", "source": "A", "target": "B", "weight": 0.8}}],
        })
        render_loop = QEventLoop()
        QTimer.singleShot(900, render_loop.quit)
        render_loop.exec()
        assert viewer.view.focusPolicy() == Qt.FocusPolicy.StrongFocus
        before.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(before, Qt.Key.Key_Tab)
        app.processEvents()
        assert viewer.view.hasFocus(), type(app.focusWidget()).__name__
        # These are real Qt key events delivered through the tab-reached web
        # view, rather than JavaScript-dispatched stand-ins. QWebEngine owns an
        # internal render-widget focus proxy; target it explicitly so a busy
        # suite cannot leave the events on the outer wrapper between focus hops.
        web_focus = viewer.view.focusProxy() or app.focusWidget()
        assert web_focus is not None
        assert web_focus.hasFocus()

        def js_value(expression):
            values = []
            wait = QEventLoop()
            viewer.view.page().runJavaScript(expression, lambda value: (values.append(value), wait.quit()))
            QTimer.singleShot(1000, wait.quit)
            wait.exec()
            assert values, expression
            return values[0]

        def wait_js(expression, timeout_ms=3000):
            elapsed = 0
            while elapsed < timeout_ms:
                if js_value(expression):
                    return
                QTest.qWait(40)
                elapsed += 40
            raise AssertionError(expression)

        # The Chromium render process receives focus asynchronously after Qt's
        # outer QWebEngineView. Wait for the actual canvas before sending real
        # key events so runner load cannot turn the accessibility gate into a
        # fixed-sleep race.
        wait_js("document.activeElement === document.getElementById('cy')")
        QTest.keyClick(web_focus, Qt.Key.Key_Right, delay=25)
        wait_js("document.getElementById('cy').getAttribute('aria-activedescendant') === 'ppi-node-0'")
        QTest.keyClick(web_focus, Qt.Key.Key_Return, delay=25)
        wait_js("document.querySelectorAll('#node-list [aria-selected=true]').length === 1")
        app.processEvents()
        result = []
        viewer.view.page().runJavaScript(
            """(() => {
                const canvas = document.getElementById('cy');
                return JSON.stringify({
                    active: canvas.getAttribute('aria-activedescendant'),
                    status: document.getElementById('network-status').textContent,
                    selected: document.querySelectorAll('#node-list [aria-selected=true]').length,
                    nodes: document.querySelectorAll('#node-list [role=option]').length,
                    focus: document.activeElement === canvas,
                });
            })()""",
            result.append,
        )
        callback_loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: callback_loop.quit() if result else None)
        poll.start()
        QTimer.singleShot(2000, callback_loop.quit)
        callback_loop.exec()
        poll.stop()
        assert result
        print(result[0])
        host.close()
        app.processEvents()
        '''
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = __import__("json").loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {
        "active": "ppi-node-0",
        "focus": True,
        "nodes": 2,
        "selected": 1,
        "status": "GENEA, log2 fold change 1.20, degree 1, module 1, 1 neighbour",
    }


def test_real_webengine_render_callback_reports_the_post_prune_counts():
    """I2: confirm runJavaScript's callback actually fires under offscreen Qt for
    PPI.render(), and that it reports viewer.js's real BUDGET=300 prune -- not a fake page's
    canned reply."""
    project_root = Path(__file__).parents[1]
    probe = textwrap.dedent(
        '''
        import json
        from PySide6.QtCore import QEventLoop, QTimer
        from app.ui.ppi_viewer import PpiViewer

        app_qt = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
        app = app_qt.instance() or app_qt([])
        viewer = PpiViewer()
        ready_loop = QEventLoop()
        QTimer.singleShot(5000, ready_loop.quit)
        if viewer.view:
            viewer.view.loadFinished.connect(lambda ok: ready_loop.quit() if ok else None)
        ready_loop.exec()
        assert viewer.available and viewer._ready

        n = 350  # above viewer.js's BUDGET=300, so this must prune
        nodes = [{"data": {"id": f"G{i}", "symbol": f"G{i}", "degree": i}} for i in range(n)]
        edges = [{"data": {"source": f"G{i}", "target": f"G{i+1}", "weight": 0.5}}
                 for i in range(n - 1)]

        received = []
        viewer.load_graph({"nodes": nodes, "edges": edges}, on_rendered=received.append)
        wait = QEventLoop()
        QTimer.singleShot(4000, wait.quit)
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: wait.quit() if received else None)
        poll.start()
        wait.exec()
        assert received, "runJavaScript callback never fired"
        print(received[0])
        '''
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = __import__("json").loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {"nodes": 300, "edges": 299}


def test_empty_state_repaints_light_dark_light(static_viewer, qapp):
    light = {
        "bg": "#ECEFF3",
        "surface": "#FFFFFF",
        "text": "#1F2933",
        "muted": "#4F5A67",
        "edge": "#D7DEE6",
    }
    dark = {
        "bg": "#20242A",
        "surface": "#2A3038",
        "text": "#E8EAED",
        "muted": "#AEB6C2",
        "edge": "#414A57",
    }

    def dominant_rendered_colour(widget: QWidget) -> str:
        image = widget.grab().toImage()
        colours = Counter(
            image.pixelColor(x, y).name()
            for y in range(image.height())
            for x in range(image.width())
        )
        return colours.most_common(1)[0][0]

    def rendered_colour_count(widget: QWidget, colour: str) -> int:
        image = widget.grab().toImage()
        return sum(
            image.pixelColor(x, y).name() == colour
            for y in range(image.height())
            for x in range(image.width())
        )

    def rendered_colours() -> tuple[str, str, str, str, int, int]:
        qapp.processEvents()
        page = static_viewer._empty_page
        card = static_viewer._empty_card
        title = static_viewer._empty_title
        body = static_viewer._empty_label
        title_colour = title.palette().color(QPalette.ColorRole.WindowText).name()
        body_colour = body.palette().color(QPalette.ColorRole.WindowText).name()
        return (
            dominant_rendered_colour(page),
            dominant_rendered_colour(card),
            title_colour,
            body_colour,
            rendered_colour_count(title, title_colour),
            rendered_colour_count(body, body_colour),
        )

    static_viewer.update_theme(light)
    first_light = rendered_colours()
    assert first_light[:4] == ("#eceff3", "#ffffff", "#1f2933", "#4f5a67")
    assert first_light[4] > 0 and first_light[5] > 0

    static_viewer.update_theme(dark)
    dark_state = rendered_colours()
    assert dark_state[:4] == ("#20242a", "#2a3038", "#e8eaed", "#aeb6c2")
    assert dark_state[4] > 0 and dark_state[5] > 0

    static_viewer.update_theme(light)
    assert rendered_colours() == first_light


@pytest.mark.parametrize("message", [
    None,
    "The interactive PPI viewer could not be loaded.",
    "No PPI network is available yet. Run the pipeline or rebuild the STRING network.",
    "STRING returned no interactions for this project. Try a supported organism, "
    "check gene-symbol mapping, or adjust the Network construction threshold.",
])
@pytest.mark.parametrize("size", [(520, 360), (940, 620), (1400, 900)])
def test_empty_state_message_is_never_clipped(static_viewer, qapp, message, size):
    static_viewer.resize(*size)
    static_viewer.set_empty_state(message)
    qapp.processEvents()
    label = _empty_label(static_viewer)
    card = static_viewer._empty_card

    assert label.heightForWidth(label.width()) <= label.height(), (label.width(), label.height())
    assert card.geometry().bottom() <= static_viewer._empty_page.height()


def _synthetic_network() -> dict:
    """A clique, a hub with leaves, a chain and isolated pairs: the shapes STRING returns."""
    nodes, edges = [], []

    def node(gene_id: str, lfc: float) -> None:
        nodes.append({"data": {"id": gene_id, "symbol": gene_id, "log2FoldChange": lfc}})

    clique = [f"CLQ{i}" for i in range(10)]
    for i, gene in enumerate(clique):
        node(gene, 1.5 if i % 2 else -1.5)
    edges += [{"data": {"source": a, "target": b, "weight": 0.9}}
              for i, a in enumerate(clique) for b in clique[i + 1:]]
    node("HUB", 2.5)
    for i in range(8):
        node(f"LEAF{i}", -0.8)
        edges.append({"data": {"source": "HUB", "target": f"LEAF{i}", "weight": 0.7}})
    chain = [f"CHN{i}" for i in range(6)]
    for gene in chain:
        node(gene, 0.6)
    edges += [{"data": {"source": a, "target": b, "weight": 0.6}} for a, b in zip(chain, chain[1:])]
    for i in range(12):
        node(f"PRA{i}", 1.1)
        node(f"PRB{i}", -1.1)
        edges.append({"data": {"source": f"PRA{i}", "target": f"PRB{i}", "weight": 0.5}})
    degree = Counter(e["data"][end] for e in edges for end in ("source", "target"))
    for item in nodes:
        item["data"]["degree"] = degree[item["data"]["id"]]
    return {"nodes": nodes, "edges": edges}


def test_real_viewer_default_layout_keeps_nodes_and_labels_apart(tmp_path):
    import json

    probe = textwrap.dedent(
        '''
        import json, sys
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView

        app = QApplication.instance() or QApplication([])
        view = QWebEngineView()
        view.resize(918, 604)
        view.show()
        loaded = []
        loop = QEventLoop()
        view.loadFinished.connect(lambda ok: (loaded.append(bool(ok)), loop.quit()))
        QTimer.singleShot(30000, loop.quit)
        view.load(QUrl.fromLocalFile(__VIEWER__))
        loop.exec()
        assert loaded == [True], loaded

        def js(expression, wait_ms=15000):
            values = []
            done = QEventLoop()
            view.page().runJavaScript(expression, lambda value: (values.append(value), done.quit()))
            QTimer.singleShot(wait_ms, done.quit)
            done.exec()
            assert values, expression
            return values[0]

        js("PPI.render(" + json.dumps({"elements": __GRAPH__}) + ")")
        settle = QEventLoop()
        QTimer.singleShot(1500, settle.quit)
        settle.exec()
        print(js("""JSON.stringify((function () {
            var boxes = cy.nodes().map(function (n) { return n.boundingBox({ includeLabels: true }); });
            var overlaps = 0;
            for (var i = 0; i < boxes.length; i++) for (var j = i + 1; j < boxes.length; j++) {
              var a = boxes[i], b = boxes[j];
              if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) overlaps++;
            }
            return { nodes: cy.nodes().length, overlaps: overlaps };
        })())"""))
        view.close()
        app.processEvents()
        '''
    ).replace("__VIEWER__", json.dumps(str(ppi_viewer_module.viewer_html_path()))).replace(
        "__GRAPH__", json.dumps(_synthetic_network()))
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[1],
                               env=env, capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["nodes"] == len(_synthetic_network()["nodes"])
    assert result["overlaps"] == 0, result
