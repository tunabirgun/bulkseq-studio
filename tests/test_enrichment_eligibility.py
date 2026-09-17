from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from _runtime import rscript_runtime


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "workflow" / "scripts" / "run_enrichment.R"
CUSTOM = ROOT / "workflow" / "scripts" / "run_custom_enrichment.R"
HELPER = ROOT / "workflow" / "scripts" / "enrichment_eligibility.R"
RULES = ROOT / "workflow" / "rules" / "enrichment.smk"


def _r_runtime(script: Path) -> tuple[list[str], str, Callable[[Path], str]]:
    runtime = rscript_runtime()
    if runtime is None:
        reason = "Rscript is unavailable for the enrichment-eligibility regression"
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R") else pytest.skip)(reason)
    command, convert = runtime
    return command, convert(script), convert


def _run_r(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    command, _, convert = _r_runtime(HELPER)
    harness = tmp_path / "enrichment_eligibility.R"
    harness.write_text(code, encoding="utf-8")
    return subprocess.run(
        [*command, convert(harness)], capture_output=True, text=True,
        timeout=60, check=False,
    )


def test_missing_r_fails_when_eligibility_contract_is_mandatory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BULKSEQ_REQUIRE_R", "1")
    monkeypatch.setattr("test_enrichment_eligibility.rscript_runtime", lambda: None)
    with pytest.raises(pytest.fail.Exception, match="enrichment-eligibility regression"):
        _r_runtime(HELPER)


def test_missing_r_skips_when_eligibility_contract_is_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BULKSEQ_REQUIRE_R", raising=False)
    monkeypatch.delenv("BULKSEQ_REQUIRE_R_FULL", raising=False)
    monkeypatch.setattr("test_enrichment_eligibility.rscript_runtime", lambda: None)
    with pytest.raises(pytest.skip.Exception, match="enrichment-eligibility regression"):
        _r_runtime(HELPER)


def test_population_contract_preserves_ora_and_adds_supported_rank_rows(
    tmp_path: Path,
) -> None:
    _, main_path, convert = _r_runtime(MAIN)
    helper_path = convert(HELPER)
    code = f'''
source({helper_path!r})
exprs <- parse(file={main_path!r})
wanted <- c("select_rank_statistic", "prepare_main_enrichment_populations",
            "build_deterministic_rank", "build_population_rank")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}
res <- data.frame(
  gene_id=c("ora_up", "ora_neutral", "independent_filter", "cooks_outlier"),
  log2FoldChange=c(2, 0.5, -4, 3), stat=c(5, 2, -3, 8),
  pvalue=c(0.001, NA, 0.02, NA), padj=c(0.01, 0.2, NA, NA),
  stringsAsFactors=FALSE)
prepared <- prepare_main_enrichment_populations(
  res, !is.na(res$padj), !is.na(res$log2FoldChange))
pop <- prepared$populations
rank <- build_population_rank(pop, res$gene_id, prepared$rank_statistic)$values
stopifnot(identical(pop$ora$gene_id, c("ora_up", "ora_neutral")),
          identical(names(rank), c("ora_up", "ora_neutral", "independent_filter")),
          identical(unname(rank), c(5, 2, -3)),
          pop$additional_rank_n == 1L)

evidence_res <- data.frame(
  gene_id=c("dup", "dup", "bad"), log2FoldChange=c(1, 3, 2),
  stat=c(1, 3, Inf), pvalue=c(0.1, 0.2, 0.3), padj=c(0.2, 0.3, 0.4),
  stringsAsFactors=FALSE)
evidence_prepared <- prepare_main_enrichment_populations(
  evidence_res, !is.na(evidence_res$padj))
evidence_rank <- build_population_rank(
  evidence_prepared$populations, evidence_res$gene_id,
  evidence_prepared$rank_statistic)
stopifnot(identical(names(evidence_rank$values), "dup"),
          identical(unname(evidence_rank$values), 2),
          evidence_rank$duplicate_id_group_n == 1L,
          evidence_rank$duplicate_source_row_n == 2L,
          evidence_rank$duplicate_rows_collapsed == 1L,
          evidence_rank$nonfinite_removed == 1L)
cat("population eligibility contract OK\\n")
'''
    completed = _run_r(tmp_path, code)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "population eligibility contract OK" in completed.stdout


def test_gsea_admits_independently_filtered_rows_without_changing_ora_or_metric(
    tmp_path: Path,
) -> None:
    _, main_path, convert = _r_runtime(MAIN)
    custom_path = convert(CUSTOM)
    helper_path = convert(HELPER)
    code = f'''
source({helper_path!r})
load_functions <- function(path, wanted) {{
  for (expr in parse(file=path)) {{
    if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
        as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
  }}
}}
load_functions({main_path!r}, c(
  "select_rank_statistic", "prepare_main_enrichment_populations",
  "prepare_mapped_enrichment_populations", "build_deterministic_rank", "build_population_rank",
  "build_bridged_population_rank", "bridge_kegg_geneid",
  "collapse_entrez_results", "build_mapped_gsea_rank", "rank_evidence_lines"))
load_functions({custom_path!r}, c(
  "build_custom_deterministic_rank", "build_custom_population_rank"))

res <- data.frame(
  gene_id=c("ora_up", "ora_neutral", "independent_filter", "cooks_outlier",
            "nonfinite_rank", "missing_effect"),
  log2FoldChange=c(2, 0.5, -4, 3, 6, NA), stat=c(5, 2, -3, 8, Inf, 7),
  pvalue=c(0.001, NA, 0.02, NA, 0.03, 0.04),
  padj=c(0.01, 0.2, NA, NA, NA, NA),
  stringsAsFactors=FALSE)
prepared <- prepare_main_enrichment_populations(
  res, !is.na(res$padj), !is.na(res$log2FoldChange))
main_pop <- prepared$populations
main_rank <- build_population_rank(
  main_pop, res$gene_id, prepared$rank_statistic)$values
stopifnot(prepared$rank_statistic == "stat",
          identical(main_pop$ora$gene_id, c("ora_up", "ora_neutral")),
          identical(names(main_rank), c("ora_up", "ora_neutral", "independent_filter")),
          identical(unname(main_rank), c(5, 2, -3)),
          !"cooks_outlier" %in% names(main_rank),
          !"nonfinite_rank" %in% names(main_rank))

custom_pop <- prepare_enrichment_populations(
  res, res$log2FoldChange, !is.na(res$padj),
  !is.na(res$padj) & !is.na(res$log2FoldChange),
  !is.na(res$log2FoldChange))
custom_rank <- build_custom_population_rank(custom_pop, res$gene_id)$values
stopifnot(identical(custom_pop$ora$gene_id, c("ora_up", "ora_neutral")),
          identical(names(custom_rank),
                    c("nonfinite_rank", "ora_up", "ora_neutral", "independent_filter")),
          identical(unname(custom_rank), c(6, 2, 0.5, -4)),
          !identical(unname(custom_rank), unname(main_rank)))

# A table without a raw-p or stat field keeps the supported legacy fallback. It
# cannot supply evidence that a padj-NA row was independently filtered.
external <- data.frame(
  gene_id=c("legacy", "unsupported"), log2FoldChange=c(1.5, 9),
  padj=c(0.03, NA), stringsAsFactors=FALSE)
external_prepared <- prepare_main_enrichment_populations(
  external, !is.na(external$padj))
external_pop <- external_prepared$populations
external_rank <- build_population_rank(
  external_pop, external$gene_id, external_prepared$rank_statistic)$values
stopifnot(external_prepared$rank_statistic == "log2FoldChange",
          is.na(external_pop$raw_p_column),
          identical(names(external_rank), "legacy"),
          identical(unname(external_rank), 1.5))

# Metric selection remains downstream of ORA eligibility and accepted mapping.
# Neither an independently filtered Cook's row nor an unmapped row with a finite
# statistic may switch established mapped ORA genes away from the LFC fallback.
metric_guard <- data.frame(
  gene_id=c("mapped_legacy", "unmapped_stat", "cooks_stat"),
  log2FoldChange=c(2, -3, 4), stat=c(NA, 99, 88),
  pvalue=c(0.01, 0.02, NA), padj=c(0.03, 0.04, NA),
  stringsAsFactors=FALSE)
accepted_ora <- metric_guard[metric_guard$gene_id == "mapped_legacy", , drop=FALSE]
guard_prepared <- prepare_mapped_enrichment_populations(
  metric_guard, !is.na(metric_guard$padj), accepted_ora)
guard_rank <- build_population_rank(
  guard_prepared$populations, c("mapped_legacy", NA_character_, "cooks_stat"),
  guard_prepared$rank_statistic)$values
stopifnot(guard_prepared$rank_statistic == "log2FoldChange",
          identical(names(guard_rank), "mapped_legacy"),
          identical(unname(guard_rank), 2))

empty_ora_results <- data.frame(
  gene_id="rank_only", log2FoldChange=2, stat=7, pvalue=0.02, padj=NA_real_,
  stringsAsFactors=FALSE)
empty_prepared <- prepare_mapped_enrichment_populations(
  empty_ora_results, FALSE, empty_ora_results[0, ], empty_ora_results)
empty_rank <- build_population_rank(
  empty_prepared$populations, empty_ora_results$gene_id,
  empty_prepared$rank_statistic)$values
stopifnot(empty_prepared$rank_statistic == "stat",
          identical(names(empty_rank), "rank_only"), identical(unname(empty_rank), 7))

# KEGG bridging happens before the one median reduction. Unequal source-alias
# multiplicity distinguishes the true source-row median (9) from a median of
# pre-collapsed alias medians ((5 + 100) / 2 = 52.5).
bridge_res <- data.frame(
  gene_id=c("aliasA", "aliasA", "aliasB", "other"),
  log2FoldChange=c(1, 9, 100, 10), stat=c(1, 9, 100, 10),
  pvalue=c(0.1, 0.1, 0.1, 0.1), padj=c(0.2, 0.2, 0.2, 0.2),
  stringsAsFactors=FALSE)
bridge_pop <- prepare_main_enrichment_populations(
  bridge_res, !is.na(bridge_res$padj))$populations
bridge_lookup <- c(aliasA="100", aliasB="100", other="200")
bridged_rank <- build_bridged_population_rank(
  bridge_pop, bridge_res$gene_id, bridge_lookup, "stat")
stopifnot(identical(names(bridged_rank$values), c("200", "100")),
          identical(unname(bridged_rank$values), c(10, 9)),
          bridged_rank$duplicate_id_group_n == 1L,
          bridged_rank$duplicate_source_row_n == 3L,
          bridged_rank$duplicate_rows_collapsed == 2L)
bridge_evidence <- rank_evidence_lines(bridged_rank)
stopifnot(any(grepl("1 group(s) containing 3 finite source row(s)",
                    bridge_evidence, fixed=TRUE)),
          any(grepl("2 row(s) collapsed by median", bridge_evidence, fixed=TRUE)))

# The ORA table is collapsed alone. A padj-NA alias mapping to an existing Entrez
# id cannot change that universe, foreground, or established rank; a newly eligible
# Entrez id is appended with its exact selected statistic.
ora_source <- data.frame(
  gene_id=c("ora_alias", "ora_other", "ora_conflict", "ora_block_up", "ora_block_down"),
  base_id=c("ora_alias", "ora_other", "ora_conflict", "ora_block_up", "ora_block_down"),
  ENTREZID=c("101", "202", "404", "505", "505"),
  log2FoldChange=c(2, 0.5, 2, 2, -2), stat=c(5, 2, 4, 6, -5),
  symbol=c("A", "B", "D", "E", "E"), baseMean=c(10, 20, 40, 50, 50),
  stringsAsFactors=FALSE)
ora_collapsed <- collapse_entrez_results(
  ora_source, c("ora_alias", "ora_conflict", "ora_block_up"),
  "ora_block_down", "stat")
additional <- data.frame(
  gene_id=c("rank_alias", "rank_alias_nonfinite", "rank_new", "rank_conflict", "rank_resurrect"),
  base_id=c("rank_alias", "rank_alias_nonfinite", "rank_new", "rank_conflict", "rank_resurrect"),
  ENTREZID=c("101", "101", "303", "404", "505"),
  log2FoldChange=c(-1, 10, 1, -20, 1), stat=c(-1, Inf, 3, -100, 2),
  symbol=c("A2", "A3", "C", "D2", "E2"), baseMean=c(11, 12, 30, 41, 51),
  stringsAsFactors=FALSE)
full_rank_source <- rbind(ora_source, additional)
mapped_rank_info <- build_mapped_gsea_rank(
  full_rank_source, "stat",
  c("ora_alias", "ora_conflict", "ora_block_up"), "ora_block_down",
  ora_collapsed$conflicts$ENTREZID)
mapped_rank <- mapped_rank_info$values
stopifnot(identical(ora_collapsed$table$ENTREZID, c("101", "202", "404")),
          identical(ora_collapsed$table$direction, c("up", "neutral", "up")),
          identical(ora_collapsed$conflicts$ENTREZID, "505"),
          identical(names(mapped_rank), c("303", "101", "202")),
          identical(unname(mapped_rank), c(3, 2, 2)),
          !"404" %in% names(mapped_rank), !"505" %in% names(mapped_rank),
          mapped_rank_info$mapped_finite_source_row_n == 6L,
          mapped_rank_info$mapped_duplicate_id_group_n == 2L,
          mapped_rank_info$mapped_duplicate_rows_collapsed == 2L,
          mapped_rank_info$mapped_nonfinite_source_n == 1L,
          mapped_rank_info$mapped_conflict_n == 1L,
          mapped_rank_info$nonfinite_removed == 0L,
          identical(mapped_rank_info$mapped_table$ENTREZID, c("101", "202", "303")))
mapped_evidence <- rank_evidence_lines(mapped_rank_info)
stopifnot(any(grepl("6 finite source row(s); 2 duplicate Entrez group(s)",
                    mapped_evidence, fixed=TRUE)),
          any(grepl("1 non-finite source score(s) excluded", mapped_evidence,
                    fixed=TRUE)),
          any(grepl("1 direction-conflict Entrez group(s) excluded", mapped_evidence,
                    fixed=TRUE)))

no_extra <- build_mapped_gsea_rank(
  ora_source[1:3, ], "stat", c("ora_alias", "ora_conflict"), character(0))$values
empty_ora <- build_mapped_gsea_rank(
  additional[additional$gene_id == "rank_new", ], "stat")$values
stopifnot(identical(names(no_extra), c("101", "404", "202")),
          identical(unname(no_extra), c(5, 4, 2)),
          identical(names(empty_ora), "303"), identical(unname(empty_ora), 3))
cat("separate ORA and GSEA eligibility contract OK\\n")
'''
    completed = _run_r(tmp_path, code)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "separate ORA and GSEA eligibility contract OK" in completed.stdout


def _assert_helper_dependencies(source: str) -> None:
    main_start = source.index("rule enrichment:")
    main_end = source.index("\n\n# Enrichment visualisations", main_start)
    custom_start = source.index("    rule custom_enrichment:")
    custom_end = source.index("\n    rule custom_enrichment_figure:", custom_start)
    for rule in (source[main_start:main_end], source[custom_start:custom_end]):
        assert 'eligibility_helper="workflow/scripts/enrichment_eligibility.R"' in rule


def test_every_enrichment_route_uses_the_tracked_eligibility_helper() -> None:
    main = MAIN.read_text(encoding="utf-8")
    custom = CUSTOM.read_text(encoding="utf-8")
    helper_source = 'source(file.path(snakemake@scriptdir, "enrichment_eligibility.R"))'
    assert helper_source in main
    assert helper_source in custom
    assert main.count("prepare_enrichment_populations(") == 2
    assert main.count("prepare_mapped_enrichment_populations(") == 1
    assert main.count("prepare_main_enrichment_populations(") == 2
    assert custom.count("prepare_enrichment_populations(") == 1
    assert "res, res$log2FoldChange, !is.na(res$padj)" in custom
    assert main.count(
        "bridge_kegg_geneid(all_ids, ora_kegg_geneid_lookup)"
    ) == 4
    assert main.count(
        "bridge_kegg_geneid(tested_genes, ora_kegg_geneid_lookup)"
    ) == 2
    assert main.count("kegg_rank_info <- build_bridged_population_rank(") == 2
    assert "names(kegg_rank_info$values) <-" not in main
    assert main.count("length(kegg_rank_info$values)") == 2
    assert main.count(
        "geneList = kegg_rank_info$values, rank_info = kegg_rank_info"
    ) == 2
    _assert_helper_dependencies(RULES.read_text(encoding="utf-8"))


def test_eligibility_dependency_gate_rejects_an_untracked_helper() -> None:
    source = RULES.read_text(encoding="utf-8")
    old = source.replace(
        '        eligibility_helper="workflow/scripts/enrichment_eligibility.R",\n',
        "", 1,
    ).replace(
        '            eligibility_helper="workflow/scripts/enrichment_eligibility.R",\n',
        "", 1,
    )
    with pytest.raises(AssertionError):
        _assert_helper_dependencies(old)
