from __future__ import annotations

from app.core.geo_metadata import _ascii_slug, _infer_condition
from app.core.sra_metadata import _suggest_conditions_from_titles


def _rows(**cols):
    """Build GEO-style sample row dicts from column -> list-of-values."""
    n = len(next(iter(cols.values())))
    base = {
        "sample_id": "", "gsm_accession": "", "condition": "unknown", "layout": "n/a",
        "fastq_1": "", "platform": "", "organism": "", "title": "", "source_name": "",
    }
    rows = []
    for i in range(n):
        row = dict(base)
        for key, vals in cols.items():
            row[key] = vals[i]
        rows.append(row)
    return rows


# --- GEO condition inference -------------------------------------------------

def test_prefers_genotype_and_is_ascii_even_with_delta():
    rows = _rows(
        genotype=["wt strain", "wt strain", "Δko strain", "Δko strain"],
        title=["WildType Rep1", "WildType Rep2", "Δko Rep1", "Δko Rep2"],
    )
    conds, source = _infer_condition(rows, 4)
    assert source == "genotype"
    assert len(set(conds)) == 2
    assert all(c.isascii() for c in conds)  # the Greek delta must not survive into the label


def test_treatment_beats_cell_line_covariate():
    rows = _rows(
        treatment=["untreated", "dexamethasone", "untreated", "dexamethasone"],
        cell_line=["N1", "N1", "N2", "N2"],
    )
    conds, source = _infer_condition(rows, 4)
    assert source == "treatment"
    assert set(conds) == {"untreated", "dexamethasone"}


def test_all_covariates_fall_back_to_title():
    rows = _rows(
        gender=["M", "F", "M", "F"], batch=["1", "1", "2", "2"],
        title=["ctrl_1", "ctrl_2", "treated_1", "treated_2"],
    )
    conds, source = _infer_condition(rows, 4)
    assert source == "title"
    assert set(conds) == {"ctrl", "treated"}


def test_unique_per_sample_stays_unknown():
    rows = _rows(barcode=["aa", "bb", "cc", "dd"], title=["one", "two", "three", "four"])
    conds, source = _infer_condition(rows, 4)
    assert source == "unknown"
    assert all(c == "unknown" for c in conds)


def test_blank_characteristic_cell_is_unknown():
    rows = _rows(cell_type=["lp", "ml", "basal", ""])
    conds, source = _infer_condition(rows, 4)
    assert source == "cell_type"
    assert conds[3] == "unknown"
    assert len({c for c in conds if c != "unknown"}) == 3


# --- SRA title-based suggestion (conservative) -------------------------------

def test_sra_groups_replicated_design():
    m = _suggest_conditions_from_titles(
        ["X_spores_R1", "X_spores_R2", "X_mycelium_R1", "X_mycelium_R2"])
    assert m is not None
    assert set(m.values()) == {"spores", "mycelium"}


def test_sra_keeps_unknown_for_per_individual_titles():
    assert _suggest_conditions_from_titles(
        ["NA19209_yale", "NA19210_yale", "NA19211_yale", "NA19222_yale"]) is None


# --- ASCII slug --------------------------------------------------------------

def test_ascii_slug_strips_nonascii():
    assert _ascii_slug("Δtor1") == "tor1"
    assert "Δ" not in _ascii_slug("WildType Δ")
    assert _ascii_slug("Dexamethasone") == "dexamethasone"
