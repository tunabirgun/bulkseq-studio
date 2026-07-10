from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

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
