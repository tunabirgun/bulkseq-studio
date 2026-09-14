"""Live resolution of every catalogue KEGG organism code against the REST endpoints.

app/data/reference_catalog.yaml declares a kegg_key_form per organism, and
workflow/scripts/run_enrichment.R trusts that declaration instead of probing: a wrong
value silently bridges (or fails to bridge) every gene id and returns empty KEGG
tables. Only the live service can falsify it, so this test resolves each code on the
two endpoints clusterProfiler::download_KEGG depends on and re-derives the key form
from link/<org>/pathway.

Opt-in because it issues ~90 requests: set BULKSEQ_KEGG_LIVE=1. Without the flag, or
without the network, it skips with the reason spelled out -- it never passes silently.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "app" / "data" / "reference_catalog.yaml"
BASE = "https://rest.kegg.jp"
RUN_FLAG = "BULKSEQ_KEGG_LIVE"
REQUEST_PAUSE_S = 0.34


def catalog_key_forms() -> dict[str, str]:
    entries = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["references"]
    return {
        str(entry["kegg_organism"]): str(entry.get("kegg_key_form") or "")
        for entry in entries
        if entry.get("kegg_organism")
    }


def fetch(path: str) -> str:
    url = f"{BASE}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=90) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"KEGG endpoint {path} failed: {exc}") from exc
    if not body.strip():
        raise RuntimeError(f"KEGG endpoint {path} returned an empty body")
    return body


def classify(body: str, code: str) -> tuple[str, int, int]:
    """Majority rule over every gene key, mirroring run_enrichment.R's classify_kegg_keys.

    link/<org>/pathway pairs a pathway entry with a gene entry and the column order is
    the service's business, so the gene key is taken as the field carrying the organism
    prefix rather than by position.
    """
    keys = [field.split(":", 1)[1]
            for line in body.splitlines()
            for field in line.split("\t")
            if field.startswith(f"{code}:")]
    numeric = sum(1 for key in keys if key.isascii() and key.isdigit())
    if not keys:
        return "unknown", 0, 0
    return ("geneid" if numeric * 2 > len(keys) else "locus_tag"), numeric, len(keys)


def disagreements(declared: dict[str, str], observed: dict[str, str]) -> dict[str, str]:
    return {
        code: f"catalogue={declared[code]} live={observed[code]}"
        for code in sorted(declared)
        if code in observed and declared[code] != observed[code]
    }


def _require_live() -> None:
    codes = catalog_key_forms()
    if not os.environ.get(RUN_FLAG):
        pytest.skip(
            f"live KEGG resolution is opt-in: set {RUN_FLAG}=1 to resolve "
            f"{len(codes)} catalogue organism codes against {BASE}")
    try:
        fetch("info/hsa")
    except RuntimeError as exc:
        pytest.skip(f"{RUN_FLAG} is set but {BASE} is unreachable, so the catalogue "
                    f"kegg_key_form values are UNVERIFIED here: {exc}")


def test_every_catalog_kegg_code_resolves_and_matches_its_declared_key_form() -> None:
    _require_live()
    declared = catalog_key_forms()
    assert declared, "no catalog entry declares a kegg_organism"
    undeclared = sorted(code for code, form in declared.items() if not form)
    assert not undeclared, f"catalog entries without a kegg_key_form: {undeclared}"

    observed: dict[str, str] = {}
    measurements: list[str] = []
    for code in sorted(declared):
        link = fetch(f"link/{code}/pathway")
        time.sleep(REQUEST_PAUSE_S)
        pathways = fetch(f"list/pathway/{code}")
        time.sleep(REQUEST_PAUSE_S)
        assert pathways.splitlines(), f"list/pathway/{code} listed no pathways"
        form, numeric, total = classify(link, code)
        assert total, f"link/{code}/pathway carried no {code}: gene keys"
        observed[code] = form
        measurements.append(f"{code}\t{form}\t{numeric}/{total}")
    print("\n".join(measurements))

    mismatched = disagreements(declared, observed)
    assert not mismatched, f"catalogue kegg_key_form disagrees with KEGG: {mismatched}"

    # Negative control against the same live measurement: a catalogue copy with one
    # flipped value must be reported, or the comparison above proves nothing.
    flipped = dict(declared)
    victim = sorted(flipped)[0]
    flipped[victim] = "locus_tag" if flipped[victim] == "geneid" else "geneid"
    assert victim in disagreements(flipped, observed)


def test_an_unknown_organism_code_is_reported_by_code_and_endpoint() -> None:
    _require_live()
    with pytest.raises(RuntimeError) as raised:
        fetch("link/zzz/pathway")
    assert "zzz" in str(raised.value) and "link/zzz/pathway" in str(raised.value)


def test_a_wrong_declared_key_form_is_reported() -> None:
    observed = {"hsa": "geneid", "fgr": "locus_tag"}
    assert disagreements({"hsa": "geneid", "fgr": "locus_tag"}, observed) == {}
    assert disagreements({"hsa": "locus_tag", "fgr": "locus_tag"}, observed) == {
        "hsa": "catalogue=locus_tag live=geneid"}


def test_classification_is_a_majority_not_an_all_rule() -> None:
    # osa carries 2 non-numeric keys among 32,578 (measured 2026-09-13); an all() rule
    # over a sampled head would classify the whole organism as locus_tag.
    body = "\n".join(["path:osa00010\tosa:LOC_Os01g01010"]
                     + [f"path:osa00010\tosa:{n}" for n in range(100)])
    assert classify(body, "osa") == ("geneid", 100, 101)
    assert classify("path:fgr00010\tfgr:FGSG_00001", "fgr") == ("locus_tag", 0, 1)
    assert classify("", "osa") == ("unknown", 0, 0)
    # The pathway column is never mistaken for a gene key, whichever side it is on.
    assert classify("osa:4326813\tpath:osa00010", "osa") == ("geneid", 1, 1)
    assert classify("path:osa00010\tpath:osa00020", "osa") == ("unknown", 0, 0)
