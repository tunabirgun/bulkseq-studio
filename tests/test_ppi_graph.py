import json

import pandas as pd

from app.core.ppi_graph import build_ppi_cytoscape_json


def _write(root, nodes, edges, deseq=None, norm=None):
    (root / "results" / "networks").mkdir(parents=True, exist_ok=True)
    (root / "results" / "deseq2").mkdir(parents=True, exist_ok=True)
    (root / "results" / "export").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(nodes).to_csv(root / "results" / "networks" / "string_ppi_nodes.csv", index=False)
    pd.DataFrame(edges).to_csv(root / "results" / "networks" / "string_ppi_edges.csv", index=False)
    if deseq is not None:
        pd.DataFrame(deseq).to_csv(root / "results" / "deseq2" / "deseq2_results.csv", index=False)
    if norm is not None:
        pd.DataFrame(norm).to_csv(root / "results" / "export" / "normalized_expression_matrix.csv", index=False)


def test_empty_when_no_network(tmp_path):
    g = build_ppi_cytoscape_json(tmp_path)
    assert g["elements"]["nodes"] == [] and g["elements"]["edges"] == []
    json.dumps(g, allow_nan=False)  # must be valid JSON


def test_assemble_basic_and_json_safe(tmp_path):
    nodes = [
        {"id": "GALE", "module": 1, "degree": 3, "betweenness": 5, "log2FC": -1.1},
        {"id": "SESB", "module": 2, "degree": 1, "betweenness": 0, "log2FC": -3.0},
        {"id": "ORPHAN", "module": 1, "degree": 1, "betweenness": 0, "log2FC": float("nan")},
    ]
    edges = [{"source": "GALE", "target": "SESB", "weight": 0.52}]
    deseq = [
        {"gene_id": "FBgn01", "symbol": "gale", "baseMean": 50.0, "log2FoldChange": -1.1, "padj": 1e-4},
        {"gene_id": "FBgn02", "symbol": "GALE", "baseMean": 500.0, "log2FoldChange": -1.2, "padj": 2e-5},
        {"gene_id": "FBgn03", "symbol": "SESB", "baseMean": 30.0, "log2FoldChange": -3.0, "padj": 1e-3},
    ]
    norm = [
        {"gene_id": "FBgn02", "s1": 8.0, "s2": 10.0},  # GALE max-baseMean row -> mean 9.0
        {"gene_id": "FBgn01", "s1": 1.0, "s2": 1.0},
        {"gene_id": "FBgn03", "s1": 4.0, "s2": 6.0},   # SESB -> mean 5.0
    ]
    _write(tmp_path, nodes, edges, deseq, norm)
    g = build_ppi_cytoscape_json(tmp_path)

    # (vi) valid JSON with no NaN
    s = json.dumps(g, allow_nan=False)
    assert "NaN" not in s

    by = {n["data"]["id"]: n["data"] for n in g["elements"]["nodes"]}
    assert g["meta"]["node_count"] == 3 and g["meta"]["edge_count"] == 1

    # (iii) dedup picks max-baseMean row (FBgn02 -> meanExpr 9.0); (iv) case-insensitive
    assert by["GALE"]["padj"] == 2e-5
    assert by["GALE"]["baseMean"] == 500.0
    assert by["GALE"]["meanExpr"] == 9.0

    # (ii) numerics are numbers not strings
    assert isinstance(by["SESB"]["degree"], int)
    assert isinstance(by["SESB"]["meanExpr"], float)
    assert isinstance(g["elements"]["edges"][0]["data"]["weight"], float)

    # (v) DE-less / NaN-LFC node emits with null attrs, not crash
    assert by["ORPHAN"]["log2FoldChange"] is None
    assert by["ORPHAN"]["padj"] is None
    assert by["ORPHAN"]["meanExpr"] is None


def test_empty_nodes_file_is_valid(tmp_path):
    # A degraded run writes a header-only nodes file (0 rows).
    _write(tmp_path, nodes=[], edges=[])
    g = build_ppi_cytoscape_json(tmp_path)
    assert g["meta"]["node_count"] == 0
    json.dumps(g, allow_nan=False)


def test_score_floor_is_data_minimum_not_sentinel(tmp_path):
    nodes = [{"id": "A", "module": 1, "degree": 1, "betweenness": 0, "log2FC": 1.0},
             {"id": "B", "module": 1, "degree": 1, "betweenness": 0, "log2FC": -1.0}]
    # raw STRING combined scores (>1): floor must be the true minimum, not a 1.0 sentinel
    edges = [{"source": "A", "target": "B", "weight": 700},
             {"source": "B", "target": "A", "weight": 900}]
    _write(tmp_path, nodes, edges)
    g = build_ppi_cytoscape_json(tmp_path)
    assert g["meta"]["score_floor"] == 700.0


def test_score_floor_zero_when_edges_lack_weights(tmp_path):
    nodes = [{"id": "A", "module": 1, "degree": 1, "betweenness": 0, "log2FC": 1.0},
             {"id": "B", "module": 1, "degree": 1, "betweenness": 0, "log2FC": -1.0}]
    (tmp_path / "results" / "networks").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(nodes).to_csv(tmp_path / "results" / "networks" / "string_ppi_nodes.csv", index=False)
    pd.DataFrame([{"source": "A", "target": "B"}]).to_csv(
        tmp_path / "results" / "networks" / "string_ppi_edges.csv", index=False)
    g = build_ppi_cytoscape_json(tmp_path)
    # non-empty edges with no weights -> floor 0.0 so the slider never hides every edge
    assert g["meta"]["edge_count"] == 1
    assert g["meta"]["score_floor"] == 0.0


def test_join_by_gene_id_when_symbols_absent(tmp_path):
    # Symbol-less genome (e.g. Fusarium): nodes are gene_ids, DE symbols are NA.
    nodes = [{"id": "FGSG_001", "module": 1, "degree": 2, "betweenness": 3, "log2FC": 4.2}]
    deseq = [{"gene_id": "FGSG_001", "symbol": float("nan"), "baseMean": 80.0,
              "log2FoldChange": 4.2, "padj": 1e-9}]
    norm = [{"gene_id": "FGSG_001", "s1": 10.0, "s2": 12.0}]
    _write(tmp_path, nodes, [], deseq, norm)
    d = build_ppi_cytoscape_json(tmp_path)["elements"]["nodes"][0]["data"]
    assert d["padj"] == 1e-9          # joined by gene_id, not symbol
    assert d["baseMean"] == 80.0
    assert d["meanExpr"] == 11.0


def test_dedup_skips_nan_basemean_row(tmp_path):
    nodes = [{"id": "GENE", "module": 1, "degree": 1, "betweenness": 0, "log2FC": 2.0}]
    deseq = [{"gene_id": "g1", "symbol": "GENE", "baseMean": float("nan"), "log2FoldChange": 9.9, "padj": 0.5},
             {"gene_id": "g2", "symbol": "GENE", "baseMean": 100.0, "log2FoldChange": 2.0, "padj": 1e-3}]
    norm = [{"gene_id": "g2", "s1": 5.0, "s2": 7.0}]
    _write(tmp_path, nodes, [], deseq, norm)
    d = {n["data"]["id"]: n["data"] for n in build_ppi_cytoscape_json(tmp_path)["elements"]["nodes"]}["GENE"]
    assert d["padj"] == 1e-3          # the valid row wins, not the NaN-baseMean row
    assert d["baseMean"] == 100.0
    assert d["meanExpr"] == 6.0
