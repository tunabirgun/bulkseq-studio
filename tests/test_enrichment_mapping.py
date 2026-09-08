from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest
import yaml

from app.core.paths import windows_to_wsl_path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workflow" / "scripts" / "run_enrichment.R"
SCOPE_SCRIPT = ROOT / "workflow" / "scripts" / "enrichment_scope.R"
CATALOG = ROOT / "app" / "data" / "reference_catalog.yaml"
KEGG_KEY_FORMS = {"kegg", "ncbi-geneid", "ncbi-proteinid", "uniprot"}


def _r_runtime(script: Path) -> tuple[list[str], str, Callable[[Path], str]]:
    rscript = shutil.which("Rscript")
    if rscript:
        probe = subprocess.run(
            [rscript, "--vanilla", "-e",
             'quit(status=if (requireNamespace("clusterProfiler", quietly=TRUE) && '
             'requireNamespace("org.Sc.sgd.db", quietly=TRUE)) 0 else 1)'],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if probe.returncode == 0:
            return [rscript, "--vanilla"], script.as_posix(), lambda path: str(path)
    wsl = shutil.which("wsl.exe")
    if os.name == "nt" and wsl:
        prefix = subprocess.run(
            [wsl, "--", "bash", "-lc", (
                'if [ -x "$HOME/micromamba/envs/bulkseq/bin/Rscript" ]; then '
                'echo "$HOME/micromamba/envs/bulkseq/bin/Rscript"; '
                'elif [ -x "/root/micromamba/envs/bulkseq/bin/Rscript" ]; then '
                'echo "/root/micromamba/envs/bulkseq/bin/Rscript"; '
                'elif [ -x "$HOME/.local/share/mamba/envs/bulkseq/bin/Rscript" ]; then '
                'echo "$HOME/.local/share/mamba/envs/bulkseq/bin/Rscript"; '
                'else exit 1; fi'
            )],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if prefix.returncode == 0 and prefix.stdout.strip():
            return ([wsl, "--", prefix.stdout.strip(), "--vanilla"],
                    windows_to_wsl_path(script), windows_to_wsl_path)
    pytest.skip("Rscript is not available for the pure enrichment-mapping regression")


def test_mixed_id_routing_excludes_ambiguity_and_direction_conflicts(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
wanted <- c("merge_mapping_candidates", "resolve_configured_keytype",
            "collapse_entrez_results", "direction_gate",
            "mapped_unique", "mapping_fraction", "mapping_percent", "mapping_gate",
            "annotation_resource_gate", "resource_status_to_check",
            "go_annotation_status",
            "normalize_species_name", "validate_kegg_identity", "assess_kegg_resource",
            "MAPPING_WARNING_FRACTION", "MAPPING_REVIEW_FRACTION",
            "ANNOTATION_WARNING_FRACTION", "KEGG_MIN_GENE_SET_SIZE",
            "KEGG_MAX_GENE_SET_SIZE")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}
ids <- c("SYM_AMBIG", "AT1G01010", "ALIAS_ONLY", "SYM_CLEAN", "UNMAPPED")
candidates <- list(
  SYMBOL=data.frame(input_id=c("SYM_AMBIG", "SYM_AMBIG", "AT1G01010", "SYM_CLEAN", NA),
                    ENTREZID=c("20", "10", "99", "50", "999")),
  TAIR=data.frame(input_id=c("AT1G01010"), ENTREZID=c("30")),
  ALIAS=data.frame(input_id=c("ALIAS_ONLY", "SYM_CLEAN"), ENTREZID=c("40", "50"))
)
resolved <- merge_mapping_candidates(ids, candidates, c("SYMBOL", "TAIR", "ALIAS"), "SYMBOL")
stopifnot(identical(resolved[["map"]][["input_id"]],
                    c("AT1G01010", "ALIAS_ONLY", "SYM_CLEAN")))
stopifnot(identical(resolved[["map"]][["ENTREZID"]], c("30", "40", "50")))
stopifnot(identical(resolved[["map"]][["resolution"]],
                    c("routed:TAIR", "eligible_keytypes_agree", "routed:SYMBOL")))
stopifnot(!"SYM_AMBIG" %in% resolved[["map"]][["input_id"]])
amb <- resolved[["exclusions"]][resolved[["exclusions"]][["input_id"]] == "SYM_AMBIG", ]
stopifnot(nrow(amb) == 1L, amb[["reason"]] == "routed_one_to_many",
          amb[["candidate_entrez"]] == "10;20")
stopifnot(resolved[["total_inputs"]] == 5L, resolved[["mapped_inputs"]] == 3L)
stopifnot(resolved[["ambiguous_excluded"]] == 1L, resolved[["unmapped_inputs"]] == 1L)
stopifnot(resolved[["one_to_many_observed"]] == 1L)
stopifnot(resolved[["cross_discordance_observed"]] == 1L,
          resolved[["cross_discordance_resolved"]] == 1L)
stopifnot(identical(mapped_unique(c("10", NA, "", "10", "20")), c("10", "20")))
stopifnot(mapping_gate(0.95, 0.90) == "PASS")
stopifnot(mapping_gate(0.79, 0.90) == "WARNING")
stopifnot(mapping_gate(0.49, 0.90) == "REVIEW_REQUIRED")
stopifnot(annotation_resource_gate(c(BP=0.95, MF=0.90)) == "PASS")
stopifnot(annotation_resource_gate(c(BP=0.781, MF=0.992, CC=0.999, DO=NA)) == "LIMITED_ANNOTATION")
stopifnot(annotation_resource_gate(c(BP=0.49, MF=0.99)) == "LIMITED_ANNOTATION")
stopifnot(annotation_resource_gate(c(BP=0, MF=0.99)) == "NOT_INTERPRETABLE")
stopifnot(annotation_resource_gate(c(DO=NA, KEGG=NA)) == "NOT_RECORDED")
stopifnot(resource_status_to_check("LIMITED_ANNOTATION") == "WARNING")
stopifnot(resource_status_to_check("NOT_INTERPRETABLE") == "REVIEW_REQUIRED")
stopifnot(resource_status_to_check("NOT_RECORDED") == "REVIEW_REQUIRED")
stopifnot(go_annotation_status(
            list(BP=NULL, MF=NULL, CC=NULL), c(BP=NA, MF=NA, CC=NA)) ==
          "NOT_INTERPRETABLE")
stopifnot(resource_status_to_check(go_annotation_status(
            list(BP=NULL, MF=NULL, CC=NULL), c(BP=NA, MF=NA, CC=NA))) ==
          "REVIEW_REQUIRED")
stopifnot(resolve_configured_keytype(
            "SYMBOL", c("GENENAME", "COMMON"), "org.Sc.sgd.db") == "GENENAME")
stopifnot(resolve_configured_keytype(
            "SYMBOL", c("COMMON"), "org.Sc.sgd.db") == "COMMON")
stopifnot(resolve_configured_keytype(
            "SYMBOL", c("GENENAME", "COMMON"), "org.Hs.eg.db") == "SYMBOL")
stopifnot(resolve_configured_keytype(
            "SYMBOL", c("ALIAS"), "org.Sc.sgd.db") == "SYMBOL")

registry <- data.frame(
  kegg.code=c("ath", "hsa", "sce"),
  kegg.name=c("Arabidopsis thaliana", "Homo sapiens", "Saccharomyces cerevisiae"),
  kegg.taxa=c("3702", "9606", "4932"), stringsAsFactors=FALSE)
identity <- validate_kegg_identity("ath", "Arabidopsis thaliana", "3702", registry)
stopifnot(identity[["status"]] == "PASS", identity[["registry_taxon"]] == "3702")
unknown_code <- validate_kegg_identity("zzz", "Arabidopsis thaliana", "3702", registry)
stopifnot(unknown_code[["status"]] == "NOT_INTERPRETABLE",
          grepl("0 exact registry matches", unknown_code[["reason"]], fixed=TRUE))
stopifnot(validate_kegg_identity("hsa", "Arabidopsis thaliana", "3702", registry)[["status"]] ==
          "NOT_INTERPRETABLE")
stopifnot(validate_kegg_identity("ath", "Homo sapiens", "3702", registry)[["status"]] ==
          "NOT_INTERPRETABLE")
stopifnot(validate_kegg_identity("ath", "Arabidopsis thaliana", "9606", registry)[["status"]] ==
          "NOT_INTERPRETABLE")
name_only <- validate_kegg_identity("ath", "Arabidopsis thaliana", NA_character_, registry)
stopifnot(name_only[["status"]] == "PASS",
          grepl("expected taxon not configured", name_only[["reason"]], fixed=TRUE))
stopifnot(validate_kegg_identity(
            "sce", "Saccharomyces cerevisiae", "4932", registry)[["status"]] == "PASS")
stopifnot(validate_kegg_identity(
            "sce", "Saccharomyces cerevisiae", "559292", registry)[["status"]] ==
          "NOT_INTERPRETABLE")

# Negative gate for the defect found by the retained Rice candidate: the legacy
# clusterProfiler taxon table has no osa row, so the former registry rejected a
# valid current KEGG organism code before retrieval could run.
legacy_registry <- registry
old_rice <- validate_kegg_identity(
  "osa", "Oryza sativa Japonica Group", "39947", legacy_registry)
stopifnot(old_rice[["status"]] == "NOT_INTERPRETABLE",
          grepl("0 exact registry matches", old_rice[["reason"]], fixed=TRUE))

current_registry <- rbind(
  transform(registry, kegg.taxon.source="clusterProfiler legacy taxon table"),
  data.frame(kegg.code="osa", kegg.name="Oryza sativa japonica",
             kegg.taxa=NA_character_,
             kegg.taxon.source=NA_character_, stringsAsFactors=FALSE))
official_rice <- function(code) list(
  status="PASS", reason="", code="osa", name="Oryza sativa ssp. japonica",
  taxon="39947", source="KEGG genome record gn:osa")
rice_identity <- validate_kegg_identity(
  "osa", "Oryza sativa Japonica Group", "39947", current_registry,
  taxon_resolver=official_rice)
stopifnot(rice_identity[["status"]] == "PASS",
          rice_identity[["registry_code"]] == "osa",
          rice_identity[["registry_taxon"]] == "39947",
          grepl("KEGG genome record gn:osa", rice_identity[["registry_source"]], fixed=TRUE))

wrong_official_code <- function(code) list(
  status="PASS", reason="", code="dosa", name="Oryza sativa japonica",
  taxon="39947", source="synthetic wrong-code record")
wrong_code_identity <- validate_kegg_identity(
  "osa", "Oryza sativa Japonica Group", "39947", current_registry,
  taxon_resolver=wrong_official_code)
stopifnot(wrong_code_identity[["status"]] == "NOT_INTERPRETABLE",
          grepl("does not match configured code", wrong_code_identity[["reason"]], fixed=TRUE))

wrong_official_taxon <- function(code) list(
  status="PASS", reason="", code="osa", name="Oryza sativa japonica",
  taxon="4530", source="synthetic wrong-taxon record")
wrong_taxon_identity <- validate_kegg_identity(
  "osa", "Oryza sativa Japonica Group", "39947", current_registry,
  taxon_resolver=wrong_official_taxon)
stopifnot(wrong_taxon_identity[["status"]] == "NOT_INTERPRETABLE",
          grepl("not expected taxon 39947", wrong_taxon_identity[["reason"]], fixed=TRUE))

supplied <- as.character(1:100)
effective <- as.character(1:20)
pathways <- list(ath00010=as.character(1:12), ath00020=as.character(9:20))
foregrounds <- list(up=c("1", "2"), down="10", combined=c("1", "2", "10"))
valid_limited <- assess_kegg_resource(identity, TRUE, "", supplied, effective,
                                      pathways, foregrounds, 2L, 0L, 0L)
stopifnot(valid_limited[["status"]] == "LIMITED_ANNOTATION")
stopifnot(valid_limited[["eligible_gene_sets_n"]] == 2L,
          valid_limited[["supported_foreground"]][["combined"]] == 3L)
stopifnot(valid_limited[["ora_adjusted_n"]] == 0L,
          resource_status_to_check(valid_limited[["status"]]) == "WARNING")
stopifnot(assess_kegg_resource(identity, FALSE, "HTTP failure", supplied, effective,
                               pathways, foregrounds, 2L, 0L, 0L)[["status"]] ==
          "NOT_INTERPRETABLE")
stopifnot(assess_kegg_resource(identity, TRUE, "", supplied, character(0),
                               pathways, foregrounds, 2L, 0L, 0L)[["status"]] ==
          "NOT_INTERPRETABLE")
stopifnot(assess_kegg_resource(identity, TRUE, "", supplied, effective,
                               list(tiny=as.character(1:5)), foregrounds,
                               0L, 0L, 0L)[["status"]] == "NOT_INTERPRETABLE")
no_hypotheses <- assess_kegg_resource(identity, TRUE, "", supplied, effective,
                                       pathways, foregrounds, 0L, 0L, 0L)
stopifnot(no_hypotheses[["status"]] == "NOT_INTERPRETABLE",
          grepl("no foreground-overlapping ORA hypotheses",
                no_hypotheses[["reason"]], fixed=TRUE))
stopifnot(assess_kegg_resource(identity, TRUE, "", supplied, effective,
                               pathways, list(up="99", down=character(0), combined="99"),
                               0L, 0L, 0L)[["status"]] == "NOT_INTERPRETABLE")

de <- data.frame(
  gene_id=c("A", "B", "C", "D"), base_id=c("A", "B", "C", "D"),
  symbol=c("A", "B", "C", "D"), ENTREZID=c("100", "100", "200", "300"),
  log2FoldChange=c(2, -3, 1.5, -2), stat=c(4, -5, 3, -4), baseMean=c(10, 12, 8, 9),
  stringsAsFactors=FALSE)
collapsed <- collapse_entrez_results(de, c("A", "C"), c("B", "D"))
stopifnot(identical(collapsed[["table"]][["ENTREZID"]], c("200", "300")))
stopifnot(identical(collapsed[["table"]][["direction"]], c("up", "down")))
stopifnot(nrow(collapsed[["conflicts"]]) == 1L,
          collapsed[["conflicts"]][["ENTREZID"]] == "100")
up_e <- collapsed[["table"]][["ENTREZID"]][collapsed[["table"]][["direction"]] == "up"]
down_e <- collapsed[["table"]][["ENTREZID"]][collapsed[["table"]][["direction"]] == "down"]
stopifnot(length(intersect(up_e, down_e)) == 0L)
stopifnot(direction_gate(0L, nrow(collapsed[["conflicts"]]), 0L) == "REVIEW_REQUIRED")
stopifnot(direction_gate(0L, 0L, 1L) == "REVIEW_REQUIRED")
stopifnot(direction_gate(0L, 0L, 0L) == "PASS")
cat("synthetic mixed-ID ambiguity and direction gates OK\n")
'''
    harness = tmp_path / "mixed_id_routing.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "synthetic mixed-ID ambiguity and direction gates OK" in completed.stdout


def test_current_kegg_species_catalog_includes_rice_and_routes_configured_taxon(
    tmp_path: Path,
) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
wanted <- c("load_kegg_registry", "normalize_species_name",
            "resolve_kegg_taxon", "validate_kegg_identity")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}
registry <- load_kegg_registry()
stopifnot(registry[["status"]] == "PASS")
rice <- registry[["data"]][registry[["data"]][["kegg.code"]] == "osa", , drop=FALSE]
stopifnot(nrow(rice) == 1L,
          rice[["kegg.name"]][[1]] == "Oryza sativa japonica")

# The installed legacy taxon table is allowed to omit osa; the independently
# supplied official resolver must fill the exact taxon without changing the code.
official <- function(code) list(
  status="PASS", reason="", code=code,
  name="Oryza sativa ssp. japonica cultivar Nipponbare",
  taxon="39947", source="synthetic official KEGG GENOME record")
identity <- validate_kegg_identity(
  "osa", "Oryza sativa Japonica Group", "39947", registry,
  taxon_resolver=official)
stopifnot(identity[["status"]] == "PASS",
          identity[["registry_taxon"]] == "39947")
cat("current KEGG rice species and taxon route OK\n")
'''
    harness = tmp_path / "current_kegg_rice_registry.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "current KEGG rice species and taxon route OK" in completed.stdout

    source = SCRIPT.read_text(encoding="utf-8")
    assert "expected_taxon = configured_taxon_id" in source


def test_yeast_symbol_namespace_routes_to_orgdb_genename(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
suppressPackageStartupMessages(library(org.Sc.sgd.db))
exprs <- parse(file={script_path!r})
wanted <- c("merge_mapping_candidates", "resolve_configured_keytype", "map_ids_with_routing",
            "go_readable_for_orgdb")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}
resolved <- map_ids_with_routing(
  c("CBC2", "MID2", "YBR126W-A", "NOT_A_YEAST_GENE"), org.Sc.sgd.db, "SYMBOL",
  "org.Sc.sgd.db")
stopifnot(resolved[["requested_keytype"]] == "SYMBOL",
          resolved[["effective_keytype"]] == "GENENAME",
          resolved[["mapped_inputs"]] == 3L,
          resolved[["unmapped_inputs"]] == 1L,
          resolved[["ambiguous_excluded"]] == 0L)
mapped <- resolved[["map"]]
stopifnot(identical(mapped[["input_id"]], c("CBC2", "MID2", "YBR126W-A")),
          identical(mapped[["resolution"]],
                        c("routed:GENENAME", "routed:GENENAME", "eligible_keytypes_agree")))
stopifnot(!go_readable_for_orgdb(org.Sc.sgd.db))
cat("yeast SYMBOL-to-GENENAME routing OK\n")
'''
    harness = tmp_path / "yeast_symbol_routing.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "yeast SYMBOL-to-GENENAME routing OK" in completed.stdout


def test_gsea_rank_ties_have_deterministic_canonical_id_order(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
wanted <- c("build_deterministic_rank", "rank_evidence_lines",
            "with_deterministic_gsea_ties")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}

# Negative gate: score-only sorting preserves the input order of exact ties, so
# reversing the canonical ids reverses the resulting GSEA tie order.
old <- c("10"=4, "2"=4, "1"=4, "20"=3)
old <- sort(old, decreasing=TRUE)
stopifnot(identical(names(old), c("10", "2", "1", "20")))
stopifnot(!identical(names(old), c("1", "2", "10", "20")))

# Negative gate: the former first-occurrence duplicate rule selected a different
# score when source rows were reversed.
old_first <- function(scores, ids) {{
  names(scores) <- ids
  sort(scores[!duplicated(names(scores))], decreasing=TRUE)
}}
old_forward <- old_first(c(1, 9, 5), c("beta", "beta", "Alpha"))
old_reverse <- old_first(rev(c(1, 9, 5)), rev(c("beta", "beta", "Alpha")))
stopifnot(unname(old_forward[["beta"]]) == 1,
          unname(old_reverse[["beta"]]) == 9,
          !identical(old_forward, old_reverse))

# OrgDb route: all-digit Entrez ids use exact numeric ascending order inside ties.
orgdb_rank <- build_deterministic_rank(
  c(4, 4, 4, 3, Inf, NA_real_, 2),
  c("10", "2", "1", "20", "99", "30", NA_character_))
stopifnot(identical(names(orgdb_rank[["values"]]), c("1", "2", "10", "20")))
stopifnot(identical(unname(orgdb_rank[["values"]]), c(4, 4, 4, 3)))
stopifnot(orgdb_rank[["tie_group_n"]] == 1L,
          orgdb_rank[["tie_pair_n"]] == 3,
          orgdb_rank[["tied_gene_n"]] == 3L,
          orgdb_rank[["invalid_id_removed"]] == 1L,
          orgdb_rank[["nonfinite_removed"]] == 2L,
          orgdb_rank[["duplicate_id_group_n"]] == 0L,
          grepl("numeric canonical ID ascending", orgdb_rank[["policy"]], fixed=TRUE))

# Non-OrgDb finite duplicates are collapsed by their median, then exact ties are
# ordered bytewise. Reversing every source row must produce the identical rank.
nonnumeric_scores <- c(1, 9, 5, 7)
nonnumeric_ids <- c("beta", "beta", "Alpha", "zeta")
nonnumeric_rank <- build_deterministic_rank(
  nonnumeric_scores, nonnumeric_ids)
nonnumeric_reverse <- build_deterministic_rank(
  rev(nonnumeric_scores), rev(nonnumeric_ids))
stopifnot(identical(names(nonnumeric_rank[["values"]]),
                    c("zeta", "Alpha", "beta")),
          identical(nonnumeric_rank[["values"]], nonnumeric_reverse[["values"]]),
          identical(unname(nonnumeric_rank[["values"]]), c(7, 5, 5)))
stopifnot(nonnumeric_rank[["duplicate_id_group_n"]] == 1L,
          nonnumeric_rank[["duplicate_source_row_n"]] == 2L,
          nonnumeric_rank[["duplicate_rows_collapsed"]] == 1L,
          nonnumeric_rank[["tie_pair_n"]] == 1,
          nonnumeric_rank[["tied_gene_n"]] == 2L,
          grepl("collapsed by median", nonnumeric_rank[["policy"]], fixed=TRUE),
          grepl("bytewise UTF-8/C-locale", nonnumeric_rank[["policy"]], fixed=TRUE))

# Filtering precedes collapse: a non-finite first occurrence cannot cause a later
# finite score for the same canonical id to disappear, and reversal is invariant.
nonfinite_first <- build_deterministic_rank(
  c(Inf, 6, 2), c("beta", "beta", "Alpha"))
nonfinite_reverse <- build_deterministic_rank(
  rev(c(Inf, 6, 2)), rev(c("beta", "beta", "Alpha")))
stopifnot(identical(nonfinite_first[["values"]], nonfinite_reverse[["values"]]),
          identical(names(nonfinite_first[["values"]]), c("beta", "Alpha")),
          identical(unname(nonfinite_first[["values"]]), c(6, 2)),
          nonfinite_first[["nonfinite_removed"]] == 1L,
          nonfinite_first[["duplicate_id_group_n"]] == 0L,
          nonfinite_first[["duplicate_rows_collapsed"]] == 0L)

evidence <- rank_evidence_lines(nonnumeric_rank)
stopifnot(any(grepl("1 pair(s) across 1 tie group(s)", evidence, fixed=TRUE)),
          any(grepl("involving 2/3 ranked genes", evidence, fixed=TRUE)),
          any(grepl("1 group(s) containing 2 finite source row(s)",
                    evidence, fixed=TRUE)),
          any(grepl("1 row(s) collapsed by median", evidence, fixed=TRUE)))

# The pinned fgsea warning is replaced with the measured deterministic contract;
# unrelated warnings must still propagate to the caller.
captured <- capture.output({{
  value <- with_deterministic_gsea_ties({{
    warning(paste0("There are ties in the preranked stats (75% of the list).\\n",
                   "The order of those tied genes will be arbitrary, which may produce unexpected results."))
    7L
  }}, orgdb_rank)
}}, type="message")
stopifnot(value == 7L,
          any(grepl("GSEA deterministic tie handling", captured, fixed=TRUE)),
          !any(grepl("arbitrary", captured, fixed=TRUE)))
unrelated_seen <- FALSE
withCallingHandlers(
  with_deterministic_gsea_ties({{ warning("unrelated warning"); 1L }}, orgdb_rank),
  warning=function(w) {{ unrelated_seen <<- TRUE; invokeRestart("muffleWarning") }})
stopifnot(unrelated_seen)
cat("deterministic GSEA rank contract OK\\n")
'''
    harness = tmp_path / "deterministic_gsea_rank.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "deterministic GSEA rank contract OK" in completed.stdout

    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count("rank_info <- build_deterministic_rank(") == 3
    assert "gene_list <- sort" not in source
    assert "deduplicate_ids" not in source


def test_catalog_declares_only_verified_kegg_key_forms() -> None:
    # enrichment_keytype is the AnnotationDbi keytype; kegg_keytype is the enrichKEGG key
    # form. clusterProfiler::download_KEGG(keyType="ncbi-geneid") returns 0 genes for spo,
    # vvi, ghi, pfa and cal while keyType="kegg" returns their full collections, so those
    # organisms must declare the kegg form rather than the NCBI one.
    entries = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["references"]
    by_name = {entry["organism_name"]: entry for entry in entries}
    declared = {
        name: entry["kegg_keytype"]
        for name, entry in by_name.items()
        if entry.get("kegg_keytype")
    }
    assert declared, "no catalog entry declares a kegg_keytype"
    assert set(declared.values()) <= KEGG_KEY_FORMS
    verified_kegg_form = [
        "Schizosaccharomyces pombe", "Vitis vinifera", "Gossypium hirsutum",
        "Zymoseptoria tritici IPO323", "Plasmodium falciparum 3D7", "Candida albicans",
    ]
    for name in verified_kegg_form:
        assert declared.get(name) == "kegg", name
    # C. albicans previously declared no key form at all; its RefSeq gene_id
    # (CAALFM_C100020CA) is the KEGG cal gene id, the same shape its fungal siblings use.
    assert by_name["Candida albicans"]["enrichment_keytype"] == "kegg"
    # An AnnotationDbi keytype must never be declared as the KEGG key form.
    assert not (set(declared.values()) & {"ENSEMBL", "SYMBOL", "TAIR", "FLYBASE"})


def test_kegg_key_form_is_derived_and_reaches_enrichkegg(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
wanted <- c("resolve_kegg_keytype", "KEGG_KEY_FORMS", "run_kegg", "assess_kegg_resource",
            "mapped_unique", "mapping_fraction", "safe_slot", "raw_result_count", "nrows",
            "build_deterministic_rank", "with_deterministic_gsea_ties",
            "ANNOTATION_WARNING_FRACTION", "KEGG_VALID_STATUSES",
            "KEGG_MIN_GENE_SET_SIZE", "KEGG_MAX_GENE_SET_SIZE")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}

# Only KEGG-side key forms are accepted; an AnnotationDbi keytype degrades to "kegg".
stopifnot(resolve_kegg_keytype(NULL, "ncbi-geneid") == "ncbi-geneid",
          resolve_kegg_keytype(NULL, "kegg") == "kegg",
          resolve_kegg_keytype(NULL, "ENSEMBL") == "kegg",
          resolve_kegg_keytype(NULL, "SYMBOL") == "kegg",
          resolve_kegg_keytype(NULL, NULL) == "kegg",
          resolve_kegg_keytype(NULL, "") == "kegg",
          resolve_kegg_keytype("uniprot", "ENSEMBL") == "uniprot",
          resolve_kegg_keytype("not-a-key-form", "ncbi-geneid") == "ncbi-geneid",
          resolve_kegg_keytype("NCBI-GeneID", "kegg") == "ncbi-geneid")

# Every run_kegg call site must pass a key form, not a hardcoded literal: the OrgDb route
# maps to Entrez ("ncbi-geneid"), the two non-OrgDb routes pass the resolved value.
key_args <- character(0)
walk <- function(e) {{
  if (!is.call(e)) return(invisible())
  if (identical(as.character(e[[1]])[[1]], "run_kegg") && length(e) >= 4L)
    key_args <<- c(key_args, paste(deparse(e[[4]]), collapse=""))
  for (part in as.list(e)) tryCatch(walk(part), error=function(err) invisible())
}}
for (expr in exprs) walk(expr)
stopifnot(length(key_args) == 3L,
          sum(key_args == "kegg_keytype") == 2L,
          sum(key_args == '"ncbi-geneid"') == 1L,
          !any(key_args == '"kegg"'))

# The key form reaches enrichKEGG and gseKEGG and is recorded in the audit evidence.
kegg_org <- "ath"; alpha <- 0.05
configured_organism_name <- "Arabidopsis thaliana"; configured_taxon_id <- "3702"
out <- list(kegg=tempfile(fileext=".csv"), kegg_gsea=tempfile(fileext=".csv"))
validate_kegg_identity <- function(kegg_code, expected_name, expected_taxon=NA_character_, ...)
  list(status="PASS", reason="synthetic PASS", configured_code=kegg_code,
       registry_code=kegg_code, registry_name=expected_name,
       registry_taxon=as.character(expected_taxon), expected_name=expected_name,
       expected_taxon=as.character(expected_taxon), registry_source="synthetic")
seen <- new.env()
enrichKEGG <- function(...) {{ seen$ora <- list(...)$keyType; NULL }}
gseKEGG <- function(...) {{ seen$gsea <- list(...)$keyType; NULL }}
ranked <- setNames(as.numeric(10:1), as.character(1:10))
for (form in c("kegg", "ncbi-geneid", "uniprot")) {{
  seen$ora <- NA; seen$gsea <- NA
  res <- run_kegg(as.character(1:5), ranked, form, background=as.character(1:10),
                  foregrounds=list(up=as.character(1:3), down=as.character(4:5),
                                   combined=as.character(1:5)))
  stopifnot(identical(seen$ora, form), identical(seen$gsea, form),
            identical(res$audit$key_form, form))
}}
cat("KEGG key form derivation and propagation OK\\n")
'''
    harness = tmp_path / "kegg_key_form.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "KEGG key form derivation and propagation OK" in completed.stdout


def test_enrichment_scope_is_shared_by_the_figures_and_the_network_export(
    tmp_path: Path,
) -> None:
    command, _, runtime_path = _r_runtime(SCRIPT)
    code = f'''
source({runtime_path(SCOPE_SCRIPT)!r})
df <- function(n) if (n == 0L) data.frame(Description=character(0)) else
  data.frame(Description=sprintf("term%d", seq_len(n)), stringsAsFactors=FALSE)

combined <- select_enrichment_scope(list(ego_all=df(4), ego_up=df(9), ego_down=df(7)))
stopifnot(combined[["scope"]] == "combined-foreground", combined[["selected_n"]] == 4L)
up <- select_enrichment_scope(list(ego_all=df(0), ego_up=df(3), ego_down=df(2)))
stopifnot(up[["scope"]] == "up-regulated", up[["selected_n"]] == 3L)
down <- select_enrichment_scope(list(ego_all=df(0), ego_up=df(1), ego_down=df(6)))
stopifnot(down[["scope"]] == "down-regulated", down[["selected_n"]] == 6L)
tie <- select_enrichment_scope(list(ego_all=df(0), ego_up=df(5), ego_down=df(5)))
stopifnot(tie[["scope"]] == "up-regulated")
empty <- select_enrichment_scope(list())
stopifnot(empty[["scope"]] == "combined-foreground", empty[["selected_n"]] == 0L)

# Negative gate: the export used to take the first object with >= 2 terms, so a
# single-term combined ORA sent the figure and the export to different BH families,
# and a GSEA-only object was exported under the ORA name.
obj <- list(ego_all=df(1), ego_up=df(5), ego_down=df(0), gse=df(8))
old_pick <- NULL
for (nm in c("ego_all", "ego_up", "ego_down", "gse")) {{
  o <- obj[[nm]]
  if (!is.null(o) && nrow(as.data.frame(o)) >= 2) {{ old_pick <- nm; break }}
}}
new <- select_enrichment_scope(obj)
stopifnot(old_pick == "ego_up", new[["scope"]] == "combined-foreground",
          new[["selected_n"]] == 1L)
cat("shared enrichment scope rule OK\\n")
'''
    harness = tmp_path / "enrichment_scope.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "shared enrichment scope rule OK" in completed.stdout

    export = (ROOT / "workflow" / "scripts" / "export_network.R").read_text(encoding="utf-8")
    figures = (ROOT / "workflow" / "scripts" / "make_enrichment_figures.R").read_text(
        encoding="utf-8")
    for source in (export, figures):
        assert 'source(file.path(snakemake@scriptdir, "enrichment_scope.R"))' in source
        assert "select_enrichment_scope(obj)" in source


def test_kegg_audit_separates_tested_hypotheses_from_adjusted_results(
    tmp_path: Path,
) -> None:
    # The audit reports the hypotheses KEGG tested before the cutoff (enrichResult@result)
    # and the pathways that met it (as.data.frame). Under DOSE they are different counts;
    # collapsing them to one number would make the evidence line false.
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
suppressMessages(library(clusterProfiler))
exprs <- parse(file={script_path!r})
wanted <- c("raw_result_count", "nrows", "safe_slot", "run_kegg", "assess_kegg_resource",
            "mapped_unique", "mapping_fraction", "build_deterministic_rank",
            "with_deterministic_gsea_ties", "ANNOTATION_WARNING_FRACTION",
            "KEGG_VALID_STATUSES", "KEGG_MIN_GENE_SET_SIZE", "KEGG_MAX_GENE_SET_SIZE")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}
set.seed(1)
universe <- sprintf("g%03d", 1:200)
t2g <- do.call(rbind, lapply(1:8, function(i)
  data.frame(term=paste0("T", i), gene=sample(universe, 12 + i), stringsAsFactors=FALSE)))
# A term with no query gene is never a tested hypothesis.
t2g <- rbind(t2g, data.frame(term="TX", gene=universe[151:170], stringsAsFactors=FALSE))
query <- universe[1:20]
ek <- enricher(query, universe=universe, TERM2GENE=t2g, pvalueCutoff=0.05,
               qvalueCutoff=0.20, minGSSize=10, maxGSSize=500)

sets <- lapply(split(t2g$gene, t2g$term), function(v) intersect(unique(v), ek@universe))
eligible <- sets[lengths(sets) >= 10 & lengths(sets) <= 500]
overlapping <- sum(vapply(eligible, function(v) any(intersect(ek@gene, ek@universe) %in% v),
                          logical(1)))
cat("eligible gene sets:", length(eligible), " tested hypotheses:", overlapping,
    " raw_result_count:", raw_result_count(ek), " adjusted:", nrows(ek), "\\n")
stopifnot(raw_result_count(ek) == overlapping,
          overlapping < length(eligible),          # TX carries no query gene
          nrows(ek) <= raw_result_count(ek))
# An adjusted-empty result still has tested hypotheses, which is what "not evidence that
# no pathway biology is present" means; the audit must carry both counts, not one twice.
strict <- enricher(query, universe=universe, TERM2GENE=t2g, pvalueCutoff=1e-12,
                   qvalueCutoff=1e-12, minGSSize=10, maxGSSize=500)
stopifnot(nrows(strict) == 0L, raw_result_count(strict) == overlapping)

kegg_org <- "ath"; alpha <- 0.05
configured_organism_name <- "Arabidopsis thaliana"; configured_taxon_id <- "3702"
out <- list(kegg=tempfile(fileext=".csv"), kegg_gsea=tempfile(fileext=".csv"))
validate_kegg_identity <- function(kegg_code, expected_name, expected_taxon=NA_character_, ...)
  list(status="PASS", reason="synthetic PASS", configured_code=kegg_code,
       registry_code=kegg_code, registry_name=expected_name,
       registry_taxon=as.character(expected_taxon), expected_name=expected_name,
       expected_taxon=as.character(expected_taxon), registry_source="synthetic")
enrichKEGG <- function(...) strict
gseKEGG <- function(...) NULL
ranked <- setNames(seq_along(universe) * 1.0, universe)
audit <- run_kegg(query, ranked, "kegg", background=universe,
                  foregrounds=list(up=query[1:10], down=query[11:20], combined=query))$audit
cat("audit hypotheses:", audit$ora_hypotheses_n, " adjusted:", audit$ora_adjusted_n, "\\n")
stopifnot(audit$ora_hypotheses_n == raw_result_count(strict),
          audit$ora_adjusted_n == nrows(strict),
          audit$ora_hypotheses_n > audit$ora_adjusted_n)
cat("KEGG-style ORA hypothesis accounting OK\\n")
'''
    harness = tmp_path / "kegg_hypothesis_counts.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "KEGG-style ORA hypothesis accounting OK" in completed.stdout
