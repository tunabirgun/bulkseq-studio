"""Independent consistency checks for the public synthetic learning examples."""
from collections import Counter
from copy import deepcopy
from html.parser import HTMLParser
import json
import math
from pathlib import Path
from statistics import mean, stdev

import pytest

ROOT = Path(__file__).resolve().parents[1]


def validate_examples(model, data):
    counts = model["counts"]
    assert len(counts) == len(model["genes"])
    assert all(len(row) == len(model["samples"]) for row in counts)
    assert all(isinstance(x, int) and x >= 0 for row in counts for x in row)
    filtering = data["filtering"]
    assert filtering["counts"] == counts
    assert filtering["samples"] == model["samples"] == data["samples"]
    assert filtering["required_samples"] == min(Counter(s.rstrip("0123456789") for s in model["samples"]).values())
    assert filtering["genes"] == model["genes"]
    expression = dict(zip(model["expression_genes"], model["expression"]))
    de = {row["id"]: row for row in model["models"]["deseq2"]}
    heat = data["heatmap"]
    ranking = sorted(expression, key=lambda gene: de[gene]["padj"])
    assert heat["genes"] == ranking[:len(heat["genes"])]
    for gene, values, z, adjusted in zip(heat["genes"], heat["values"], heat["z"], heat["padj"]):
        assert values == expression[gene]
        assert adjusted == de[gene]["padj"]
        expected = [(v - mean(values)) / stdev(values) for v in values]
        assert z == pytest.approx(expected, abs=1e-10)
    for state in data["pca"].values():
        order = sorted(expression, key=lambda gene: stdev(expression[gene]), reverse=True)
        assert state["genes"] == order[:state["count"]]
        assert len(state["scores"]) == len(model["samples"])
        assert all(math.isfinite(v) for point in state["scores"] for v in point)
        assert 0 < sum(state["percent"]) <= 100
    for name, rows in model["models"].items():
        by_id = {row["id"]: row for row in rows}
        assert data["engines"][name]["tested"] == len(rows)
        for row in data["engines"][name]["genes"]:
            expected = by_id.get(row["id"], {"id": row["id"], "log2fc": None, "padj": None})
            assert row == expected
    for rows in data["enrichment"].values():
        pvalues = []
        for row in rows:
            n, k, size, overlap = (row[key] for key in ("universe", "term_size", "foreground", "overlap"))
            p = sum(math.comb(k, x) * math.comb(n-k, size-x) for x in range(overlap, min(k, size)+1)) / math.comb(n, size)
            assert row["pvalue"] == pytest.approx(p, rel=1e-10)
            pvalues.append(p)
        ranked = sorted(range(len(rows)), key=pvalues.__getitem__)
        adjusted = [0.] * len(rows)
        ceiling = 1.
        for rank, index in reversed(list(enumerate(ranked, start=1))):
            ceiling = min(ceiling, pvalues[index] * len(rows) / rank)
            adjusted[index] = ceiling
        assert [r["padj"] for r in rows] == pytest.approx(adjusted, rel=1e-10)


@pytest.fixture
def examples():
    return tuple(json.loads((ROOT / "docs_src" / name).read_text(encoding="utf-8"))
                 for name in ("model-examples.json", "learning-examples.json"))


def test_learning_examples_agree_with_source_and_independent_arithmetic(examples):
    validate_examples(*examples)


@pytest.mark.parametrize("defect", ["count", "zscore", "enrichment", "engine", "pca"])
def test_learning_gate_rejects_corruption(examples, defect):
    model, data = deepcopy(examples)
    if defect == "count":
        data["filtering"]["counts"][0][0] = -1
    elif defect == "zscore":
        data["heatmap"]["z"][0][0] = 99
    elif defect == "enrichment":
        data["enrichment"]["100"][0]["padj"] = .99
    elif defect == "engine":
        data["engines"]["deseq2"]["genes"][0]["log2fc"] = 99
    else:
        data["pca"]["100"]["genes"].reverse()
    with pytest.raises(AssertionError):
        validate_examples(model, data)


def test_home_embeds_only_the_verified_volcano(examples):
    class FixtureParser(HTMLParser):
        points = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if "data-parameter-point" in values:
                self.points.append((values["data-id"], float(values["data-log2fc"]), float(values["data-padj"])))

    parser = FixtureParser()
    source = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    parser.feed(source)
    expected = [(r["id"], r["log2fc"], r["padj"]) for r in examples[0]["models"]["deseq2"] if r["padj"] is not None and r["padj"] > 0 and r["log2fc"] is not None]
    assert parser.points == expected
    assert "data-learning-data" not in source
    assert "lesson-picker" not in source
    assert 'src="assets/learning.js"' not in source
