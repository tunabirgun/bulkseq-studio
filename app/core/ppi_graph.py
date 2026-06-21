"""Assemble the interactive PPI graph (cytoscape.js JSON) from pipeline outputs.

Topology comes from the STRING network CSVs (results/networks/string_ppi_*.csv);
per-gene attributes are joined from the DESeq2 table and the normalized-expression
matrix. Rebuilding from the CSVs (rather than parsing string_ppi.cyjs, which lacks
the new attributes) keeps the graph consistent with the exporter by construction.

Pure / Qt-free so it is unit-testable. Missing values are emitted as JSON null
(never NaN, which is invalid JSON and would break the viewer).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

NODES_CSV = "results/networks/string_ppi_nodes.csv"
EDGES_CSV = "results/networks/string_ppi_edges.csv"
DESEQ_CSV = "results/deseq2/deseq2_results.csv"
NORM_CSV = "results/export/normalized_expression_matrix.csv"


def _num(v):
    """Native Python number, or None for NaN/NA/missing (JSON-safe)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def _empty() -> dict:
    return {"elements": {"nodes": [], "edges": []},
            "meta": {"node_count": 0, "edge_count": 0, "score_floor": 0.0}}


def build_ppi_cytoscape_json(project_root: str | Path) -> dict:
    """Return {elements:{nodes,edges}, meta:{...}} for the cytoscape.js viewer."""
    root = Path(project_root)
    nodes_path = root / NODES_CSV
    if not nodes_path.exists():
        return _empty()
    try:
        nodes_df = pd.read_csv(nodes_path)
    except Exception:
        return _empty()
    if nodes_df.empty or "id" not in nodes_df.columns:
        return _empty()

    # DE attributes keyed by UPPER(symbol); dedup many-to-one by max baseMean.
    de_by_sym: dict[str, dict] = {}
    sym_to_gene: dict[str, str] = {}
    deseq_path = root / DESEQ_CSV
    if deseq_path.exists():
        try:
            de = pd.read_csv(deseq_path)
        except Exception:
            de = pd.DataFrame()
        if not de.empty and "symbol" in de.columns:
            de = de[de["symbol"].notna()].copy()
            de["__sym"] = de["symbol"].astype(str).str.upper()
            if "baseMean" in de.columns:
                # na_position='first' so a NaN baseMean never wins keep='last' (the max).
                de = de.sort_values("baseMean", na_position="first")
            de = de.drop_duplicates("__sym", keep="last")
            for _, r in de.iterrows():
                de_by_sym[r["__sym"]] = {
                    "log2FoldChange": _num(r.get("log2FoldChange")),
                    "padj": _num(r.get("padj")),
                    "baseMean": _num(r.get("baseMean")),
                }
                if "gene_id" in de.columns and pd.notna(r.get("gene_id")):
                    sym_to_gene[r["__sym"]] = str(r["gene_id"])

    # Mean VST expression per gene_id (row mean across sample columns).
    mean_by_gene: dict[str, float] = {}
    norm_path = root / NORM_CSV
    if norm_path.exists():
        try:
            mat = pd.read_csv(norm_path)
        except Exception:
            mat = pd.DataFrame()
        if not mat.empty:
            gcol = "gene_id" if "gene_id" in mat.columns else mat.columns[0]
            sample_cols = [c for c in mat.columns if c != gcol]
            if sample_cols:
                num = mat[sample_cols].apply(pd.to_numeric, errors="coerce")
                means = num.mean(axis=1, skipna=True)
                for gid, m in zip(mat[gcol].astype(str), means):
                    mean_by_gene[gid] = _num(m)

    has_node_lfc = "log2FC" in nodes_df.columns
    out_nodes = []
    for _, n in nodes_df.iterrows():
        sym = str(n["id"])
        usym = sym.upper()
        de = de_by_sym.get(usym, {})
        gid = sym_to_gene.get(usym)
        mean_expr = mean_by_gene.get(gid) if gid is not None else None
        # log2FC already case-joined into the node at build time; fall back to DE.
        node_lfc = _num(n.get("log2FC")) if has_node_lfc else None
        if node_lfc is None:
            node_lfc = de.get("log2FoldChange")
        out_nodes.append({"data": {
            "id": sym, "symbol": sym,
            "log2FoldChange": node_lfc,
            "padj": de.get("padj"),
            "baseMean": de.get("baseMean"),
            "meanExpr": mean_expr,
            "degree": _num(n.get("degree")),
            "betweenness": _num(n.get("betweenness")),
            "module": _num(n.get("module")),
        }})

    out_edges = []
    weights = []
    edges_path = root / EDGES_CSV
    if edges_path.exists():
        try:
            edges_df = pd.read_csv(edges_path)
        except Exception:
            edges_df = pd.DataFrame()
        for _, e in edges_df.iterrows():
            if pd.isna(e.get("source")) or pd.isna(e.get("target")):
                continue
            w = _num(e.get("weight"))
            out_edges.append({"data": {
                "source": str(e["source"]), "target": str(e["target"]), "weight": w}})
            if isinstance(w, (int, float)):
                weights.append(w)
    # The confidence slider floors at the true minimum edge weight (the build
    # threshold); never use a 1.0 sentinel, which would hide every edge.
    floor = min(weights) if weights else 0.0

    return {"elements": {"nodes": out_nodes, "edges": out_edges},
            "meta": {"node_count": len(out_nodes), "edge_count": len(out_edges),
                     "score_floor": round(float(floor), 3)}}
