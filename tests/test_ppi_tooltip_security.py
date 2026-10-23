from __future__ import annotations

import csv
import html
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tooltip_probe(tmp_path_factory):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path_factory.mktemp("tooltip"))],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=45,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("field", ["symbol", "id", "degree", "module"])
def test_tooltip_csv_values_are_literal_text(tooltip_probe, field):
    result = tooltip_probe[field]
    assert result["executed"] is False, result
    assert result["images"] == 0, result
    assert result["payload"] in result["text"], result
    assert result["file_read"] == "", result


def test_tooltip_preserves_ordinary_values(tooltip_probe):
    result = tooltip_probe["safe"]
    assert result["text"] == "GENE1 & valuelog2FC: 1.25padj: n/amean expr: n/adegree: 2  module: 3"
    assert result["labels"] == ["log2FC:", "padj:", "mean expr:", "degree:", "module:"]
    assert result["images"] == 0


def test_viewer_vendored_layout_and_exports_remain_available(tooltip_probe):
    assert tooltip_probe["png_export"] is True
    assert tooltip_probe["svg_export"] is True


def _probe(root):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from app.core.ppi_graph import NODES_CSV, build_ppi_cytoscape_json
    from app.ui.ppi_viewer import PpiViewer

    app = QApplication.instance() or QApplication([])
    viewer = PpiViewer()
    assert viewer.available
    viewer.resize(900, 600)
    viewer.show()
    ready = QEventLoop()
    viewer.view.loadFinished.connect(lambda _ok: ready.quit())
    QTimer.singleShot(10000, ready.quit)
    if not viewer._ready:
        ready.exec()
    assert viewer._ready

    def js(expression):
        values = []
        loop = QEventLoop()
        viewer.view.page().runJavaScript(expression, lambda value: (values.append(value), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        assert values, expression
        return values[0]

    assert js("Boolean(window.PPI && window.cytoscapeFcose && window.cytoscapeSvg)")
    secret = root / "unrelated-local-file.txt"
    secret.write_text("SYNTHETIC_LOCAL_CONTENT", encoding="utf-8")
    attack = (
        "window.__tooltipExecuted=true;"
        "const x=new XMLHttpRequest();"
        f"x.open('GET',{json.dumps(secret.as_uri())});"
        "x.onload=()=>window.__tooltipRead=x.responseText;x.send()"
    )
    payload = '<img src="missing-tooltip-image" onerror="' + html.escape(attack, quote=True) + '">'
    results = {}
    for field in ("symbol", "id", "degree", "module", "safe"):
        row = {"id": "GENE1 & value", "degree": 2, "module": 3, "log2FC": 1.25}
        if field != "safe":
            row["id" if field in ("symbol", "id") else field] = payload
        csv_path = root / field / NODES_CSV
        csv_path.parent.mkdir(parents=True)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=row.keys())
            writer.writeheader()
            writer.writerow(row)
        elements = build_ppi_cytoscape_json(root / field)["elements"]
        if field == "id":
            elements["nodes"][0]["data"]["symbol"] = ""
        rendered = []
        viewer.load_graph(elements, on_rendered=rendered.append)
        for _ in range(200):
            if rendered:
                break
            QTest.qWait(25)
        assert rendered and json.loads(rendered[0])["nodes"] == 1
        js("window.__tooltipExecuted=false;window.__tooltipRead='';cy.nodes()[0].emit('mouseover');true")
        QTest.qWait(200)
        results[field] = json.loads(js("""JSON.stringify({
            text: document.getElementById('tip').textContent,
            labels: Array.from(document.querySelectorAll('#tip .k'), e => e.textContent),
            images: document.querySelectorAll('#tip img').length,
            executed: window.__tooltipExecuted,
            file_read: window.__tooltipRead
        })"""))
        results[field]["payload"] = payload

    results["png_export"] = js("PPI.exportImage('png','#ffffff').startsWith('data:image/png')")
    results["svg_export"] = js("PPI.exportImage('svg','#ffffff').includes('<svg')")
    print(json.dumps(results))
    viewer.close()
    app.processEvents()


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT))
    _probe(Path(sys.argv[1]))
