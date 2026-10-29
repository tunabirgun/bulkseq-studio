"""Cache a small, versioned STRING functional network for the website."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "network-example.json"
BASE = "https://version-12-5.string-db.org/api/json"
SYMBOLS = ("TP53", "MDM2", "ATM", "CHEK2", "BRCA1", "BRCA2", "RAD51", "CDKN1A", "BAX", "GADD45A", "H2AX", "PPM1D")
SPECIES = 9606
MINIMUM_SCORE = 400


def get(method: str, params: dict[str, str | int]) -> list[dict]:
    url = f"{BASE}/{method}?{urlencode(params)}"
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def components(nodes: list[dict], edges: list[dict]) -> int:
    neighbours = {node["id"]: set() for node in nodes}
    for edge in edges:
        neighbours[edge["source"]].add(edge["target"])
        neighbours[edge["target"]].add(edge["source"])
    seen, count = set(), 0
    for node in neighbours:
        if node in seen:
            continue
        count += 1
        stack = [node]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(neighbours[current] - seen)
    return count


def validate(snapshot: dict) -> None:
    provenance = snapshot["provenance"]
    nodes, edges = snapshot["nodes"], snapshot["edges"]
    assert provenance["database"] == "STRING"
    assert provenance["version"] == "12.5"
    assert provenance["species"] == SPECIES
    assert provenance["minimumScore"] == MINIMUM_SCORE
    assert provenance["networkType"] == "functional"
    assert provenance["querySymbols"] == list(SYMBOLS)
    mappings = provenance["mappings"]
    assert [mapping["querySymbol"] for mapping in mappings] == list(SYMBOLS)
    assert len({mapping["stringId"] for mapping in mappings}) == len(SYMBOLS)
    assert len(nodes) == len(SYMBOLS) == len({node["id"] for node in nodes})
    assert {(node["label"], node["id"]) for node in nodes} == {(mapping["querySymbol"], mapping["stringId"]) for mapping in mappings}
    node_ids = {node["id"] for node in nodes}
    assert edges and all(edge["source"] in node_ids and edge["target"] in node_ids for edge in edges)
    assert all(MINIMUM_SCORE <= edge["score"] <= 1000 for edge in edges)
    assert all(edge["source"] < edge["target"] for edge in edges)
    assert len({(edge["source"], edge["target"]) for edge in edges}) == len(edges)
    counts = {str(cutoff): sum(edge["score"] >= cutoff for edge in edges) for cutoff in (400, 700, 900)}
    assert provenance["edgeCounts"] == counts
    assert counts["400"] >= counts["700"] >= counts["900"] > 0
    assert provenance["componentCounts"] == {str(cutoff): components(nodes, [edge for edge in edges if edge["score"] >= cutoff]) for cutoff in (400, 700, 900)}


def build() -> dict:
    common = {"species": SPECIES, "caller_identity": "BulkSeqStudio"}
    mappings = get("get_string_ids", {**common, "identifiers": "\r".join(SYMBOLS), "echo_query": 1})
    sleep(1)
    by_query = {row["queryItem"]: row for row in mappings}
    if set(by_query) != set(SYMBOLS):
        missing = sorted(set(SYMBOLS) - set(by_query))
        raise ValueError(f"STRING did not map every requested public teaching symbol: {missing}")
    if any(by_query[symbol]["preferredName"] != symbol for symbol in SYMBOLS):
        raise ValueError("STRING mapping changed a requested teaching symbol")
    nodes = [{"id": by_query[symbol]["stringId"], "label": symbol} for symbol in SYMBOLS]
    rows = get("network", {**common, "identifiers": "\r".join(node["id"] for node in nodes), "required_score": MINIMUM_SCORE, "network_type": "functional", "add_nodes": 0})
    node_ids = {node["id"] for node in nodes}
    edges = []
    for row in rows:
        source, target = sorted((row["stringId_A"], row["stringId_B"]))
        if source not in node_ids or target not in node_ids:
            raise ValueError("STRING returned an unrequested network node")
        score = round(float(row["score"]) * 1000)
        if score < MINIMUM_SCORE:
            raise ValueError("STRING returned an edge below the requested score")
        edges.append({"source": source, "target": target, "score": score})
    edges.sort(key=lambda edge: (edge["source"], edge["target"]))
    if len({(edge["source"], edge["target"]) for edge in edges}) != len(edges):
        raise ValueError("STRING returned duplicate undirected edges")
    edge_counts = {str(cutoff): sum(edge["score"] >= cutoff for edge in edges) for cutoff in (400, 700, 900)}
    snapshot = {
        "provenance": {
            "database": "STRING",
            "version": "12.5",
            "retrieved": datetime.now(timezone.utc).date().isoformat(),
            "source": "https://version-12-5.string-db.org/api/json",
            "apiDocumentation": "https://string-db.org/help/api/",
            "license": "CC BY 4.0",
            "licenseUrl": "https://string-db.org/cgi/access?footer_active_subpage=licensing",
            "attribution": "STRING database, version 12.5; modified into a fixed teaching snapshot.",
            "querySymbols": list(SYMBOLS),
            "mappings": [{"querySymbol": symbol, "stringId": by_query[symbol]["stringId"]} for symbol in SYMBOLS],
            "species": SPECIES,
            "minimumScore": MINIMUM_SCORE,
            "networkType": "functional",
            "addedNodes": 0,
            "edgeCounts": edge_counts,
            "componentCounts": {str(cutoff): components(nodes, [edge for edge in edges if edge["score"] >= cutoff]) for cutoff in (400, 700, 900)},
        },
        "nodes": nodes,
        "edges": edges,
    }
    validate(snapshot)
    return snapshot


parser = argparse.ArgumentParser()
parser.add_argument("--validate", type=Path)
args = parser.parse_args()
if args.validate:
    validate(json.loads(args.validate.read_text(encoding="utf-8")))
else:
    OUTPUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
