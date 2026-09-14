"""Declared artefact x DE-engine parity table.

Every differential-expression route writes into the same canonical directory, but they do
not all produce the same artefacts. That divergence has been recorded only in prose and in
the Snakefile's mode flags, so an artefact added to one engine and forgotten on another (or
a check silently gated off) has no failing test behind it.

DECLARED below is the intended state; `observed_artefacts` derives the same table from the
scripts and rule blocks. A mismatch in either direction fails: adding an output to one
engine without updating the table, and claiming a cell the sources do not support.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"
RULES = ROOT / "workflow" / "rules"

# Engine -> the script that fits it, the Snakemake rule that runs it, and the Snakefile
# mode flags that are true on that route (a check gated `if not (... or FLAG ...)` is off
# for every engine carrying that flag).
ENGINES = {
    "DESeq2": ("run_deseq2.R", "deseq2", frozenset()),
    "edgeR-QLF": ("run_edger.R", "edger_de", frozenset({"EDGER_MODE", "ALT_DE_MODE"})),
    "limma-voom": ("run_voom.R", "voom_de", frozenset({"VOOM_MODE", "ALT_DE_MODE"})),
    "limma": ("run_limma.R", "limma_de", frozenset({"MICROARRAY_MODE"})),
    "imported": ("ingest_deseq2_results.R", "ingest_deseq2_results",
                 frozenset({"DE_RESULTS_MODE"})),
}

# State after the 0.31.0 engine-parity wave: the three count engines annotate ncbi_geneid, all
# four local-model engines write PCA coordinates (so check 23 screens every route that fits a
# model) and carry the |log2FC| > L companion column computed by the engine's own native test.
# The TOST equivalence output stays DESeq2-only. The imported-results route fits no model.
DECLARED: dict[str, dict[str, object]] = {
    "DESeq2": {
        "ncbi_geneid": True,
        "pca_coordinates": True,
        "check_23_covariate_structure": True,
        "padj_lfc_ge_threshold": True,
        "lfc_companion_threshold_rule": True,
        "companion_column_outside_the_up_down_split": True,
        "lfc_threshold_test": "greaterAbs",
        "unchanged_genes": True,
        "check_13_equivalence": True,
        "lfcSE_populated": True,
        "stat_column": True,
        "check_14_wilcoxon": True,
        "interaction_handling": "review_required",
        "sources_de_common_before_library": True,
    },
    "edgeR-QLF": {
        "ncbi_geneid": True,
        "pca_coordinates": True,
        "check_23_covariate_structure": True,
        "padj_lfc_ge_threshold": True,
        "lfc_companion_threshold_rule": True,
        "companion_column_outside_the_up_down_split": True,
        "lfc_threshold_test": "glmTreat",
        "unchanged_genes": False,
        "check_13_equivalence": False,
        "lfcSE_populated": False,
        "stat_column": True,
        "check_14_wilcoxon": True,
        "interaction_handling": "refuse",
        "sources_de_common_before_library": True,
    },
    "limma-voom": {
        "ncbi_geneid": True,
        "pca_coordinates": True,
        "check_23_covariate_structure": True,
        "padj_lfc_ge_threshold": True,
        "lfc_companion_threshold_rule": True,
        "companion_column_outside_the_up_down_split": True,
        "lfc_threshold_test": "treat",
        "unchanged_genes": False,
        "check_13_equivalence": False,
        "lfcSE_populated": True,
        "stat_column": True,
        "check_14_wilcoxon": True,
        "interaction_handling": "refuse",
        "sources_de_common_before_library": True,
    },
    "limma": {
        "ncbi_geneid": False,
        "pca_coordinates": True,
        "check_23_covariate_structure": True,
        "padj_lfc_ge_threshold": True,
        "lfc_companion_threshold_rule": True,
        "companion_column_outside_the_up_down_split": True,
        "lfc_threshold_test": "treat",
        "unchanged_genes": False,
        "check_13_equivalence": False,
        "lfcSE_populated": True,
        "stat_column": True,
        "check_14_wilcoxon": True,
        "interaction_handling": "refuse",
        "sources_de_common_before_library": True,
    },
    "imported": {
        "ncbi_geneid": False,
        "pca_coordinates": False,
        "check_23_covariate_structure": False,
        "padj_lfc_ge_threshold": False,
        "lfc_companion_threshold_rule": False,
        "companion_column_outside_the_up_down_split": True,
        "lfc_threshold_test": None,
        "unchanged_genes": False,
        "check_13_equivalence": False,
        "lfcSE_populated": True,
        "stat_column": True,
        "check_14_wilcoxon": False,
        "interaction_handling": "not_applicable",
        "sources_de_common_before_library": False,
    },
}


def read_sources() -> dict[str, str]:
    names = {script for script, _, _ in ENGINES.values()}
    sources = {name: (SCRIPTS / name).read_text(encoding="utf-8") for name in names}
    for smk in ("deseq2.smk", "checks.smk"):
        sources[smk] = (RULES / smk).read_text(encoding="utf-8")
    return sources


def _rule_block(smk: str, rule: str) -> str:
    match = re.search(rf"^(\s*)rule {re.escape(rule)}:\s*$", smk, re.M)
    assert match, f"rule {rule} not found"
    indent = len(match.group(1))
    body = []
    for line in smk[match.end():].splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return "\n".join(body)


def _check_gate_flags(checks_smk: str, check_file: str) -> frozenset[str]:
    """Mode flags in the `if not (...)` guard that adds `check_file` to ALL_CHECKS.

    An unguarded append returns the empty set (the check runs on every route).
    """
    lines = checks_smk.splitlines()
    marker = f'ALL_CHECKS.append("{check_file}")'
    index = next(i for i, line in enumerate(lines) if marker in line)
    indent = len(lines[index]) - len(lines[index].lstrip())
    for line in reversed(lines[:index]):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) >= indent:
            continue
        guard = line.strip()
        assert guard.startswith("if not"), (
            f"{check_file} is guarded by {guard!r}, which this reader cannot interpret as a "
            "mode exclusion")
        return frozenset(re.findall(r"\b[A-Z][A-Z0-9_]*_MODE\b", guard))
    return frozenset()


def _braced_block(text: str, start: int) -> str:
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
    raise AssertionError("unbalanced braces after the interaction detector")


def _interaction_handling(script: str) -> str:
    match = re.search(r"has_interaction\(\s*design_formula\s*\)", script)
    if not match:
        return "not_applicable"
    block = _braced_block(script, match.end())
    if "stop(" in block:
        return "refuse"
    if "REVIEW_REQUIRED" in block:
        return "review_required"
    return "unhandled"


def _sources_de_common_before_library(script: str) -> bool:
    source_at = script.find('source(file.path(snakemake@scriptdir, "de_common.R"))')
    if source_at < 0:
        return False
    library_at = re.search(r"^\s*library\(", script, re.M)
    # A script that loads nothing still satisfies the ordering requirement.
    return library_at is None or source_at < library_at.start()


LFC_THRESHOLD_RULE = "require_lfc_threshold(if (lfc_thr > 0) lfc_thr else 1.0)"


def _up_down_split(script: str) -> str:
    """The block that classifies significant genes, from the `sig` vector to the down write."""
    start = script.index("sig <- ")
    return script[start:script.index("write.csv(down", start)]


def _lfc_threshold_test(script: str) -> str | None:
    match = re.search(r'lfc_threshold_test = "([A-Za-z]+)"', script)
    return match.group(1) if match else None


def _has_stat_column(script: str) -> bool:
    # Two shapes produce the canonical `stat` column: an explicit mapping onto the DESeq2
    # schema, and DESeq2's own results object, whose data frame already carries it.
    return bool(re.search(r"(?:^|[,(])\s*stat = ", script, re.M)) or "res <- results(dds," in script


def observed_artefacts(sources: dict[str, str]) -> dict[str, dict[str, object]]:
    checks_smk = sources["checks.smk"]
    gate_23 = _check_gate_flags(checks_smk, "checks/23_covariate_structure_qc.json")
    gate_14 = _check_gate_flags(checks_smk, "checks/14_wilcoxon_sensitivity.json")

    table: dict[str, dict[str, object]] = {}
    for engine, (script_name, rule_name, mode_flags) in ENGINES.items():
        script = sources[script_name]
        rule = _rule_block(sources["deseq2.smk"], rule_name)
        # Two independent readings of the same fact: the rule declares the output and the
        # script writes it. A disagreement is the finding, not something to average over.
        rule_pca = "pca_coordinates=" in rule
        script_pca = "pca_coordinates" in script
        assert rule_pca == script_pca, (
            f"{engine}: rule {rule_name} declares pca_coordinates={rule_pca} but "
            f"{script_name} writes it={script_pca}")
        table[engine] = {
            "ncbi_geneid": "ncbi_geneid" in script,
            "pca_coordinates": rule_pca,
            "check_23_covariate_structure": not (mode_flags & gate_23),
            "padj_lfc_ge_threshold": "padj_lfc_ge_threshold" in script,
            "lfc_companion_threshold_rule": LFC_THRESHOLD_RULE in script,
            # The companion column is informational: it must never reach the up/down call.
            "companion_column_outside_the_up_down_split":
                "padj_lfc_ge_threshold" not in _up_down_split(script),
            "lfc_threshold_test": _lfc_threshold_test(script),
            "unchanged_genes": "unchanged=" in rule,
            "check_13_equivalence": "equivalence_check=" in rule,
            # DESeq2 supplies lfcSE from its own results object; the mapped engines either
            # derive it or record that the method has none.
            "lfcSE_populated": "lfcSE = NA_real_" not in script,
            "stat_column": _has_stat_column(script),
            "check_14_wilcoxon": not (mode_flags & gate_14),
            "interaction_handling": _interaction_handling(script),
            "sources_de_common_before_library": _sources_de_common_before_library(script),
        }
    return table


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    return read_sources()


def test_declared_table_covers_every_engine_and_artefact(sources) -> None:
    assert set(DECLARED) == set(ENGINES)
    artefacts = {frozenset(cells) for cells in DECLARED.values()}
    assert len(artefacts) == 1, "every engine must declare the same artefact keys"
    assert set(observed_artefacts(sources)["DESeq2"]) == next(iter(artefacts))


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_engine_artefacts_match_the_declared_table(sources, engine: str) -> None:
    assert observed_artefacts(sources)[engine] == DECLARED[engine]


def test_alt_engines_drop_the_equivalence_check_in_both_places(sources) -> None:
    # The rule output and the ALL_CHECKS removal are separate edits; a rule that keeps
    # unchanged_genes while checks.smk removes check 13 would deadlock the run.
    assert 'if ALT_DE_MODE and "checks/13_equivalence_qc.json" in ALL_CHECKS:' in sources["checks.smk"]
    for engine in ("edgeR-QLF", "limma-voom"):
        assert DECLARED[engine]["check_13_equivalence"] is False
        assert DECLARED[engine]["unchanged_genes"] is False


def test_an_artefact_added_to_one_engine_without_the_table_is_caught(sources) -> None:
    # Negative control, script side: give the microarray engine the annotation column the
    # count engines write. The declared table must reject it.
    mutated = dict(sources)
    mutated["run_limma.R"] = sources["run_limma.R"].replace(
        "res_out$biotype <- NA_character_",
        "res_out$biotype <- NA_character_\nres_out$ncbi_geneid <- NA_character_", 1)
    assert mutated["run_limma.R"] != sources["run_limma.R"]
    table = observed_artefacts(mutated)
    assert table["limma"]["ncbi_geneid"] is True
    assert table["limma"] != DECLARED["limma"]
    assert table["DESeq2"] == DECLARED["DESeq2"], "the mutation must not perturb other engines"


def test_a_dropped_result_column_is_caught(sources) -> None:
    # Negative control for a cell that is true everywhere today: an engine that stops
    # mapping the signed test statistic must not still read as carrying it.
    mutated = dict(sources)
    mutated["run_voom.R"] = sources["run_voom.R"].replace("  stat = tt$t,\n", "", 1)
    assert mutated["run_voom.R"] != sources["run_voom.R"]
    table = observed_artefacts(mutated)
    assert table["limma-voom"]["stat_column"] is False
    assert table["limma-voom"] != DECLARED["limma-voom"]


def test_an_output_added_to_one_rule_without_the_table_is_caught(sources) -> None:
    # Negative control, rule side: declare the TOST output on the voom rule.
    mutated = dict(sources)
    block = _rule_block(sources["deseq2.smk"], "voom_de")
    anchor = '            normalized="results/deseq2/normalized_counts.csv",'
    assert anchor in block
    mutated["deseq2.smk"] = sources["deseq2.smk"].replace(
        block, block.replace(anchor, anchor + '\n            unchanged="results/deseq2/unchanged_genes.csv",'), 1)
    table = observed_artefacts(mutated)
    assert table["limma-voom"]["unchanged_genes"] is True
    assert table["limma-voom"] != DECLARED["limma-voom"]


def test_a_dropped_pca_writer_is_caught(sources) -> None:
    # Negative control for ENG-2. Removing the writer from one engine leaves its rule declaring
    # an output nothing produces; the two independent readings must disagree rather than agree
    # on a missing file.
    mutated = dict(sources)
    mutated["run_voom.R"] = sources["run_voom.R"].replace(
        'write_pca_coordinates(logcpm, snakemake@output[["pca_coordinates"]])\n', "", 1)
    assert mutated["run_voom.R"] != sources["run_voom.R"]
    with pytest.raises(AssertionError, match="declares pca_coordinates"):
        observed_artefacts(mutated)


def test_dropping_the_pca_output_from_both_places_is_caught(sources) -> None:
    # The consistent-but-wrong version of the same mutation: rule and script agree, so the
    # declared table is the only thing left to catch it.
    mutated = dict(sources)
    mutated["run_edger.R"] = sources["run_edger.R"].replace(
        'write_pca_coordinates(logcpm, snakemake@output[["pca_coordinates"]])\n', "", 1)
    block = _rule_block(sources["deseq2.smk"], "edger_de")
    mutated["deseq2.smk"] = sources["deseq2.smk"].replace(
        block, block.replace(
            '            pca_coordinates="results/deseq2/pca_coordinates.csv",\n', ""), 1)
    assert mutated["run_edger.R"] != sources["run_edger.R"]
    assert mutated["deseq2.smk"] != sources["deseq2.smk"]
    table = observed_artefacts(mutated)
    assert table["edgeR-QLF"]["pca_coordinates"] is False
    assert table["edgeR-QLF"] != DECLARED["edgeR-QLF"]


def test_the_companion_column_reaching_the_up_down_split_is_caught(sources) -> None:
    # Negative control for ENG-3's central constraint: padj_lfc_ge_threshold is informational
    # and must not classify genes. Wire it into the significance vector and the table rejects it.
    mutated = dict(sources)
    mutated["run_edger.R"] = sources["run_edger.R"].replace(
        "sig <- !is.na(res_out$padj) & res_out$padj < alpha",
        "sig <- !is.na(res_out$padj_lfc_ge_threshold) & res_out$padj_lfc_ge_threshold < alpha", 1)
    assert mutated["run_edger.R"] != sources["run_edger.R"]
    table = observed_artefacts(mutated)
    assert table["edgeR-QLF"]["companion_column_outside_the_up_down_split"] is False
    assert table["edgeR-QLF"] != DECLARED["edgeR-QLF"]


def test_a_diverging_threshold_fallback_is_caught(sources) -> None:
    # The L_eq fallback rule is duplicated at four call sites; it is only safe while identical.
    mutated = dict(sources)
    mutated["run_limma.R"] = sources["run_limma.R"].replace(
        LFC_THRESHOLD_RULE, "require_lfc_threshold(if (lfc_thr > 0) lfc_thr else 0.5)", 1)
    assert mutated["run_limma.R"] != sources["run_limma.R"]
    table = observed_artefacts(mutated)
    assert table["limma"]["lfc_companion_threshold_rule"] is False
    assert table["limma"] != DECLARED["limma"]


def test_a_mislabelled_threshold_test_method_is_caught(sources) -> None:
    # The recorded method is what the run summary will name; edgeR does not run limma's treat().
    mutated = dict(sources)
    mutated["run_edger.R"] = sources["run_edger.R"].replace(
        'lfc_threshold_test = "glmTreat"', 'lfc_threshold_test = "treat"', 1)
    assert mutated["run_edger.R"] != sources["run_edger.R"]
    table = observed_artefacts(mutated)
    assert table["edgeR-QLF"]["lfc_threshold_test"] == "treat"
    assert table["edgeR-QLF"] != DECLARED["edgeR-QLF"]


# The changelog publishes the same matrix. A table that drifts from DECLARED documents an
# engine the workflow does not implement, which is worse than documenting nothing.
CHANGELOG = ROOT / "CHANGELOG.md"
CONSTANTS = ROOT / "app" / "constants.py"
CELL_VALUES: dict[str, object] = {
    "yes": True,
    "no": False,
    "review required": "review_required",
    "refuses": "refuse",
    "not applicable": "not_applicable",
    "\u2014": None,
}


def _app_version() -> str:
    match = re.search(r'^APP_VERSION = "([^"]+)"', CONSTANTS.read_text(encoding="utf-8"), re.M)
    assert match, "app/constants.py declares no APP_VERSION"
    return match.group(1)


def changelog_entry(text: str, version: str) -> str:
    heading = re.search(rf"^## {re.escape(version)}\b.*$", text, re.M)
    assert heading, f"CHANGELOG.md carries no entry for {version}"
    rest = text[heading.end():]
    following = re.search(r"^## ", rest, re.M)
    return rest[: following.start()] if following else rest


def parse_engine_matrix(entry: str) -> dict[str, dict[str, object]]:
    """The artefact x engine table published in a changelog entry, as DECLARED's shape."""
    runs: list[list[str]] = []
    for line in entry.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            if not runs or runs[-1] is None:
                runs.append([])
            runs[-1].append(stripped)
        elif runs and runs[-1]:
            runs.append(None)  # type: ignore[arg-type]
    tables = [run for run in runs if run]
    assert len(tables) == 1, f"expected exactly one table in the entry, found {len(tables)}"
    rows = tables[0]
    assert len(rows) >= 3, "the entry's table has no body"

    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip("|").split("|")]

    header = cells(rows[0])
    engines = header[1:]
    assert len(set(engines)) == len(engines), f"duplicate engine columns {engines}"
    table: dict[str, dict[str, object]] = {engine: {} for engine in engines}
    for row in rows[2:]:
        values = cells(row)
        key = re.fullmatch(r"`([A-Za-z0-9_]+)`", values[0])
        assert key, f"row label {values[0]!r} is not a backticked artefact key"
        assert len(values) == len(header), f"row {values[0]} has {len(values)} cells, header has {len(header)}"
        for engine, text in zip(engines, values[1:]):
            assert text, f"empty cell for {engine} in row {key.group(1)}"
            table[engine][key.group(1)] = CELL_VALUES.get(text, text)
    return table


def test_changelog_publishes_the_declared_engine_matrix() -> None:
    entry = changelog_entry(CHANGELOG.read_text(encoding="utf-8"), _app_version())
    table = parse_engine_matrix(entry)
    # Structure first: a parser that found nothing must not compare two empty tables.
    assert set(table) == set(ENGINES)
    for engine, cells in table.items():
        assert set(cells) == set(DECLARED[engine]), engine
    assert table == DECLARED


def test_a_mutated_changelog_cell_is_caught() -> None:
    # Negative control: publish edgeR as carrying a standard error it does not report.
    text = CHANGELOG.read_text(encoding="utf-8")
    entry = changelog_entry(text, _app_version())
    mutated = entry.replace("| `lfcSE_populated` | yes | no |", "| `lfcSE_populated` | yes | yes |", 1)
    assert mutated != entry, "negative-control fixture drifted; the lfcSE row changed shape"
    table = parse_engine_matrix(mutated)
    assert table["edgeR-QLF"]["lfcSE_populated"] is True
    assert table != DECLARED


def test_an_entry_without_the_matrix_is_caught() -> None:
    # The gate must fail loudly when the table is gone, not pass on an empty comparison.
    entry = changelog_entry(CHANGELOG.read_text(encoding="utf-8"), _app_version())
    without = "\n".join(line for line in entry.splitlines() if not line.strip().startswith("|"))
    with pytest.raises(AssertionError, match="exactly one table"):
        parse_engine_matrix(without)
