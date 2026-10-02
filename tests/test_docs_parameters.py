"""Independent arithmetic checks for the synthetic parameter illustration."""
from html.parser import HTMLParser
from pathlib import Path
import math
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class Plot(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.points, self.controls, self.counts = [], {}, {}
        self.domain, self.bounds, self.active = {}, (), None
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if "data-parameter-point" in values:
            self.points.append(values)
        if tag == "svg" and "parameter-canvas" in values.get("class", ""):
            self.domain = values
        if tag == "path" and "parameter-axis" in values.get("class", ""):
            self.bounds = tuple(map(float, re.findall(r"-?\d+(?:\.\d+)?", values["d"])))
        if tag == "input" and values.get("id") in {"p-cutoff", "fc-cutoff"}:
            self.controls[values["id"]] = values
        for key in ("below", "selected", "higher", "lower"):
            if f"data-{key}-count" in values:
                self.active = (tag, key)
                self.counts[key] = ""

    def handle_data(self, data):
        if self.active:
            self.counts[self.active[1]] += data

    def handle_endtag(self, tag):
        if self.active and tag == self.active[0]:
            self.active = None


def inspect_plot(text):
    plot = Plot(text)
    failures = []
    if not plot.points or len(plot.bounds) != 4:
        return ["Missing points or plot axes"]
    left, top, bottom, right = plot.bounds
    dx, dy = float(plot.domain["data-domain-x"]), float(plot.domain["data-domain-y"])
    alpha = float(plot.controls["p-cutoff"]["value"])
    cutoff = float(plot.controls["fc-cutoff"]["value"])
    counts = dict.fromkeys(("below", "selected", "higher", "lower"), 0)
    for point in plot.points:
        effect, padj = float(point["data-log2fc"]), float(point["data-padj"])
        if not (math.isfinite(effect) and effect != 0 and 0 < padj <= 1):
            failures.append("Invalid synthetic result value")
            continue
        expected_x = (left + right) / 2 + effect * (right - left) / (2 * dx)
        expected_y = bottom - (-math.log10(padj)) * (bottom - top) / dy
        if abs(float(point["cx"]) - expected_x) > .011 or abs(float(point["cy"]) - expected_y) > .011:
            failures.append("Point coordinates disagree with the result values")
        if not (left <= expected_x <= right and top <= expected_y <= bottom):
            failures.append("Point is outside the plot domain")
        below = padj < alpha
        both = below and abs(effect) >= cutoff
        counts["below"] += below
        counts["selected"] += both
        counts["higher"] += both and effect > 0
        counts["lower"] += both and effect < 0
    if plot.counts != {key: str(value) for key, value in counts.items()}:
        failures.append("Static counts disagree with the plotted result table")
    return failures


@pytest.fixture
def source():
    return (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def test_parameter_illustration_matches_its_data(source):
    assert inspect_plot(source) == []


def test_demo_defaults_match_application_defaults(source):
    plot = Plot(source)
    defaults = yaml.safe_load((ROOT / "app" / "data" / "default_config.yaml").read_text(encoding="utf-8"))["deseq2"]
    assert float(plot.controls["p-cutoff"]["value"]) == defaults["alpha"]
    assert float(plot.controls["fc-cutoff"]["value"]) == defaults["lfc_threshold"]


@pytest.mark.parametrize("defect", ["coordinate", "count", "probability"])
def test_parameter_gate_rejects_known_defects(source, defect):
    if defect == "coordinate":
        changed = re.sub(r'(data-parameter-point[^>]*?\bcx=")[^"]+', r"\g<1>999", source, count=1)
    elif defect == "count":
        changed = re.sub(r'(data-selected-count>)[^<]+', r"\g<1>999", source, count=1)
    else:
        changed = re.sub(r'(data-parameter-point[^>]*?data-padj=")[^"]+', r"\g<1>0", source, count=1)
    assert changed != source
    assert inspect_plot(changed)
