"""Prepare small, deterministic learning states from the synthetic model fits."""
import json
import re
from pathlib import Path

import numpy as np
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.stats import false_discovery_control, hypergeom

root = Path(__file__).resolve().parents[1]
model = json.loads((root / "model-examples.json").read_text(encoding="utf-8"))
sample_parts = [re.fullmatch(r"([A-Za-z]+)(\d+)", sample) for sample in model["samples"]]
assert all(sample_parts), "Synthetic sample labels must encode condition and donor."
condition_names = [sample[1] for sample in sample_parts]
levels = sorted(set(condition_names))
assert len(levels) == 2
conditions = np.array([int(name == levels[1]) for name in condition_names])
donors = np.array([int(sample[2]) for sample in sample_parts])
donor_levels = sorted(set(donors))
n_samples = len(model["samples"])
required_samples = min(condition_names.count(level) for level in levels)
expression = np.asarray(model["expression"], dtype=float)
gene_ids = model["expression_genes"]
by_id = {row["id"]: row for row in model["models"]["deseq2"]}
ranked = sorted((gene for gene in gene_ids if by_id[gene]["padj"] is not None), key=lambda gene: by_id[gene]["padj"])
heat_genes = ranked[:12]
heat_matrix = expression[[gene_ids.index(gene) for gene in heat_genes]]
z_scores = (heat_matrix - heat_matrix.mean(axis=1, keepdims=True)) / heat_matrix.std(axis=1, ddof=1, keepdims=True)
assert np.isfinite(z_scores).all()
heat_states = {}
for count in (4, 8, 12):
    for cap in (.5, 1, 1.5, 2.5):
        clipped = np.clip(z_scores[:count], -cap, cap)
        rows = linkage(clipped, method="ward", metric="euclidean")
        columns = linkage(clipped.T, method="ward", metric="euclidean")
        heat_states[f"{count}:{cap:g}"] = {
            "row_order": leaves_list(rows).tolist(), "column_order": leaves_list(columns).tolist(),
            "row_tree": rows.tolist(), "column_tree": columns.tolist(),
            "clipped_cells": int(np.count_nonzero(np.abs(z_scores[:count]) > cap)),
        }
heatmap = {"genes": heat_genes, "samples": model["samples"], "values": heat_matrix.tolist(),
           "padj": [by_id[gene]["padj"] for gene in heat_genes], "z": z_scores.tolist(), "states": heat_states}

variance = expression.var(axis=1, ddof=1)
variable_order = np.argsort(-variance, kind="stable")
pca = {}
for count in (20, 100, min(500, len(gene_ids))):
    indices = variable_order[:count]
    matrix = expression[indices].T
    centered = matrix - matrix.mean(axis=0)
    u, singular, vt = np.linalg.svd(centered, full_matrices=False)
    scores = u[:, :2] * singular[:2]
    for axis in range(2):
        if vt[axis, np.argmax(np.abs(vt[axis]))] < 0:
            scores[:, axis] *= -1
    pca[str(count)] = {"count": count, "genes": [gene_ids[i] for i in indices],
                       "scores": scores.tolist(), "percent": (100 * singular[:2]**2 / np.sum(singular**2)).tolist()}

counts = np.asarray(model["counts"], dtype=int)
low_indices = np.argsort(counts.mean(axis=1), kind="stable")[:12]
filtering = {"genes": model["genes"], "counts": counts.tolist(), "shown_indices": low_indices.tolist(),
             "samples": model["samples"], "required_samples": required_samples}

designs = {}
for structure in ("balanced", "confounded", "paired"):
    for adjusted in (False, True):
        columns = [np.ones(n_samples)]
        labels = ["Intercept"]
        formula = "~ condition"
        if adjusted:
            if structure == "paired":
                columns.extend((donors == donor).astype(float) for donor in donor_levels[1:])
                labels.extend(f"Donor {donor}" for donor in donor_levels[1:])
                formula = "~ donor + condition"
            else:
                batch = np.array([donor_levels.index(donor) % 2 for donor in donors]) if structure == "balanced" else conditions.copy()
                columns.append(batch)
                labels.append("Batch 2")
                formula = "~ batch + condition"
        columns.append(conditions)
        labels.append(f"{levels[1]} vs {levels[0]}")
        matrix = np.column_stack(columns)
        rank = int(np.linalg.matrix_rank(matrix))
        designs[f"{structure}:{int(adjusted)}"] = {"matrix": matrix.tolist(), "labels": labels,
            "formula": formula, "rank": rank, "parameters": len(columns), "residual_df": n_samples-rank,
            "estimable": rank == len(columns), "omitted_structure": not adjusted and structure != "balanced"}

foreground = {0, 1, 2, 3, 4, 10, 15, 20, 40, 60}
terms = {"Set A": set(range(20)), "Set B": set(range(10, 40)), "Set C": set(range(40, 55))}
enrichment = {}
for universe_size in (100, 1000):
    records = [{"name": name, "overlap": len(foreground & members), "term_size": len(members),
                "foreground": len(foreground), "universe": universe_size,
                "pvalue": float(hypergeom.sf(len(foreground & members)-1, universe_size, len(members), len(foreground)))}
               for name, members in terms.items()]
    adjusted = false_discovery_control([row["pvalue"] for row in records], method="bh")
    for row, padj in zip(records, adjusted):
        row["padj"] = float(padj)
    enrichment[str(universe_size)] = records

protein_nodes = [{"id": f"P{i+1:02d}", "log2fc": (-1 if i % 3 == 0 else 1) * (.6 + (i % 5)*.35)} for i in range(12)]
protein_edges = [[0,1,.95],[1,2,.85],[2,3,.7],[3,0,.55],[0,2,.9],
                 [4,5,.92],[5,6,.8],[6,7,.65],[7,4,.4],[4,6,.75],
                 [8,9,.97],[9,10,.9],[10,11,.7],[11,8,.5],[8,10,.85],
                 [2,4,.55],[6,8,.45],[1,10,.4],[3,11,.6]]
network = {"nodes": protein_nodes, "edges": [{"source": protein_nodes[a]["id"], "target": protein_nodes[b]["id"], "score": score} for a,b,score in protein_edges]}
engine_genes = ["G001", "G010", "G030", "G061", "G080", "G110", "G160", "G400"]
engines = {name: {"tested": len(rows), "genes": [next((row for row in rows if row["id"] == gene), {"id": gene, "log2fc": None, "padj": None}) for gene in engine_genes]}
           for name, rows in model["models"].items()}
shrunken = {row["id"]: row["log2fc"] for row in model["shrunk"]}
engines["shrinkage"] = [{"id": gene, "log2fc": shrunken.get(gene)} for gene in engine_genes]
data = {"samples": model["samples"], "transformation": model["provenance"]["transformation"],
        "design": designs, "filtering": filtering, "normalization": model["normalization"],
        "pca": pca, "engines": engines, "heatmap": heatmap, "enrichment": enrichment, "network": network}
(root / "learning-examples.json").write_text(json.dumps(data, allow_nan=False, separators=(",", ":")), encoding="utf-8")
print(f"Prepared {len(designs)} design, {len(pca)} PCA and {len(heat_states)} heatmap states.")
