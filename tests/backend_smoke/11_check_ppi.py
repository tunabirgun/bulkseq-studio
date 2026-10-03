from __future__ import annotations

import csv
import gzip
import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pandas as pd


project = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects/ppi"
network = project / "results/networks"
nodes = pd.read_csv(network / "string_ppi_nodes.csv")
edges = pd.read_csv(network / "string_ppi_edges.csv")
hubs = pd.read_csv(network / "ppi_hub_genes.csv")
expected = {"CDC28", "CLB2", "SIC1"}
node_ids = set(nodes["id"])
cache = network / "string_cache"
with gzip.open(cache / "4932.protein.info.v12.0.txt.gz", "rt", encoding="utf-8") as handle:
    matches = {}
    for row in csv.DictReader(handle, delimiter="\t"):
        if row["preferred_name"] in expected:
            matches.setdefault(row["preferred_name"], set()).add(row["#string_protein_id"])
if set(matches) != expected or any(len(ids) != 1 for ids in matches.values()):
    raise SystemExit("Official yeast protein names do not map uniquely to the three public seeds")
protein_to_seed = {next(iter(ids)): seed for seed, ids in matches.items()}
expected_edges = {}
with gzip.open(cache / "4932.protein.links.v12.0.txt.gz", "rt", encoding="ascii") as handle:
    if handle.readline().split() != ["protein1", "protein2", "combined_score"]:
        raise SystemExit("Official STRING link table schema changed")
    for line in handle:
        protein1, protein2, score_text = line.split()
        if protein1 not in protein_to_seed or protein2 not in protein_to_seed:
            continue
        score = int(score_text)
        if score >= 400 and protein1 != protein2:
            pair = frozenset((protein_to_seed[protein1], protein_to_seed[protein2]))
            expected_edges[pair] = max(expected_edges.get(pair, 0), score / 1000)
if not expected_edges:
    raise SystemExit("The approved yeast seeds have no qualifying official STRING link")


def check_edges(frame: pd.DataFrame) -> dict[frozenset[str], float]:
    observed = {}
    for row in frame.itertuples():
        pair = frozenset((row.source, row.target))
        weight = float(row.weight)
        if len(pair) != 2 or not pair <= expected or not math.isfinite(weight) or not 0.4 <= weight <= 1:
            raise ValueError("Sparse STRING edge identity or score is invalid")
        if pair in observed:
            raise ValueError("Sparse STRING exported duplicate edges")
        observed[pair] = weight
    if set(observed) != set(expected_edges) or any(
            not math.isclose(observed[pair], value, abs_tol=1e-9)
            for pair, value in expected_edges.items()):
        raise ValueError("Sparse STRING edges disagree with official cached links")
    return observed


observed = check_edges(edges)
if node_ids != expected or set(hubs["symbol"]) != node_ids:
    raise SystemExit("Sparse STRING node or hub exports disagree with public seeds")
if sys.argv[1:] == ["--negative-score"]:
    altered = edges.copy()
    altered.loc[0, "weight"] = 0.1
    check_edges(altered)
elif sys.argv[1:] == ["--negative-edge"]:
    altered = edges.iloc[1:].copy()
    check_edges(altered)
elif sys.argv[1:]:
    raise SystemExit("Unknown PPI checker argument")
provenance = json.loads((network / "string_ppi_provenance.json").read_text(encoding="utf-8"))
check = json.loads((project / "checks/16_ppi_network.json").read_text(encoding="utf-8"))
realized = provenance["realized"]
threshold = provenance["configuration"]["score_threshold_combined"] / 1000
if (threshold != 0.4 or
        realized["mapped_seed_count"] != 3 or realized["node_count"] != 3 or
        realized["edge_count"] != len(edges) or check["status"] != "PASS"):
    raise SystemExit("Sparse STRING score, provenance or check failed")
cyjs = json.loads((network / "string_ppi.cyjs").read_text(encoding="utf-8"))["elements"]
cy_nodes = {item["data"]["id"] for item in cyjs["nodes"]}
cy_edges = {frozenset((item["data"]["source"], item["data"]["target"])):
            float(item["data"]["weight"]) for item in cyjs["edges"]}
graph = ET.parse(network / "string_ppi.graphml").getroot()
ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
keys = {item.get("id"): item.get("attr.name") for item in graph.findall("g:key", ns)}
graph_nodes = {}
for item in graph.findall(".//g:node", ns):
    data = {keys.get(d.get("key")): d.text for d in item.findall("g:data", ns)}
    graph_nodes[item.get("id")] = data.get("name")
graph_edges = {}
for item in graph.findall(".//g:edge", ns):
    pair = frozenset((graph_nodes[item.get("source")], graph_nodes[item.get("target")]))
    data = {keys.get(d.get("key")): d.text for d in item.findall("g:data", ns)}
    graph_edges[pair] = float(data["weight"])
sif = set()
for line in (network / "string_ppi.sif").read_text(encoding="utf-8").splitlines():
    source, relation, target = line.split("\t")
    if relation != "interacts":
        raise SystemExit("Sparse STRING SIF relation changed")
    sif.add(frozenset((source, target)))
if (cy_nodes != node_ids or set(graph_nodes.values()) != node_ids or
        sif != set(observed) or set(cy_edges) != set(observed) or
        set(graph_edges) != set(observed) or any(
            not math.isclose(other[pair], weight, abs_tol=1e-9)
            for other in (cy_edges, graph_edges) for pair, weight in observed.items())):
    raise SystemExit("Sparse STRING graph identities or weights disagree across exports")
figure = project / "results/figures/ppi_network"
if not figure.with_suffix(".png").is_file() or not figure.with_suffix(".svg").is_file():
    raise SystemExit("Sparse STRING image pair is missing")
print(f"PASS: {len(nodes)} public yeast seeds, {len(edges)} unique edges, "
      f"score range {min(observed.values()):.3f}-{max(observed.values()):.3f}, check PASS")
