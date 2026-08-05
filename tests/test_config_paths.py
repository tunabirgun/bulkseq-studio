"""Path-valued config settings that gate a rule must fail the setup check, not the job graph.

A rule that is defined only when a config setting names a file, and then uses that value as a
rule input, makes an unreachable path fatal at DAG construction: Snakemake resolves inputs
before running anything, so the run dies with a MissingInputException naming the rule and the
filename and nothing else. That is reachable by copying a config.yaml between projects. These
tests pin the behaviour that replaces it, and re-derive the covered-setting list from the rule
files so a newly gated input cannot be added without either covering it or failing here.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_VALIDATE = REPO / "workflow" / "scripts" / "validate_project.py"
_RULES_DIR = REPO / "workflow" / "rules"


@pytest.fixture(scope="module")
def vp():
    spec = importlib.util.spec_from_file_location("validate_project", _VALIDATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _config(**gene_sets) -> dict:
    return {"project": {}, "input": {}, "reference": {}, "workflow": {}, "resources": {},
            "gene_sets": gene_sets}


def test_missing_gene_list_fails_and_names_the_setting(vp, tmp_path):
    msgs = vp.check_gating_paths(
        _config(custom_gene_list=str(tmp_path / "genes_of_interest.txt")))
    assert len(msgs) == 1, msgs
    only = msgs[0]
    assert only["status"] == "FAIL"
    # The message has to carry the setting name, the path, and the way out; a bare
    # "file not found" is what the raw Snakemake exception already gave.
    assert "gene_sets.custom_gene_list" in only["message"]
    assert "genes_of_interest.txt" in only["message"]
    assert "clear it" in only["message"]


def test_existing_gene_list_passes(vp, tmp_path):
    p = tmp_path / "goi.txt"
    p.write_text("ACT1\n", encoding="utf-8")
    assert vp.check_gating_paths(_config(custom_gene_list=str(p))) == []


def test_unset_and_blank_settings_are_not_errors(vp):
    assert vp.check_gating_paths(_config()) == []
    assert vp.check_gating_paths(_config(custom_gene_list="")) == []
    assert vp.check_gating_paths(_config(custom_gene_list=None)) == []
    assert vp.check_gating_paths({}) == []


def test_relative_paths_resolve_against_the_project_root(vp, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "goi.txt").write_text("ACT1\n", encoding="utf-8")
    cfg = _config(custom_gene_list="config/goi.txt")
    assert vp.check_gating_paths(cfg, base=tmp_path) == []
    assert len(vp.check_gating_paths(_config(custom_gene_list="config/absent.txt"),
                                     base=tmp_path)) == 1


def test_every_gating_setting_is_reported_independently(vp, tmp_path):
    cfg = {"gene_sets": {"custom_gene_list": str(tmp_path / "a.txt"),
                         "custom_gene_sets": str(tmp_path / "b.gmt"),
                         "functional_annotation_table": str(tmp_path / "c.tsv"),
                         "background_gene_list": str(tmp_path / "d.txt")},
           "input": {"count_matrix": str(tmp_path / "e.tsv"),
                     "deseq2_results": str(tmp_path / "f.csv")},
           "microarray": {"source": "local_matrix",
                          "expression_matrix": str(tmp_path / "g.tsv")}}
    msgs = vp.check_gating_paths(cfg)
    assert len(msgs) == 7, [m["message"] for m in msgs]
    assert all(m["status"] == "FAIL" for m in msgs)


def test_microarray_matrix_is_inert_off_the_local_matrix_source(vp, tmp_path):
    """A stale local-matrix path must not block a GEO-series run, where no rule reads it."""
    cfg = {"microarray": {"source": "geo_series_matrix",
                          "expression_matrix": str(tmp_path / "absent.tsv")}}
    assert vp.check_gating_paths(cfg) == []
    cfg["microarray"]["source"] = "local_matrix"
    assert len(vp.check_gating_paths(cfg)) == 1


def test_setup_check_surfaces_the_failure(vp, tmp_path, monkeypatch):
    """The message must reach checks/00_project_setup.json, which is the panel users see."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "project: {}\ninput: {samples: samples.tsv}\nreference: {}\nworkflow: {}\n"
        "resources: {}\ngene_sets: {custom_gene_list: /nope/genes.txt}\n", encoding="utf-8")
    samples = tmp_path / "samples.tsv"
    samples.write_text("sample\tcondition\ns1\tA\n", encoding="utf-8")
    out = tmp_path / "00_project_setup.json"
    monkeypatch.setattr(vp, "check_r_packages", lambda config=None: [])
    monkeypatch.setattr(
        "sys.argv", ["validate_project.py", "--config", str(cfg),
                     "--samples", str(samples), "--out", str(out)])
    rc = vp.main()
    assert rc == 1
    import json
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert any("gene_sets.custom_gene_list" in m["message"] for m in payload["messages"])


def _gated_config_inputs() -> set[tuple[str, str]]:
    """Re-derive, from the rule files, which config settings reach a rule's `input:` block.

    Finds module-level `_VAR = config.get("section", {}).get("key")` assignments, then keeps
    those whose variable is referenced inside an `input:` block. Deliberately narrow: it only
    recognises the two-level `.get().get()` shape the rule files actually use, so a new gating
    style would show up as a test that needs updating rather than as a silent pass.
    """
    assign = re.compile(
        r'^(_[A-Z_]+)\s*=\s*config\.get\(\s*["\'](\w+)["\']\s*,\s*\{\}\s*\)'
        r'\.get\(\s*["\'](\w+)["\']', re.M)
    found: set[tuple[str, str]] = set()
    for path in sorted(_RULES_DIR.glob("*.smk")):
        text = path.read_text(encoding="utf-8")
        varmap = {m.group(1): (m.group(2), m.group(3))
                  for m in assign.finditer(text)}
        if not varmap:
            continue
        for block in re.finditer(r"^\s*input:\n((?:\s{8,}.*\n)+)", text, re.M):
            for var, key in varmap.items():
                if re.search(rf"\b{var}\b", block.group(1)):
                    found.add(key)
    return found


def test_gating_table_covers_every_config_derived_rule_input(vp):
    derived = _gated_config_inputs()
    # A scan that silently matches nothing would make this test pass no matter what the rule
    # files contain, so require it to have found the settings that are known to be gated.
    assert ("gene_sets", "custom_gene_list") in derived, (
        "the rule-file scan found nothing where it must find gene_sets.custom_gene_list "
        f"(figures.smk); it derived {sorted(derived)}")
    assert len(derived) >= 4, sorted(derived)
    covered = {(s, k) for s, k, _ in vp.GATING_PATHS}
    missing = derived - covered
    assert not missing, (
        f"rule files gate an input on {sorted(missing)}, which validate_project.GATING_PATHS "
        f"does not cover; add it there so the failure names the setting")


def test_gating_table_entries_are_well_formed(vp):
    for section, key, effect in vp.GATING_PATHS:
        assert section and key and effect
        assert not effect.endswith("."), f"{section}.{key}: effect is inlined mid-sentence"
