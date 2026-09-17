# Muffle only the benign "package X was built under R version 4.5.3" load warning: the r45 ABI
# is stable, so the 4.5.3-built conda packages run correctly under the pinned r-base 4.5.2;
# real warnings still surface. Shadow library()/require() so it works under Snakemake's
# script runner at any call-stack depth (a top-level globalCallingHandlers does not).
# Aligning r-base to 4.5.3 would force salmon off 1.10.3 onto the 2.x Rust rewrite, so we
# muffle the harmless warning instead of changing the benchmarked environment.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

source(file.path(snakemake@scriptdir, "enrichment_mapping.R"))
source(file.path(snakemake@scriptdir, "enrichment_eligibility.R"))

# Functional enrichment (protocol section 8): GO + KEGG ORA and GSEA via
# clusterProfiler. GO / disease-ontology need a Bioconductor OrgDb (human, mouse,
# fly, worm, zebrafish, yeast, Arabidopsis). KEGG runs for any organism with a
# KEGG organism code (e.g. fungi such as Fusarium graminearum, code "fgr"),
# mapping the gene ids directly, so enrichment still works where no OrgDb exists.
# Best-effort: any failure degrades to empty outputs + a REVIEW_REQUIRED check so
# the pipeline still completes.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

results_file <- snakemake@input[["results"]]
up_file <- snakemake@input[["up"]]
down_file <- snakemake@input[["down"]]
orgdb_name <- snakemake@params[["orgdb"]]
keytype <- snakemake@params[["keytype"]]
kegg_org <- snakemake@params[["kegg"]]
backend <- snakemake@params[["backend"]]
gprofiler_org <- snakemake@params[["gprofiler_organism"]]
configured_organism_name <- snakemake@params[["organism_name"]]
if (is.null(configured_organism_name)) configured_organism_name <- ""
configured_taxon_id <- snakemake@params[["taxon_id"]]
if (is.null(configured_taxon_id)) configured_taxon_id <- ""
alpha <- as.numeric(snakemake@params[["alpha"]])
out <- snakemake@output

# enrichKEGG/gseKEGG key form for the non-OrgDb routes, kept separate from the
# AnnotationDbi `keytype` above: they are different id spaces (KEGG accepts
# kegg | ncbi-geneid | uniprot, AnnotationDbi accepts ENSEMBL/SYMBOL/TAIR/...).
# Prefer an explicit kegg_keytype param; until the rule forwards the catalog field,
# derive it from the configured keytype and accept only KEGG-side values, so an
# AnnotationDbi keytype can never be handed to KEGG. A wrong value is not silent:
# download_KEGG then returns an empty collection and the audit reports
# NOT_INTERPRETABLE ("no pathway gene-set collection").
KEGG_KEY_FORMS <- c("kegg", "ncbi-geneid", "ncbi-proteinid", "uniprot")
resolve_kegg_keytype <- function(configured, fallback) {
  for (value in list(configured, fallback)) {
    value <- tolower(trimws(as.character(value)))
    if (length(value) == 1L && !is.na(value) && value %in% KEGG_KEY_FORMS) return(value)
  }
  "kegg"
}
kegg_keytype <- resolve_kegg_keytype(
  tryCatch(snakemake@params[["kegg_keytype"]], error = function(e) NULL), keytype)

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "10_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

# Locus-tag -> NCBI GeneID bridge for the raw-locus-tag KEGG route (kegg_keytype ==
# "kegg"). Measured 2026-09-10: S. pombe (kegg_organism "spo") keys its KEGG entries
# by bare NCBI GeneID (e.g. spo:2541932), but the workflow's gene ids on that route
# are the RefSeq GTF locus tags (SPOM_SPAC212.11-style); enrichKEGG mapped 0/50 test
# genes without this bridge. `lookup` comes from run_deseq2.R's ncbi_geneid column
# (db_xref "GeneID:<n>" on the GTF gene record). An id already in bare-numeric form
# (e.g. rice LOC<GeneID> after the strip above) needs no bridging and passes through
# unchanged -- a no-op for organisms whose native id already IS the KEGG key.
bridge_kegg_geneid <- function(ids, lookup) {
  if (is.null(lookup) || !length(ids)) return(ids)
  need <- !grepl("^[0-9]+$", ids)
  mapped <- lookup[ids[need]]
  ok <- !is.na(mapped) & nzchar(mapped)
  ids[need][ok] <- unname(mapped[ok])
  ids
}

# The GeneID bridge is a fix for organisms whose KEGG entries key on the bare NCBI
# GeneID (measured: S. pombe 'spo'); for organisms whose KEGG entries key on the
# native locus tag (measured: Fusarium 'fgr', C. albicans 'cal', Z. tritici 'ztr',
# P. falciparum 'pfa', S. aureus 'sao'), bridging maps every id to a key KEGG does
# not use, silently zeroing the result. The catalogue records the form measured for
# every preset organism; an organism outside the catalogue is measured live, since
# KEGG's own tables are the source of truth and coverage changes over time.
KEGG_KEY_FORMS_OBSERVABLE <- c("geneid", "locus_tag")

# Probe sources, in the order that makes them trustworthy for this question.
# link/<org>/pathway is the gene-to-pathway table enrichKEGG is built from, so its
# keys are exactly the keys a query must use; list/<org> is the organism's gene
# catalogue; conv/<org>/ncbi-geneid carries the same KEGG-side keys as its names;
# info/<org> carries no gene keys at all and can only establish that the organism
# is reachable. Injectable so the fallback chain is exercisable without a network.
kegg_probe_sources <- function() {
  list(
    link = function(org) names(KEGGREST::keggLink("pathway", org)),
    list = function(org) names(KEGGREST::keggList(org)),
    conv = function(org) names(KEGGREST::keggConv("ncbi-geneid", org)),
    info = function(org) { KEGGREST::keggInfo(org); character(0) })
}

# Majority over the full key list, not all() over a 50-key head: osa carries 2
# non-numeric keys among 32,578 and ssc 24 among 21,008 (measured 2026-09-13), so a
# single one of those in the head would have flipped the organism to locus_tag and
# suppressed the bridge for every gene.
classify_kegg_keys <- function(keys) {
  ids <- sub("^[^:]+:", "", as.character(keys))
  ids <- ids[!is.na(ids) & nzchar(ids)]
  numeric_n <- sum(grepl("^[0-9]+$", ids))
  list(form = if (!length(ids)) "unknown" else
              if (numeric_n * 2 > length(ids)) "geneid" else "locus_tag",
       numeric_n = numeric_n, total_n = length(ids))
}

probe_kegg_key_form <- function(kegg_org, sources = kegg_probe_sources()) {
  reached <- "none"
  for (name in names(sources)) {
    keys <- tryCatch(as.character(suppressMessages(sources[[name]](kegg_org))),
                     error = function(e) NULL)
    if (is.null(keys)) next
    reached <- name
    # Each source pairs the organism's keys with something else (pathways for link,
    # NCBI GeneIDs for conv). Measured 2026-09-13 on fgr, KEGGREST returns the KEGG
    # side as the names; keeping only the organism-prefixed keys means a source that
    # ever returned the other side falls through to the next one instead of
    # classifying every organism as geneid off the NCBI GeneIDs it was paired with.
    measured <- classify_kegg_keys(keys[startsWith(keys, paste0(kegg_org, ":"))])
    if (!identical(measured$form, "unknown")) return(c(measured, list(probe = name)))
  }
  list(form = "unknown", numeric_n = NA_integer_, total_n = NA_integer_, probe = reached)
}

# The catalogue value wins where it exists, so a preset organism costs no KEGG round
# trip and a network outage cannot change which ids are queried. allow_probe is the
# caller's statement that the key form can still affect this run.
resolve_kegg_key_form <- function(catalogue, allow_probe,
                                  probe = function() probe_kegg_key_form(kegg_org)) {
  declared <- tolower(trimws(as.character(if (is.null(catalogue)) "" else catalogue)))
  unmeasured <- list(form = "unknown", probe = "not attempted",
                     numeric_n = NA_integer_, total_n = NA_integer_)
  if (length(declared) == 1L && !is.na(declared) &&
      declared %in% KEGG_KEY_FORMS_OBSERVABLE)
    return(c(list(form = declared, source = "catalogue"), unmeasured[-1]))
  if (!isTRUE(allow_probe)) return(c(unmeasured, list(source = "not measured")))
  measured <- probe()
  c(measured, list(source = if (identical(measured$form, "unknown"))
                             "live probe failed" else "live"))
}

format_kegg_key_form <- function(resolved) {
  if (is.null(resolved)) return("not recorded")
  total <- resolved$total_n
  detail <- if (is.null(total) || is.na(total) || total == 0L)
    sprintf("probe=%s", resolved$probe)
  else sprintf("probe=%s; numeric keys %d/%d (%.4f)", resolved$probe,
               resolved$numeric_n, total, resolved$numeric_n / total)
  sprintf("%s; source=%s; %s", resolved$form, resolved$source, detail)
}

# Two states make a KEGG query structurally incapable of returning tables, and both
# would otherwise surface as an ordinary empty result: an unmeasurable key form, and
# a GeneID-keyed organism reached from a route that carries no GeneID column to
# bridge with. Neither applies once the ids are already bare numbers.
geneid_column_usable <- function(values) {
  !is.null(values) && any(!is.na(values) & nzchar(as.character(values)))
}

kegg_route_gap <- function(key_form, ids, geneid_available, route, keytype_used,
                           organism = kegg_org) {
  if (!identical(keytype_used, "kegg") || !length(ids)) return(NULL)
  numeric_n <- sum(grepl("^[0-9]+$", ids))
  if (numeric_n * 2 > length(ids)) return(NULL)
  unbridgeable <- length(ids) - numeric_n
  if (identical(key_form, "unknown"))
    return(sprintf(paste0("the KEGG key form for organism %s could not be measured and ",
                          "%d/%d gene ids are not bare NCBI GeneIDs, so the KEGG tables ",
                          "may be empty for that reason"),
                   organism, unbridgeable, length(ids)))
  if (identical(key_form, "geneid") && !isTRUE(geneid_available))
    return(sprintf(paste0("organism %s keys KEGG on bare NCBI GeneIDs but the %s carries ",
                          "no usable ncbi_geneid values, so %d/%d gene ids cannot be ",
                          "bridged and the KEGG tables will be empty for that reason"),
                   organism, route, unbridgeable, length(ids)))
  NULL
}

# The bridge is built only when the measured key form is "geneid"; a "locus_tag" or
# "unknown" organism gets no lookup, so bridge_kegg_geneid is a guaranteed no-op there.
kegg_geneid_lookup_for <- function(kegg_keytype, key_form_observed, base_id, ncbi_geneid) {
  if (!identical(kegg_keytype, "kegg") || !identical(key_form_observed, "geneid") ||
      is.null(ncbi_geneid)) return(NULL)
  setNames(as.character(ncbi_geneid), base_id)
}

effective_ora_universe_n <- function(result) {
  if (is.null(result)) return(NA_integer_)
  tryCatch(length(unique(as.character(methods::slot(result, "universe")))),
           error = function(e) NA_integer_)
}

# Strip a trailing version suffix ONLY from Ensembl-style ids (ENSG00000123.4 ->
# ENSG00000123). A naive sub("\\..*$","",id) corrupts PomBase ids whose ordinal is
# a structural dot (SPOM_SPAC212.11 -> SPOM_SPAC212), so the strip is gated on shape:
# locus tags (FGSG_*, ANIA_*, SPOM_*), TAIR (AT#G#####) and ORF ids pass through.
strip_version <- function(id) {
  v <- grepl("^ENS", id)
  id[v] <- sub("\\.\\d+$", "", id[v])
  # NCBI RefSeq crop/plant gene ids are LOC<GeneID> (e.g. rice LOC4326813); KEGG keys
  # on the bare NCBI GeneID (osa:4326813), so strip the LOC prefix. Shape-gated to
  # LOC + digits only, so MSU/TIGR locus tags (LOC_Os01g01010, underscore) and any
  # other id pass through unchanged.
  # SYMBOL-keyed runs (microarray) can carry legitimate gene symbols like "LOC101927877";
  # only strip the LOC prefix on NCBI-GeneID / KEGG key routes, never for SYMBOL.
  if (!identical(keytype, "SYMBOL")) {
    l <- grepl("^LOC[0-9]+$", id)
    id[l] <- sub("^LOC", "", id[l])
  }
  id
}

# Always create the output files first so the rule succeeds even on failure.
# go_bp/go_mf/go_cc are the uniform per-ontology ORA trio (in addition to the untouched
# go_ora_all.csv); they must exist on every route, incl. the no-route early quit below.
for (k in c("go", "go_up", "go_down", "gsea", "kegg", "kegg_gsea",
            "go_bp", "go_mf", "go_cc")) writeLines("", out[[k]])
# id bridge (gene_id, base_id, symbol, entrez) so the app can resolve an enrichment term's
# genes (entrez on the KEGG-OrgDb / GSEA routes) back to symbols/ids without re-deriving them.
# Written with a header up front; the OrgDb branch fills it in, other branches leave entrez blank.
write.csv(data.frame(gene_id = character(0), base_id = character(0),
                     symbol = character(0), entrez = character(0)),
          out[["id_map"]], row.names = FALSE)
write_id_map <- function(res) {
  tryCatch(write.csv(data.frame(
      gene_id = res$gene_id,
      base_id = if (!is.null(res$base_id)) res$base_id else res$gene_id,
      symbol  = if (!is.null(res$symbol)) res$symbol else NA_character_,
      entrez  = if (!is.null(res$ENTREZID)) res$ENTREZID else ""),
    out[["id_map"]], row.names = FALSE), error = function(e) NULL)
}
# Persist an (empty) objects RDS up front so the enrichment_figures rule always
# has an input, even when enrichment is skipped or fails. Overwritten on success.
saveRDS(list(), out[["objects"]])
summary_lines <- c("Functional enrichment summary", "=============================", "")

has_orgdb     <- !is.null(orgdb_name)    && nzchar(orgdb_name)
has_kegg      <- !is.null(kegg_org)      && nzchar(kegg_org)
has_gprofiler <- !is.null(gprofiler_org) && nzchar(gprofiler_org)

# Resolved once. The catalogue answer applies on every route (an OrgDb that fails to
# load drops this run onto the KEGG-only route, which needs it); the live probe costs
# a KEGG round trip and so runs only where the bridge can still apply -- the OrgDb
# route queries KEGG by ENTREZ id and never consults the key form.
KEGG_KEY_FORM <- resolve_kegg_key_form(
  tryCatch(snakemake@params[["kegg_key_form"]], error = function(e) NULL),
  has_kegg && !has_orgdb)
KEGG_KEY_FORM_OBSERVED <- KEGG_KEY_FORM$form
KEGG_KEY_FORM_EVIDENCE <- format_kegg_key_form(KEGG_KEY_FORM)
DE_ROUTE <- tryCatch(as.character(snakemake@params[["de_route"]]), error = function(e) "")
if (!length(DE_ROUTE) || is.na(DE_ROUTE[1]) || !nzchar(DE_ROUTE[1]))
  DE_ROUTE <- "differential expression route"

# No usable enrichment route (no OrgDb, no KEGG code, no g:Profiler organism):
# skip cleanly rather than risk running against the wrong species' database.
if (!has_orgdb && !has_kegg && !has_gprofiler) {
  writeLines(c(summary_lines,
               "Skipped: no Bioconductor OrgDb, no KEGG code and no g:Profiler organism mapped.",
               "GO supports human, mouse, fly, worm, zebrafish, yeast, Arabidopsis;",
               "KEGG needs a KEGG organism code (set enrichment.kegg_organism, e.g. 'fgr');",
               "g:Profiler needs enrichment.gprofiler_organism (e.g. 'anidulans')."),
             out[["summary"]])
  write_check(out[["check"]], "PASS",
              "Enrichment skipped: organism not mapped (gene-level DE is unaffected).")
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}

# Read a deseq2 up/down CSV and return its gene_id column (Ensembl version stripped).
read_ids_csv <- function(path) {
  if (!file.exists(path)) return(character(0))
  df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
  ids <- strip_version(df$gene_id)
  unique(as.character(ids[!is.na(ids) & nzchar(as.character(ids))]))
}

# The signed model test statistic is the GSEA ranking metric: it orders genes by
# evidence against the null (effect size divided by its standard error), whereas
# log2FoldChange alone promotes large but poorly estimated effects. Every DE writer
# emits `stat` (DESeq2 Wald, limma/voom moderated t, edgeR sign(logFC)*sqrt(F),
# ingested results a direction-checked fallback), so log2FoldChange is used only
# when no finite statistic exists. The exported preranked .rnk already ranks on
# `stat`; this keeps the in-pipeline GSEA and that export on the same metric.
select_rank_statistic <- function(res) {
  if ("stat" %in% names(res)) {
    values <- suppressWarnings(as.numeric(res[["stat"]]))
    if (any(is.finite(values))) return(list(values = values, name = "stat"))
  }
  list(values = suppressWarnings(as.numeric(res[["log2FoldChange"]])),
       name = "log2FoldChange")
}

prepare_main_enrichment_populations <- function(
    results, ora_mask, additional_qc_mask = rep(TRUE, nrow(results))) {
  ora <- results[!is.na(ora_mask) & as.logical(ora_mask), , drop = FALSE]
  candidates <- results[
    additional_gsea_candidate_mask(results, additional_qc_mask), , drop = FALSE]
  metric_source <- if (nrow(ora)) ora else candidates
  rank_stat <- select_rank_statistic(metric_source)
  rank_values <- suppressWarnings(as.numeric(results[[rank_stat$name]]))
  list(
    populations = prepare_enrichment_populations(
      results, rank_values, ora_mask, additional_qc_mask = additional_qc_mask),
    rank_statistic = rank_stat$name)
}

prepare_mapped_enrichment_populations <- function(
    results, ora_mask, mapped_ora, mapped_rank_candidates = mapped_ora) {
  metric_source <- if (nrow(mapped_ora)) mapped_ora else mapped_rank_candidates
  rank_stat <- select_rank_statistic(metric_source)
  rank_values <- suppressWarnings(as.numeric(results[[rank_stat$name]]))
  list(
    populations = prepare_enrichment_populations(results, rank_values, ora_mask),
    rank_statistic = rank_stat$name)
}

# Build every GSEA input under one explicit ordering contract. Statistics must be
# finite and are ordered decreasingly. Exact-score ties use the canonical gene id:
# all-digit ids (Entrez) compare by exact numeric value, while all other id spaces
# compare bytewise after UTF-8 encoding (the C-locale/radix order). After invalid
# ids and non-finite scores are removed, repeated canonical ids are collapsed by
# their median score, matching the deterministic many-to-one reducer used above
# for OrgDb mappings and making the rank invariant to source-row order.
build_deterministic_rank <- function(statistic, canonical_id,
                                     statistic_name = NA_character_) {
  if (length(statistic) != length(canonical_id)) {
    stop("GSEA statistics and canonical ids must have equal lengths")
  }
  statistic <- suppressWarnings(as.numeric(statistic))
  canonical_id <- enc2utf8(as.character(canonical_id))
  valid_id <- !is.na(canonical_id) & nzchar(canonical_id)
  invalid_id_removed <- sum(!valid_id)
  statistic <- statistic[valid_id]
  canonical_id <- canonical_id[valid_id]

  finite <- is.finite(statistic)
  nonfinite_removed <- sum(!finite)
  statistic <- statistic[finite]
  canonical_id <- canonical_id[finite]

  canonical_ids <- unique(canonical_id)
  groups <- lapply(canonical_ids, function(id) which(canonical_id == id))
  group_sizes <- lengths(groups)
  duplicate_groups <- group_sizes > 1L
  duplicate_id_group_n <- sum(duplicate_groups)
  duplicate_source_row_n <- sum(group_sizes[duplicate_groups])
  duplicate_rows_collapsed <- sum(pmax(group_sizes - 1L, 0L))
  statistic <- vapply(groups, function(idx) stats::median(statistic[idx]), numeric(1))
  canonical_id <- canonical_ids

  numeric_ids <- length(canonical_id) > 0L &&
    all(grepl("^[0-9]+$", canonical_id))
  if (numeric_ids) {
    # Length + bytewise ordering of zero-stripped digit strings is exact numeric
    # ordering without double-precision loss; the original id is a deterministic
    # tertiary key for numerically equal spellings such as 1 and 01.
    numeric_key <- sub("^0+", "", canonical_id)
    numeric_key[!nzchar(numeric_key)] <- "0"
    rank_order <- order(-statistic, nchar(numeric_key), numeric_key, canonical_id,
                        method = "radix")
    id_policy <- "numeric canonical ID ascending (exact digit-string order)"
  } else {
    rank_order <- order(-statistic, canonical_id, method = "radix")
    id_policy <- "canonical ID ascending in bytewise UTF-8/C-locale order"
  }
  values <- statistic[rank_order]
  names(values) <- canonical_id[rank_order]

  tie_sizes <- if (length(values)) {
    runs <- rle(sort(unname(values), method = "radix"))$lengths
    runs[runs > 1L]
  } else integer(0)
  ranked_on <- if (is.na(statistic_name)) "the supplied statistic" else
    as.character(statistic_name)
  list(
    values = values,
    statistic_name = ranked_on,
    ranked_gene_n = length(values),
    tie_group_n = length(tie_sizes),
    tie_pair_n = sum(as.double(tie_sizes) * (tie_sizes - 1) / 2),
    tied_gene_n = sum(tie_sizes),
    duplicate_id_group_n = as.integer(duplicate_id_group_n),
    duplicate_source_row_n = as.integer(duplicate_source_row_n),
    duplicate_rows_collapsed = as.integer(duplicate_rows_collapsed),
    invalid_id_removed = as.integer(invalid_id_removed),
    nonfinite_removed = as.integer(nonfinite_removed),
    id_policy = id_policy,
    policy = paste0("ranked on ", ranked_on, "; invalid IDs and non-finite statistics ",
                    "removed; duplicate canonical IDs collapsed by median; finite ",
                    "statistic descending; exact ties by ", id_policy)
  )
}

build_population_rank <- function(population, canonical_id, statistic_name) {
  build_deterministic_rank(
    population$rank_values[population$rank_mask], canonical_id[population$rank_mask],
    statistic_name)
}

build_bridged_population_rank <- function(
    population, source_id, lookup, statistic_name) {
  build_population_rank(
    population, bridge_kegg_geneid(source_id, lookup), statistic_name)
}

rank_evidence_lines <- function(rank_info) {
  mapped_collapse <- if (!is.null(rank_info$mapped_source_row_n)) c(
    sprintf(paste0("Mapped GSEA source collapse: %d finite source row(s); %d duplicate ",
                   "Entrez group(s); %d finite row(s) collapsed by median; %d non-finite ",
                   "source score(s) excluded; %d direction-conflict Entrez group(s) excluded."),
            rank_info$mapped_finite_source_row_n,
            rank_info$mapped_duplicate_id_group_n,
            rank_info$mapped_duplicate_rows_collapsed,
            rank_info$mapped_nonfinite_source_n,
            rank_info$mapped_conflict_n)) else character(0)
  c(
    sprintf("GSEA ranking order: %s.", rank_info$policy),
    sprintf(paste0("GSEA exact-score ties: %.0f pair(s) across %d tie group(s), ",
                   "involving %d/%d ranked genes."),
            rank_info$tie_pair_n, rank_info$tie_group_n,
            rank_info$tied_gene_n, rank_info$ranked_gene_n),
    sprintf(paste0("GSEA duplicate canonical-ID collapse: %d group(s) containing %d ",
                   "finite source row(s); %d row(s) collapsed by median; %d invalid-ID ",
                   "and %d non-finite-score row(s) removed before collapse."),
            rank_info$duplicate_id_group_n, rank_info$duplicate_source_row_n,
            rank_info$duplicate_rows_collapsed, rank_info$invalid_id_removed,
            rank_info$nonfinite_removed),
    mapped_collapse
  )
}

# fgsea 1.36.2 re-sorts an already decreasing vector stably, so the canonical-id
# order above reaches the enrichment calculation. Replace its generic warning that
# tie order is arbitrary with run-specific evidence; every unrelated warning still
# propagates unchanged.
with_deterministic_gsea_ties <- function(expr, rank_info) {
  withCallingHandlers(expr, warning = function(w) {
    warning_text <- conditionMessage(w)
    is_fgsea_tie_notice <-
      grepl("There are ties in the preranked stats", warning_text, fixed = TRUE) &&
      grepl("order of those tied genes will be arbitrary", tolower(warning_text),
            fixed = TRUE)
    if (is_fgsea_tie_notice) {
      message(sprintf(paste0("GSEA deterministic tie handling: %.0f exact-score pair(s) ",
                             "across %d group(s), involving %d/%d ranked genes; %s."),
                      rank_info$tie_pair_n, rank_info$tie_group_n,
                      rank_info$tied_gene_n, rank_info$ranked_gene_n,
                      rank_info$policy))
      invokeRestart("muffleWarning")
    }
  })
}

# Collapse the accepted source rows once, in Entrez space, before deriving any
# enrichment foreground or ranked list. Median effect/statistic summaries avoid
# privileging an arbitrary alias row. A mapped Entrez group is excluded entirely
# when its source rows imply both directions or when its collapsed effect has the
# opposite sign from the one declared direction. This keeps foreground and
# universe in the same conflict-free gene space.
collapse_entrez_results <- function(res, up_ids, down_ids,
                                    statistic_column = "log2FoldChange") {
  required <- c("base_id", "ENTREZID", "log2FoldChange", statistic_column)
  if (!all(required %in% names(res))) {
    stop("Entrez collapse requires columns: ", paste(required, collapse = ", "))
  }
  empty_table <- data.frame(
    gene_id = character(0), base_id = character(0), source_ids = character(0),
    symbol = character(0), ENTREZID = character(0), log2FoldChange = numeric(0),
    stat = numeric(0), baseMean = numeric(0), rank_statistic = numeric(0),
    direction = character(0), stringsAsFactors = FALSE)
  empty_conflicts <- data.frame(
    ENTREZID = character(0), reason = character(0), source_ids = character(0),
    source_directions = character(0), stringsAsFactors = FALSE)
  up_ids <- unique(as.character(up_ids[!is.na(up_ids) & nzchar(as.character(up_ids))]))
  down_ids <- unique(as.character(down_ids[!is.na(down_ids) & nzchar(as.character(down_ids))]))
  if (!nrow(res)) return(list(
    table = empty_table, conflicts = empty_conflicts,
    source_overlap = intersect(up_ids, down_ids), many_to_one_groups = 0L,
    duplicate_rows_collapsed = 0L))

  res$base_id <- as.character(res$base_id)
  res$ENTREZID <- as.character(res$ENTREZID)
  keep <- !is.na(res$base_id) & nzchar(res$base_id) &
          !is.na(res$ENTREZID) & nzchar(res$ENTREZID)
  res <- res[keep, , drop = FALSE]
  source_overlap <- intersect(up_ids, down_ids)
  res$input_direction <- "neutral"
  res$input_direction[res$base_id %in% up_ids] <- "up"
  res$input_direction[res$base_id %in% down_ids] <- "down"
  res$input_direction[res$base_id %in% source_overlap] <- "conflict"

  finite_median <- function(values) {
    values <- suppressWarnings(as.numeric(values))
    values <- values[is.finite(values)]
    if (length(values)) stats::median(values) else NA_real_
  }
  first_text <- function(values) {
    values <- sort(unique(as.character(values[!is.na(values) & nzchar(as.character(values))])))
    if (length(values)) values[[1]] else NA_character_
  }
  groups <- split(seq_len(nrow(res)), res$ENTREZID)
  rows <- list()
  conflicts <- list()
  for (entrez in sort(names(groups))) {
    idx <- groups[[entrez]]
    source_ids <- sort(unique(res$base_id[idx]))
    directions <- sort(unique(res$input_direction[idx]))
    nonneutral <- setdiff(directions, "neutral")
    lfc <- finite_median(res$log2FoldChange[idx])
    # The direction guard keys on the column the GSEA rank is built from, so a
    # collapsed group can never enter the ranked list with a sign opposite to the
    # direction its source rows declared.
    rank_stat <- finite_median(res[[statistic_column]][idx])
    reason <- character(0)
    if ("conflict" %in% directions || all(c("up", "down") %in% nonneutral)) {
      reason <- c(reason, "opposed_source_directions")
    }
    declared <- if (identical(nonneutral, "up")) "up" else
                if (identical(nonneutral, "down")) "down" else "neutral"
    if (identical(declared, "up") && is.finite(rank_stat) && rank_stat <= 0) {
      reason <- c(reason, "collapsed_effect_not_positive")
    }
    if (identical(declared, "down") && is.finite(rank_stat) && rank_stat >= 0) {
      reason <- c(reason, "collapsed_effect_not_negative")
    }
    if (length(reason)) {
      conflicts[[length(conflicts) + 1L]] <- data.frame(
        ENTREZID = entrez, reason = paste(unique(reason), collapse = ";"),
        source_ids = paste(source_ids, collapse = ";"),
        source_directions = paste(directions, collapse = ";"),
        stringsAsFactors = FALSE)
      next
    }
    rows[[length(rows) + 1L]] <- data.frame(
      gene_id = source_ids[[1]], base_id = source_ids[[1]],
      source_ids = paste(source_ids, collapse = ";"),
      symbol = if ("symbol" %in% names(res)) first_text(res$symbol[idx]) else NA_character_,
      ENTREZID = entrez, log2FoldChange = lfc,
      stat = if ("stat" %in% names(res)) finite_median(res[["stat"]][idx]) else NA_real_,
      baseMean = if ("baseMean" %in% names(res)) finite_median(res[["baseMean"]][idx]) else NA_real_,
      rank_statistic = rank_stat,
      direction = declared, stringsAsFactors = FALSE)
  }
  table <- if (length(rows)) do.call(rbind, rows) else empty_table
  conflict_table <- if (length(conflicts)) do.call(rbind, conflicts) else empty_conflicts
  list(
    table = table,
    conflicts = conflict_table,
    source_overlap = source_overlap,
    many_to_one_groups = sum(lengths(groups) > 1L),
    duplicate_rows_collapsed = sum(pmax(lengths(groups) - 1L, 0L)))
}

build_mapped_gsea_rank <- function(rank_res, statistic_column,
                                   up_ids = character(0), down_ids = character(0),
                                   excluded_entrez = character(0)) {
  eligible <- rank_res[!rank_res$ENTREZID %in% excluded_entrez, , drop = FALSE]
  source_statistic <- suppressWarnings(as.numeric(eligible[[statistic_column]]))
  valid_source_id <- !is.na(eligible$ENTREZID) & nzchar(as.character(eligible$ENTREZID))
  finite_source <- valid_source_id & is.finite(source_statistic)
  finite_groups <- table(as.character(eligible$ENTREZID[finite_source]))
  duplicate_groups <- finite_groups[finite_groups > 1L]
  collapsed_result <- collapse_entrez_results(
    eligible, up_ids, down_ids, statistic_column)
  collapsed <- collapsed_result$table
  rank_info <- build_deterministic_rank(
    collapsed$rank_statistic, collapsed$ENTREZID, statistic_column)
  rank_info$mapped_source_row_n <- nrow(eligible)
  rank_info$mapped_finite_source_row_n <- sum(finite_source)
  rank_info$mapped_duplicate_id_group_n <- length(duplicate_groups)
  rank_info$mapped_duplicate_rows_collapsed <- sum(duplicate_groups - 1L)
  rank_info$mapped_nonfinite_source_n <- sum(valid_source_id & !is.finite(source_statistic))
  rank_info$mapped_canonical_gene_n <- nrow(collapsed)
  rank_info$mapped_conflict_n <- nrow(collapsed_result$conflicts)
  rank_info$mapped_table <- collapsed
  rank_info
}

direction_gate <- function(source_overlap_count, conflict_entrez_count,
                           foreground_overlap_count) {
  if (any(c(source_overlap_count, conflict_entrez_count,
            foreground_overlap_count) > 0L)) "REVIEW_REQUIRED" else "PASS"
}

mapping_fraction <- function(mapped, total) {
  if (total < 1) return(NA_real_)
  mapped / total
}

mapping_percent <- function(value) {
  if (is.na(value)) "not applicable" else sprintf("%.1f%%", 100 * value)
}

mapped_unique <- function(mapped) {
  mapped <- as.character(mapped)
  unique(mapped[!is.na(mapped) & nzchar(mapped)])
}

# Coverage below 80% is too incomplete to interpret without a warning; below
# 50% requires explicit review. These are interpretation gates, not claims that
# 80% mapping makes enrichment unbiased.
MAPPING_WARNING_FRACTION <- 0.80
MAPPING_REVIEW_FRACTION <- 0.50
ANNOTATION_WARNING_FRACTION <- 0.80
KEGG_MIN_GENE_SET_SIZE <- 10L
KEGG_MAX_GENE_SET_SIZE <- 500L
# Fewer significant genes than this cannot support an over-representation reading;
# every route gates its check status on it so a near-empty foreground stays visible
# whatever the resource audit says about the databases themselves.
MIN_ORA_FOREGROUND_GENES <- 5L

mapping_gate <- function(tested_fraction, significant_fraction) {
  observed <- c(tested_fraction, significant_fraction)
  observed <- observed[!is.na(observed)]
  if (!length(observed)) return("REVIEW_REQUIRED")
  if (any(observed < MAPPING_REVIEW_FRACTION)) return("REVIEW_REQUIRED")
  if (any(observed < MAPPING_WARNING_FRACTION)) return("WARNING")
  "PASS"
}

# Database annotation eligibility is distinct from global identifier mapping.
# Limited but non-zero resource coverage is a WARNING regardless of whether it
# is above or below 50%; zero, malformed, or unverifiable resource data is the
# condition that makes a resource NOT_INTERPRETABLE.
annotation_resource_gate <- function(fractions) {
  observed <- as.numeric(fractions)
  observed <- observed[is.finite(observed)]
  if (!length(observed)) return("NOT_RECORDED")
  if (any(observed <= 0 | observed > 1)) return("NOT_INTERPRETABLE")
  if (any(observed < ANNOTATION_WARNING_FRACTION)) return("LIMITED_ANNOTATION")
  "PASS"
}

format_annotation_coverage <- function(effective_n, supplied_n) {
  if (is.na(effective_n) || supplied_n < 1L) return("not recorded")
  fraction <- mapping_fraction(effective_n, supplied_n)
  sprintf("%d/%d (%s; %s)", effective_n, supplied_n,
          mapping_percent(fraction), annotation_resource_gate(fraction))
}

resource_status_to_check <- function(status) {
  if (identical(status, "NOT_INTERPRETABLE")) return("REVIEW_REQUIRED")
  if (identical(status, "LIMITED_ANNOTATION")) return("WARNING")
  if (identical(status, "PASS") || identical(status, "NOT_RUN")) return("PASS")
  "REVIEW_REQUIRED"
}

go_readable_for_orgdb <- function(orgdb) {
  "SYMBOL" %in% tryCatch(AnnotationDbi::columns(orgdb), error = function(e) character(0))
}

go_annotation_status <- function(results, fractions) {
  # enrichGO returns a valid zero-row enrichResult when the analysis ran but no
  # terms passed. NULL means an attempted ontology failed and must not be
  # reported as merely unrecorded or allowed through the run gate.
  if (!length(results) || any(vapply(results, is.null, logical(1)))) {
    return("NOT_INTERPRETABLE")
  }
  annotation_resource_gate(fractions)
}

status_max <- function(...) {
  priority <- c(PASS = 0L, WARNING = 1L, REVIEW_REQUIRED = 2L, FAIL = 3L)
  values <- unlist(list(...), use.names = FALSE)
  values <- values[values %in% names(priority)]
  if (!length(values)) return("REVIEW_REQUIRED")
  values[which.max(priority[values])]
}

# Normalize names only for exact scientific-name comparison across punctuation,
# case, and the optional taxonomic word "Group" (e.g. Oryza japonica Group).
normalize_species_name <- function(value) {
  value <- tolower(trimws(as.character(value)))
  value <- gsub("[^a-z0-9]+", " ", value)
  value <- gsub("\\bgroup\\b", " ", value)
  trimws(gsub("\\s+", " ", value))
}

load_kegg_registry <- function() {
  # clusterProfiler's current internal species catalog is the authority for the
  # organism-code namespace used by enrichKEGG/gseKEGG. Its legacy kegg_taxa.rds
  # is useful for offline taxon ids but is incomplete (for example, it omits the
  # valid current rice code `osa`), so it may augment but must not define the set
  # of accepted organism codes.
  species <- tryCatch(
    clusterProfiler::search_kegg_organism(
      ".", by = "kegg_code", use_internal_data = TRUE),
    error = function(e) e)
  if (inherits(species, "error") || !is.data.frame(species) ||
      !all(c("kegg_code", "scientific_name") %in% names(species))) {
    reason <- if (inherits(species, "error")) conditionMessage(species) else
      "clusterProfiler internal KEGG species catalog is malformed"
    return(list(status = "NOT_INTERPRETABLE", reason = reason,
                data = NULL, source = "clusterProfiler internal KEGG species catalog"))
  }
  registry <- data.frame(
    kegg.code = trimws(as.character(species$kegg_code)),
    kegg.name = trimws(as.character(species$scientific_name)),
    kegg.taxa = NA_character_,
    kegg.taxon.source = NA_character_,
    stringsAsFactors = FALSE)
  registry <- registry[nzchar(registry$kegg.code), , drop = FALSE]
  registry <- registry[!duplicated(registry$kegg.code), , drop = FALSE]

  taxon_path <- system.file("extdata/kegg_taxa.rds", package = "clusterProfiler")
  taxon_registry <- if (nzchar(taxon_path) && file.exists(taxon_path))
    tryCatch(readRDS(taxon_path), error = function(e) NULL) else NULL
  if (is.data.frame(taxon_registry) &&
      all(c("kegg.code", "kegg.taxa") %in% names(taxon_registry))) {
    taxon_match <- match(registry$kegg.code, as.character(taxon_registry$kegg.code))
    has_taxon <- !is.na(taxon_match)
    registry$kegg.taxa[has_taxon] <-
      as.character(taxon_registry$kegg.taxa[taxon_match[has_taxon]])
    registry$kegg.taxon.source[has_taxon] <- taxon_path
  }
  list(status = "PASS", reason = "", data = registry,
       source = "clusterProfiler internal KEGG species catalog")
}

# Resolve a missing legacy taxon id from the official KEGG GENOME entry for the
# exact configured organism code. The returned ORG_CODE is checked again by the
# caller, so a redirect or a wrong record fails closed rather than borrowing the
# taxon from a related strain/database code.
resolve_kegg_taxon <- function(kegg_code) {
  code <- trimws(as.character(kegg_code))
  source <- sprintf("KEGG GENOME record gn:%s", code)
  empty <- list(status = "NOT_INTERPRETABLE", reason = "", code = NA_character_,
                name = NA_character_, taxon = NA_character_, source = source)
  if (!nzchar(code)) {
    empty$reason <- "configured KEGG code is missing"
    return(empty)
  }
  if (!requireNamespace("KEGGREST", quietly = TRUE)) {
    empty$reason <- "KEGGREST is unavailable"
    return(empty)
  }
  record <- tryCatch(KEGGREST::keggGet(paste0("gn:", code)),
                     error = function(e) e)
  if (inherits(record, "error") || !is.list(record) || length(record) != 1L) {
    empty$reason <- if (inherits(record, "error")) conditionMessage(record) else
      "official KEGG GENOME lookup returned no unique record"
    return(empty)
  }
  record <- record[[1]]
  taxonomy <- record$TAXONOMY
  taxonomy_id <- if (is.list(taxonomy)) taxonomy$TAXONOMY else taxonomy
  taxonomy_id <- trimws(as.character(taxonomy_id))
  taxonomy_id <- sub("^TAX:", "", taxonomy_id)
  resolved_code <- trimws(as.character(record$ORG_CODE))
  if (length(resolved_code) != 1L || !nzchar(resolved_code) ||
      length(taxonomy_id) != 1L || !grepl("^[0-9]+$", taxonomy_id)) {
    empty$reason <- "official KEGG GENOME record lacks a unique organism code or NCBI taxon"
    return(empty)
  }
  empty$status <- "PASS"
  empty$code <- resolved_code
  empty$name <- trimws(as.character(record$NAME[[1]]))
  empty$taxon <- taxonomy_id
  empty
}

validate_kegg_identity <- function(kegg_code, expected_name, expected_taxon = NA_character_,
                                   registry = load_kegg_registry(),
                                   taxon_resolver = resolve_kegg_taxon) {
  if (is.data.frame(registry)) registry <- list(
    status = "PASS", reason = "", data = registry, source = "synthetic")
  empty <- list(status = "NOT_INTERPRETABLE", reason = "", configured_code = as.character(kegg_code),
                registry_code = NA_character_, registry_name = NA_character_,
                registry_taxon = NA_character_, expected_name = as.character(expected_name),
                expected_taxon = as.character(expected_taxon), registry_source = registry$source)
  if (!identical(registry$status, "PASS") || is.null(registry$data)) {
    empty$reason <- paste("KEGG registry unavailable:", registry$reason)
    return(empty)
  }
  code <- trimws(as.character(kegg_code))
  hits <- registry$data[as.character(registry$data$kegg.code) == code, , drop = FALSE]
  if (!nzchar(code) || nrow(hits) != 1L) {
    empty$reason <- sprintf("configured KEGG code '%s' has %d exact registry matches", code, nrow(hits))
    return(empty)
  }
  empty$registry_code <- as.character(hits$kegg.code[[1]])
  empty$registry_name <- as.character(hits$kegg.name[[1]])
  empty$registry_taxon <- as.character(hits$kegg.taxa[[1]])
  expected_name <- trimws(as.character(expected_name))
  if (!nzchar(expected_name)) {
    empty$reason <- "configured reference organism name is missing"
    return(empty)
  }
  if (!identical(normalize_species_name(expected_name),
                 normalize_species_name(empty$registry_name))) {
    empty$reason <- sprintf("KEGG code %s resolves to %s, not configured organism %s",
                            code, empty$registry_name, expected_name)
    return(empty)
  }
  if (!nzchar(empty$registry_taxon) || is.na(empty$registry_taxon)) {
    resolved <- tryCatch(taxon_resolver(code), error = function(e) list(
      status = "NOT_INTERPRETABLE", reason = conditionMessage(e),
      code = NA_character_, taxon = NA_character_, source = "KEGG taxon resolver"))
    if (!identical(resolved$status, "PASS")) {
      empty$reason <- sprintf("KEGG code %s has no offline registry taxon and official lookup failed: %s",
                              code, as.character(resolved$reason))
      return(empty)
    }
    if (!identical(trimws(as.character(resolved$code)), code)) {
      empty$reason <- sprintf("official KEGG organism code %s does not match configured code %s",
                              as.character(resolved$code), code)
      return(empty)
    }
    empty$registry_taxon <- trimws(as.character(resolved$taxon))
    empty$registry_source <- sprintf("%s; taxon: %s", empty$registry_source,
                                     as.character(resolved$source))
  } else if ("kegg.taxon.source" %in% names(hits) &&
             nzchar(as.character(hits$kegg.taxon.source[[1]]))) {
    empty$registry_source <- sprintf("%s; taxon: %s", empty$registry_source,
                                     as.character(hits$kegg.taxon.source[[1]]))
  }
  expected_taxon <- trimws(as.character(expected_taxon))
  if (nzchar(expected_taxon) && !is.na(expected_taxon) &&
      !identical(expected_taxon, empty$registry_taxon)) {
    empty$reason <- sprintf("KEGG code %s resolves to taxon %s, not expected taxon %s",
                            code, empty$registry_taxon, expected_taxon)
    return(empty)
  }
  if (!nzchar(empty$registry_taxon) || is.na(empty$registry_taxon)) {
    empty$reason <- sprintf("KEGG code %s has no registry taxon", code)
    return(empty)
  }
  empty$status <- "PASS"
  empty$reason <- if (nzchar(expected_taxon) && !is.na(expected_taxon))
    "exact code/name/taxon registry match" else
    "exact code/name registry match; independent expected taxon not configured"
  empty
}

orgdb_identity <- function(orgdb) {
  metadata <- tryCatch(AnnotationDbi::metadata(orgdb), error = function(e) NULL)
  if (is.null(metadata) || !all(c("name", "value") %in% names(metadata))) {
    return(list(name = "", taxon = NA_character_))
  }
  get_value <- function(key) {
    value <- metadata$value[toupper(as.character(metadata$name)) == key]
    if (length(value)) as.character(value[[1]]) else ""
  }
  list(name = get_value("ORGANISM"), taxon = get_value("TAXID"))
}

safe_slot <- function(object, name, default = NULL) {
  if (is.null(object) || !name %in% methods::slotNames(object)) return(default)
  tryCatch(methods::slot(object, name), error = function(e) default)
}

raw_result_count <- function(object) {
  result <- safe_slot(object, "result", data.frame())
  if (is.data.frame(result)) nrow(result) else 0L
}

KEGG_VALID_STATUSES <- c("PASS", "LIMITED_ANNOTATION")

kegg_package_version <- function(pkg) {
  tryCatch(as.character(utils::packageVersion(pkg)), error = function(e) NA_character_)
}
kegg_query_date_utc <- function() format(Sys.time(), tz = "UTC", format = "%Y-%m-%d")

assess_kegg_resource <- function(identity, retrieval_success, retrieval_error,
                                 supplied_universe, effective_universe, pathway_sets,
                                 foregrounds, ora_hypotheses_n, ora_adjusted_n,
                                 gsea_adjusted_n,
                                 min_size = KEGG_MIN_GENE_SET_SIZE,
                                 max_size = KEGG_MAX_GENE_SET_SIZE,
                                 ora_attempted = TRUE, ora_success = retrieval_success,
                                 ora_error = retrieval_error,
                                 gsea_attempted = TRUE, gsea_success = retrieval_success,
                                 gsea_error = retrieval_error,
                                 ranked_ids = character(0)) {
  clean_ids <- function(values) mapped_unique(as.character(values))
  supplied <- clean_ids(supplied_universe)
  effective <- clean_ids(effective_universe)
  ranked <- clean_ids(ranked_ids)
  foregrounds <- lapply(foregrounds, clean_ids)
  for (name in c("up", "down", "combined")) {
    if (is.null(foregrounds[[name]])) foregrounds[[name]] <- character(0)
  }
  sets <- if (is.list(pathway_sets)) lapply(pathway_sets, clean_ids) else list()
  # Gene sets are eligible per leg: restricted to that leg's own gene space (the
  # ORA universe, the ranked list) and then to the declared size window.
  eligible_for <- function(space) {
    restricted <- lapply(sets, function(values) intersect(values, space))
    sizes <- lengths(restricted)
    restricted[sizes >= min_size & sizes <= max_size]
  }
  eligible <- eligible_for(effective)
  eligible_ids <- clean_ids(unlist(eligible, use.names = FALSE))
  gsea_eligible <- eligible_for(ranked)
  gsea_annotated <- clean_ids(unlist(gsea_eligible, use.names = FALSE))
  supported <- if (length(eligible_ids))
    lapply(foregrounds, function(values) length(intersect(values, eligible_ids))) else
    list(up = 0L, down = 0L, combined = 0L)
  result <- list(
    status = "NOT_INTERPRETABLE", reason = "", identity = identity,
    retrieval_success = isTRUE(retrieval_success), retrieval_error = as.character(retrieval_error),
    supplied_universe_n = length(supplied), effective_universe_n = length(effective),
    effective_universe_fraction = mapping_fraction(length(effective), length(supplied)),
    pathway_collection_n = length(sets),
    eligible_gene_sets_n = length(eligible), eligible_universe_n = length(eligible_ids),
    supported_foreground = supported,
    foreground_total = lapply(foregrounds, length),
    ora_hypotheses_n = as.integer(ora_hypotheses_n),
    ora_adjusted_n = as.integer(ora_adjusted_n),
    gsea_adjusted_n = as.integer(gsea_adjusted_n),
    gsea_ranked_n = length(ranked),
    gsea_gene_sets_n = length(gsea_eligible),
    gsea_annotated_n = length(gsea_annotated),
    gsea_annotated_fraction = mapping_fraction(length(gsea_annotated), length(ranked)),
    min_size = as.integer(min_size), max_size = as.integer(max_size))

  ora_leg <- function() {
    fail <- function(reason) list(status = "NOT_INTERPRETABLE", reason = reason)
    if (!identical(identity$status, "PASS")) return(fail(identity$reason))
    if (!isTRUE(ora_attempted)) return(list(
      status = "NOT_RUN",
      reason = if (nzchar(ora_error)) as.character(ora_error) else
        "KEGG over-representation was not run"))
    if (!isTRUE(ora_success)) return(fail(paste("KEGG ORA retrieval failed:", ora_error)))
    if (!length(supplied) || !length(effective) || any(!effective %in% supplied)) {
      return(fail("KEGG effective universe is zero, malformed, or outside the supplied tested universe"))
    }
    if (!length(sets)) return(fail("KEGG retrieval returned no pathway gene-set collection"))
    if (!length(eligible) || !length(eligible_ids)) {
      return(fail(sprintf("KEGG returned no eligible %d-%d gene pathway hypotheses",
                          min_size, max_size)))
    }
    if (supported$combined < 1L) {
      return(fail("no combined foreground genes are supported by eligible KEGG pathways"))
    }
    if (!is.finite(result$ora_hypotheses_n) || result$ora_hypotheses_n < 1L) {
      return(fail("KEGG produced no foreground-overlapping ORA hypotheses after size filtering"))
    }
    fractions <- c(
      result$effective_universe_fraction,
      vapply(c("up", "down", "combined"), function(name) {
        total <- result$foreground_total[[name]]
        if (total > 0L) supported[[name]] / total else NA_real_
      }, numeric(1)))
    limited <- any(fractions[is.finite(fractions)] < ANNOTATION_WARNING_FRACTION)
    list(status = if (limited) "LIMITED_ANNOTATION" else "PASS",
         reason = if (limited)
           "valid KEGG resource with limited universe or foreground annotation coverage" else
           "valid KEGG resource and supported foreground")
  }

  gsea_leg <- function() {
    fail <- function(reason) list(status = "NOT_INTERPRETABLE", reason = reason)
    if (!identical(identity$status, "PASS")) return(fail(identity$reason))
    if (!isTRUE(gsea_attempted)) return(list(
      status = "NOT_RUN",
      reason = if (nzchar(gsea_error)) as.character(gsea_error) else
        "KEGG gene-set enrichment was not run"))
    if (!isTRUE(gsea_success)) return(fail(paste("KEGG GSEA retrieval failed:", gsea_error)))
    if (!length(sets)) return(fail("KEGG retrieval returned no pathway gene-set collection"))
    if (!length(gsea_eligible) && length(ranked)) {
      return(fail(sprintf("KEGG returned no eligible %d-%d gene pathway hypotheses for the ranked list",
                          min_size, max_size)))
    }
    fraction <- result$gsea_annotated_fraction
    limited <- is.finite(fraction) && fraction < ANNOTATION_WARNING_FRACTION
    list(status = if (limited) "LIMITED_ANNOTATION" else "PASS",
         reason = if (limited)
           "valid KEGG resource with limited ranked-list annotation coverage" else
           "valid KEGG resource and annotated ranked list")
  }

  # Worst status across the legs, ignoring legs that never ran: a leg that did not
  # run is not evidence against the other leg's resource, and only when neither ran
  # is the resource itself unexercised.
  overall_of <- function(legs) {
    priority <- c(PASS = 0L, LIMITED_ANNOTATION = 1L, NOT_INTERPRETABLE = 2L)
    ran <- Filter(function(leg) leg$status %in% names(priority), legs)
    if (!length(ran)) return(list(
      status = "NOT_RUN",
      reason = paste(vapply(legs, function(leg) leg$reason, ""), collapse = "; ")))
    ran[[which.max(priority[vapply(ran, function(leg) leg$status, "")])]]
  }
  ora <- ora_leg()
  gsea <- gsea_leg()
  result$ora_status <- ora$status
  result$ora_reason <- ora$reason
  result$gsea_status <- gsea$status
  result$gsea_reason <- gsea$reason
  overall <- overall_of(list(ora, gsea))
  result$status <- overall$status
  result$reason <- overall$reason
  result
}

format_fraction_count <- function(numerator, denominator) {
  if (is.na(numerator) || is.na(denominator) || denominator < 1L) return("not recorded")
  sprintf("%d/%d (%s)", numerator, denominator,
          mapping_percent(mapping_fraction(numerator, denominator)))
}

kegg_evidence_lines <- function(kegg) {
  audit <- kegg$audit
  if (is.null(audit)) return("KEGG resource status: NOT_INTERPRETABLE; audit evidence missing.")
  identity <- audit$identity
  interpretation <- if (audit$status %in% KEGG_VALID_STATUSES &&
                        audit$ora_adjusted_n == 0L && audit$gsea_adjusted_n == 0L) {
    paste0("no supported KEGG pathways met the adjusted criterion; this is not evidence ",
           "that no pathway biology is present")
  } else if (audit$status %in% KEGG_VALID_STATUSES) {
    sprintf("%d ORA and %d GSEA pathways met the adjusted criterion",
            audit$ora_adjusted_n, audit$gsea_adjusted_n)
  } else audit$reason
  c(
    sprintf(paste0("KEGG identity verification: %s; configured code=%s; registry code=%s; ",
                   "organism=%s; taxon=%s; expected organism=%s; expected taxon=%s; source=%s"),
            identity$status, identity$configured_code, identity$registry_code,
            identity$registry_name, identity$registry_taxon,
            identity$expected_name, identity$expected_taxon, identity$registry_source),
    sprintf("KEGG retrieval: %s; key form=%s; pathway collection=%d; detail=%s",
            if (audit$retrieval_success) "SUCCESS" else "FAILED",
            if (is.null(audit$key_form)) "not recorded" else as.character(audit$key_form),
            audit$pathway_collection_n,
            if (nzchar(audit$retrieval_error)) audit$retrieval_error else "none"),
    sprintf("KEGG retrieval date (UTC): %s; KEGGREST %s; clusterProfiler %s",
            if (is.null(audit$query_date_utc) || is.na(audit$query_date_utc))
              "not recorded" else audit$query_date_utc,
            if (is.null(audit$keggrest_version) || is.na(audit$keggrest_version))
              "not recorded" else audit$keggrest_version,
            if (is.null(audit$clusterprofiler_version) || is.na(audit$clusterprofiler_version))
              "not recorded" else audit$clusterprofiler_version),
    sprintf("KEGG effective resource universe: %s; eligible %d-%d pathway universe=%d",
            format_fraction_count(audit$effective_universe_n, audit$supplied_universe_n),
            audit$min_size, audit$max_size, audit$eligible_universe_n),
    sprintf("KEGG supported foreground: up %s; down %s; combined %s",
            format_fraction_count(audit$supported_foreground$up, audit$foreground_total$up),
            format_fraction_count(audit$supported_foreground$down, audit$foreground_total$down),
            format_fraction_count(audit$supported_foreground$combined, audit$foreground_total$combined)),
    sprintf(paste0("KEGG eligible hypotheses/gene sets: %d after %d-%d filter; ",
                   "foreground-overlapping ORA hypotheses tested before the cutoff=%d"),
            audit$eligible_gene_sets_n, audit$min_size, audit$max_size,
            audit$ora_hypotheses_n),
    sprintf(paste0("KEGG ranked-list annotation: annotated ranked genes %s; eligible ",
                   "%d-%d gene sets for the ranked list=%d"),
            format_fraction_count(audit$gsea_annotated_n, audit$gsea_ranked_n),
            audit$min_size, audit$max_size, audit$gsea_gene_sets_n),
    sprintf("KEGG adjusted results: ORA=%d; GSEA=%d; BH pvalueCutoff=%s; qvalueCutoff=0.20",
            audit$ora_adjusted_n, audit$gsea_adjusted_n,
            format(alpha, scientific = FALSE, trim = TRUE)),
    sprintf("KEGG ORA status: %s; adjusted pathways=%d; detail=%s",
            audit$ora_status, audit$ora_adjusted_n, audit$ora_reason),
    sprintf("KEGG GSEA status: %s; adjusted pathways=%d; detail=%s",
            audit$gsea_status, audit$gsea_adjusted_n, audit$gsea_reason),
    sprintf("KEGG resource status: %s; %s", audit$status, interpretation),
    sprintf("KEGG key form observed: %s",
            if (is.null(audit$kegg_key_form_observed)) "not recorded" else audit$kegg_key_form_observed))
}

# KEGG ORA (combined significant set) + GSEA (ranked list). The returned audit
# distinguishes a valid but sparsely annotated resource from an invalid or
# unverifiable resource, and preserves zero adjusted results as a negative result.
# The two legs are audited and written independently: an empty significant-gene
# foreground makes ORA impossible without saying anything about the ranked GSEA,
# and a failed gseKEGG call says nothing about the ORA.
run_kegg <- function(genes_all, ranked, kegg_keytype, background = NULL,
                     foregrounds = list(up = character(0), down = character(0),
                                        combined = genes_all),
                     expected_name = configured_organism_name,
                     expected_taxon = configured_taxon_id,
                     rank_info = build_deterministic_rank(ranked, names(ranked)),
                     key_form_observed = if (exists("KEGG_KEY_FORM_EVIDENCE"))
                       KEGG_KEY_FORM_EVIDENCE else "not recorded") {
  genes_all <- mapped_unique(genes_all)
  background <- mapped_unique(background)
  identity <- validate_kegg_identity(kegg_org, expected_name, expected_taxon)
  empty_return <- function(reason) {
    audit <- assess_kegg_resource(
      identity, FALSE, reason, background, character(0), list(), foregrounds,
      0L, 0L, 0L, ranked_ids = names(ranked))
    audit$key_form <- kegg_keytype
    audit$kegg_key_form_observed <- key_form_observed
    audit$query_date_utc <- kegg_query_date_utc()
    audit$keggrest_version <- kegg_package_version("KEGGREST")
    audit$clusterprofiler_version <- kegg_package_version("clusterProfiler")
    list(ekegg_all = NULL, kegg_gse = NULL, n_ora = 0L, n_gsea = 0L,
         audit = audit)
  }
  if (!identical(identity$status, "PASS")) return(empty_return(identity$reason))
  # Use the rank object as the single source of truth so the vector passed to
  # gseKEGG cannot diverge from the tie counts and policy reported for it.
  ranked <- rank_info$values
  # `background` is the ORA universe: the genes that were actually tested, in the
  # same id space as `genes_all`. Without it enrichKEGG defaults to every gene in
  # the KEGG organism, which inflates enrichment -- an unexpressed pathway counts
  # as depleted background rather than as absent from the experiment. enrichGO and
  # enrichDO in this script already pass their universe; KEGG was the sole outlier.
  # Caller supplies it per route because the id space differs (ENTREZ on the OrgDb
  # route, raw locus tags elsewhere), and a background in the wrong space would
  # silently return zero terms.
  kegg_args <- list(gene = genes_all, organism = kegg_org, keyType = kegg_keytype,
                    pAdjustMethod = "BH", pvalueCutoff = alpha, qvalueCutoff = 0.20,
                    minGSSize = KEGG_MIN_GENE_SET_SIZE,
                    maxGSSize = KEGG_MAX_GENE_SET_SIZE)
  # Omit the argument entirely when no background is available, rather than passing
  # an empty vector: clusterProfiler treats a zero-length universe as a failure, and
  # trading an inflated result for no result is the worse error.
  if (length(background) > 0) kegg_args$universe <- background
  ora_attempted <- length(genes_all) >= 1
  gsea_attempted <- length(ranked) > 0
  ek_error <- ""
  ek <- if (ora_attempted) tryCatch(
    do.call(enrichKEGG, kegg_args), error = function(e) {
      ek_error <<- conditionMessage(e); message("enrichKEGG failed: ", ek_error); NULL
    }) else { ek_error <- "combined foreground is empty"; NULL }
  kg <- NULL
  kg_error <- ""
  if (gsea_attempted) {
    set.seed(42)
    kg <- tryCatch(
      with_deterministic_gsea_ties(
        gseKEGG(geneList = ranked, organism = kegg_org, keyType = kegg_keytype,
                pvalueCutoff = alpha, pAdjustMethod = "BH",
                minGSSize = KEGG_MIN_GENE_SET_SIZE, maxGSSize = KEGG_MAX_GENE_SET_SIZE,
                eps = 0, seed = TRUE, verbose = FALSE),
        rank_info),
      error = function(e) {
        kg_error <<- conditionMessage(e); message("gseKEGG failed: ", kg_error); NULL
      })
  } else kg_error <- "ranked list is empty"
  ora_success <- !is.null(ek) && !nzchar(ek_error)
  gsea_success <- !is.null(kg) && !nzchar(kg_error)
  # Only a leg that was actually attempted can have failed retrieval; an empty
  # foreground or ranked list is a property of the differential expression, not of
  # the KEGG resource, and must not be reported as a retrieval failure.
  attempted_error <- c(if (ora_attempted) ek_error, if (gsea_attempted) kg_error)
  retrieval_error <- paste(attempted_error[nzchar(attempted_error)], collapse = "; ")
  # gseKEGG's geneSets are the organism's pathway collection; fall back to the ORA
  # object's own collection so a failed GSEA leg cannot strip the ORA leg of the
  # resource evidence it is judged on (and vice versa).
  pathway_sets <- safe_slot(kg, "geneSets", safe_slot(ek, "geneSets", list()))
  audit <- assess_kegg_resource(
    identity = identity,
    retrieval_success = (!ora_attempted || ora_success) && (!gsea_attempted || gsea_success),
    retrieval_error = retrieval_error,
    supplied_universe = background,
    effective_universe = safe_slot(ek, "universe", character(0)),
    pathway_sets = pathway_sets,
    foregrounds = foregrounds,
    ora_hypotheses_n = raw_result_count(ek),
    ora_adjusted_n = nrows(ek),
    gsea_adjusted_n = nrows(kg),
    ora_attempted = ora_attempted, ora_success = ora_success, ora_error = ek_error,
    gsea_attempted = gsea_attempted, gsea_success = gsea_success, gsea_error = kg_error,
    ranked_ids = names(ranked))
  audit$key_form <- kegg_keytype
  audit$kegg_key_form_observed <- key_form_observed
  audit$query_date_utc <- kegg_query_date_utc()
  audit$keggrest_version <- kegg_package_version("KEGGREST")
  audit$clusterprofiler_version <- kegg_package_version("clusterProfiler")
  if (audit$ora_status %in% KEGG_VALID_STATUSES && nrows(ek) > 0) {
    write.csv(as.data.frame(ek), out[["kegg"]], row.names = FALSE)
  }
  if (audit$gsea_status %in% KEGG_VALID_STATUSES && nrows(kg) > 0) {
    write.csv(as.data.frame(kg), out[["kegg_gsea"]], row.names = FALSE)
  }
  list(ekegg_all = ek, kegg_gse = kg, n_ora = nrows(ek), n_gsea = nrows(kg),
       audit = audit)
}

# GO-route selection. clusterProfiler KEGG is always-on (the proven path) and is the
# SOLE source of ekegg_all/kegg_gse for EVERY route, so the figures rule keeps rendering
# the KEGG S4 plots regardless of backend. The GO route is chosen as:
#   1. OrgDb   — backend != "gprofiler" AND OrgDb loads AND bitr maps > 0 ids
#   2. gProf   — else if backend == "gprofiler" OR a g:Profiler organism is set
#   3. none    — KEGG-only (e.g. Fusarium graminearum: g:Profiler rejects FGSG_ ids)
# Routes 1/3 set gprofiler_table = NULL; route 2 sets gse/kegg via clusterProfiler still.

# Probe the OrgDb: load the package and map the result ids. On any failure (package
# not installed, ~0 ids mapped) orgdb_ok stays FALSE so the run falls through to the
# g:Profiler or KEGG-only route instead of aborting (W1 load-bearing fix).
orgdb_ok <- FALSE
orgdb_probe <- NULL
if (has_orgdb && !identical(backend, "gprofiler")) {
  orgdb_probe <- tryCatch({
    suppressMessages({
      library(clusterProfiler)
      library(orgdb_name, character.only = TRUE)
    })
    orgdb <- get(orgdb_name)
    full_res <- read.csv(results_file, stringsAsFactors = FALSE)
    ora_mask <- !is.na(full_res$padj)
    res <- full_res[ora_mask, , drop = FALSE]
    ids <- strip_version(res$gene_id)
    mapping <- map_ids_with_routing(ids, orgdb, keytype, orgdb_name)
    map <- mapping$map
    res$base_id <- ids
    res$ENTREZID <- map$ENTREZID[match(ids, map$input_id)]
    res$mapping_keytype <- map$keytype[match(ids, map$input_id)]
    res <- res[!is.na(res$ENTREZID) & nzchar(res$ENTREZID), , drop = FALSE]
    candidate_res <- full_res[additional_gsea_candidate_mask(full_res), , drop = FALSE]
    candidate_ids <- strip_version(candidate_res$gene_id)
    candidate_mapping <- map_ids_with_routing(
      candidate_ids, orgdb, keytype, orgdb_name)
    candidate_map <- candidate_mapping$map
    candidate_res$base_id <- candidate_ids
    candidate_res$ENTREZID <- candidate_map$ENTREZID[
      match(candidate_ids, candidate_map$input_id)]
    candidate_res <- candidate_res[
      !is.na(candidate_res$ENTREZID) & nzchar(candidate_res$ENTREZID), , drop = FALSE]
    prepared <- prepare_mapped_enrichment_populations(
      full_res, ora_mask, res, candidate_res)
    rank_column <- prepared$rank_statistic
    populations <- prepared$populations
    rank_res <- populations$rank
    rank_ids <- strip_version(rank_res$gene_id)
    rank_mapping <- map_ids_with_routing(rank_ids, orgdb, keytype, orgdb_name)
    list(orgdb = orgdb, res = res, ids = ids, mapping = mapping,
         rank_res = rank_res, rank_ids = rank_ids, rank_mapping = rank_mapping,
         rank_column = rank_column,
         populations = populations, n_in = mapping$total_inputs,
         n_mapped = mapping$mapped_inputs + rank_mapping$mapped_inputs)
  }, error = function(e) {
    message("OrgDb route unavailable (", orgdb_name, "): ", conditionMessage(e))
    NULL
  })
  if (!is.null(orgdb_probe) && orgdb_probe$n_mapped > 0) {
    orgdb_ok <- TRUE
  } else if (!is.null(orgdb_probe)) {
    # OrgDb loaded but ~0 ids mapped: do not silently run an empty GO route. Fall
    # through to KEGG/g:Profiler; the ID-conversion message is recorded if no route hits.
    message(sprintf("OrgDb bitr mapped %d/%d ids for keytype %s; falling through.",
                    orgdb_probe$n_mapped, orgdb_probe$n_in, keytype))
  }
}

if (orgdb_ok) {
  result <- tryCatch({
    orgdb <- orgdb_probe$orgdb
    res <- orgdb_probe$res
    ids <- orgdb_probe$ids
    mapping <- orgdb_probe$mapping
    map <- mapping$map
    rank_res <- orgdb_probe$rank_res
    rank_map <- orgdb_probe$rank_mapping$map
    rank_res$base_id <- orgdb_probe$rank_ids
    rank_res$ENTREZID <- rank_map$ENTREZID[match(rank_res$base_id, rank_map$input_id)]
    rank_res <- rank_res[
      !is.na(rank_res$ENTREZID) & nzchar(rank_res$ENTREZID), , drop = FALSE]

    # The up/down CSVs declare which source rows passed the DE cutoffs. Annotate
    # those source rows, collapse the complete accepted DE table exactly once in
    # Entrez space, and only then derive foregrounds and the tested universe.
    up_input <- read_ids_csv(up_file)
    down_input <- read_ids_csv(down_file)
    sig_input <- unique(c(up_input, down_input))
    rank_column <- orgdb_probe$rank_column
    collapsed <- collapse_entrez_results(res, up_input, down_input, rank_column)
    res <- collapsed$table
    conflict_entrez <- mapped_unique(collapsed$conflicts$ENTREZID)
    up_e <- mapped_unique(res$ENTREZID[res$direction == "up"])
    down_e <- mapped_unique(res$ENTREZID[res$direction == "down"])
    foreground_overlap <- intersect(up_e, down_e)
    if (length(foreground_overlap)) {
      # Defensive fail-closed branch: the collapse normally makes this impossible.
      # Remove any overlapping genes from both foreground and universe and retain a
      # REVIEW_REQUIRED gate rather than allowing contradictory ORA inputs.
      res <- res[!res$ENTREZID %in% foreground_overlap, , drop = FALSE]
      up_e <- setdiff(up_e, foreground_overlap)
      down_e <- setdiff(down_e, foreground_overlap)
    }
    stopifnot(length(intersect(up_e, down_e)) == 0L)
    universe <- mapped_unique(res$ENTREZID)
    all_sig <- unique(c(up_e, down_e))
    final_map <- map[map$ENTREZID %in% universe, , drop = FALSE]

    mapping_stats_for_ids <- function(ids_in) {
      ids_in <- unique(as.character(ids_in[!is.na(ids_in) & nzchar(as.character(ids_in))]))
      mapped <- final_map$ENTREZID[match(ids_in, final_map$input_id)]
      mapped_ok <- !is.na(mapped) & nzchar(mapped)
      list(total = length(ids_in), retained = sum(mapped_ok),
           unique_entrez = length(unique(mapped[mapped_ok])))
    }
    tested_retained_n <- length(unique(final_map$input_id))
    up_mapping <- mapping_stats_for_ids(up_input)
    down_mapping <- mapping_stats_for_ids(down_input)
    sig_mapping <- mapping_stats_for_ids(sig_input)
    tested_fraction <- mapping_fraction(tested_retained_n, mapping$total_inputs)
    significant_fraction <- mapping_fraction(sig_mapping$retained, sig_mapping$total)
    coverage_status <- mapping_gate(tested_fraction, significant_fraction)
    direction_status <- direction_gate(
      length(collapsed$source_overlap), nrow(collapsed$conflicts),
      length(foreground_overlap))
    direction_conflict_inputs <- length(unique(
      map$input_id[map$ENTREZID %in% unique(c(conflict_entrez, foreground_overlap))]))

    # Backfill display symbols from the OrgDb when the DE table lacked them. The
    # stable source-row representative is used only by the GUI id bridge; it does
    # not choose or weight any analytical mapping.
    if (is.null(res$symbol)) res$symbol <- rep(NA_character_, nrow(res))
    res$symbol <- tryCatch({
      need <- is.na(res$symbol) | !nzchar(res$symbol)
      if (any(need) && go_readable_for_orgdb(orgdb)) {
        sym <- suppressWarnings(suppressMessages(AnnotationDbi::select(
          orgdb, keys = as.character(res$ENTREZID), columns = "SYMBOL",
          keytype = "ENTREZID")))
        sym <- sym[!is.na(sym$ENTREZID) & !is.na(sym$SYMBOL) & nzchar(sym$SYMBOL), , drop = FALSE]
        by_entrez <- tapply(sym$SYMBOL, as.character(sym$ENTREZID),
                           function(values) paste(sort(unique(values)), collapse = ";"))
        res$symbol[need] <- unname(by_entrez[as.character(res$ENTREZID[need])])
      }
      res$symbol
    }, error = function(e) res$symbol)
    go_readable <- go_readable_for_orgdb(orgdb)
    run_ora <- function(genes, path, ont = "BP") {
      genes <- mapped_unique(genes)
      if (length(genes) < 1) return(NULL)
      # Both the tested universe and every multiple-testing/cutoff choice are
      # explicit so the saved evidence describes the exact inferential procedure.
      ego <- tryCatch(enrichGO(gene = genes, universe = universe, OrgDb = orgdb,
                      keyType = "ENTREZID", ont = ont, pAdjustMethod = "BH",
                      pvalueCutoff = alpha, qvalueCutoff = 0.20,
                      minGSSize = 10, maxGSSize = 500, readable = go_readable),
                      error = function(e) NULL)
      if (!is.null(ego) && nrow(as.data.frame(ego)) > 0) {
        write.csv(as.data.frame(ego), path, row.names = FALSE)
      }
      ego  # return the enrichResult (or NULL) so it can be persisted for figures
    }
    # Write a per-ontology ORA CSV (header-only when the enrichResult is a valid 0-row
    # object; the pre-created empty file stays when ego is NULL). Never throws.
    write_ont_csv <- function(ego, path) {
      if (is.null(ego)) return(invisible())
      tryCatch(write.csv(as.data.frame(ego), path, row.names = FALSE),
               error = function(e) NULL)
    }

    ego_all <- run_ora(all_sig, out[["go"]])
    ego_up <- run_ora(up_e, out[["go_up"]])
    ego_down <- run_ora(down_e, out[["go_down"]])
    n_all <- nrows(ego_all); n_up <- nrows(ego_up); n_down <- nrows(ego_down)

    # Per-ontology GO ORA trio on the combined significant set: BP (reuses ego_all),
    # plus MF and CC. Written to the uniform go_ora_<ONT>.csv paths, leaving the
    # existing go_ora_all.csv (from run_ora above) untouched.
    ego_mf <- run_ora(all_sig, out[["go_mf"]], ont = "MF")
    ego_cc <- run_ora(all_sig, out[["go_cc"]], ont = "CC")
    write_ont_csv(ego_all, out[["go_bp"]])
    write_ont_csv(ego_mf, out[["go_mf"]])
    write_ont_csv(ego_cc, out[["go_cc"]])

    # ORA and GSEA use separate eligible populations. Both apply the selected
    # statistic and the same direction-conflict rules, with GSEA independently
    # collapsing its complete mapped ranked population in Entrez space.
    rank_info <- build_mapped_gsea_rank(
      rank_res, rank_column, up_input, down_input,
      unique(c(conflict_entrez, foreground_overlap)))
    gene_list <- rank_info$values
    rank_only_map <- rank_info$mapped_table[
      !rank_info$mapped_table$ENTREZID %in% res$ENTREZID, , drop = FALSE]
    write_id_map(rbind(res, rank_only_map))
    set.seed(42)
    # Gene-set size limits and BH correction are gseGO's defaults, stated explicitly.
    gse <- tryCatch(
      with_deterministic_gsea_ties(
        gseGO(geneList = gene_list, OrgDb = orgdb, ont = "BP", keyType = "ENTREZID",
              pvalueCutoff = alpha, pAdjustMethod = "BH", minGSSize = 10, maxGSSize = 500,
              eps = 0, seed = TRUE, verbose = FALSE),
        rank_info),
      error = function(e) NULL)
    n_gsea <- nrows(gse)
    if (n_gsea > 0) write.csv(as.data.frame(gse), out[["gsea"]], row.names = FALSE)

    # Disease-ontology ORA (human/mouse only). DOSE::enrichDO uses ont="HDO" and
    # organism in {hsa, mm}, and THROWS for any other organism and on the first-run
    # HDO.sqlite fetch. It MUST have its OWN tryCatch: the saveRDS below is inside
    # the outer tryCatch, so an uncaught enrichDO error would wipe ALL persisted
    # enrichment objects and figures.
    do_org <- if (grepl("org.Hs", orgdb_name)) "hsa" else if (grepl("org.Mm", orgdb_name)) "mm" else NA_character_
    ego_do <- tryCatch(
      if (is.na(do_org)) NULL else DOSE::enrichDO(gene = all_sig, ont = "HDO", organism = do_org,
                         universe = universe, pAdjustMethod = "BH",
                         pvalueCutoff = alpha, qvalueCutoff = 0.20,
                         minGSSize = 10, maxGSSize = 500),
      error = function(e) { message("enrichDO skipped: ", conditionMessage(e)); NULL })
    n_do <- nrows(ego_do)

    # KEGG ORA + GSEA on the ENTREZ ids (KEGG uses NCBI GeneIDs for OrgDb species).
    # `universe` (not names(gene_list)): gene_list drops genes with NA log2FoldChange,
    # so it is a strict subset. Passing it would give KEGG a different background from
    # enrichGO/enrichDO -- the same inconsistency this fix removes, one layer down.
    expected_identity <- orgdb_identity(orgdb)
    expected_kegg_name <- if (nzchar(configured_organism_name)) configured_organism_name else
      expected_identity$name
    # Use the catalog's species-level taxon. OrgDb metadata can identify a strain
    # (e.g. S. cerevisiae S288C 559292) while KEGG's registry correctly identifies
    # the species (4932); comparing those as peers creates a false mismatch.
    expected_kegg_taxon <- trimws(as.character(configured_taxon_id))
    if (!nzchar(expected_kegg_taxon) || is.na(expected_kegg_taxon)) {
      expected_kegg_taxon <- NA_character_
    }
    kegg <- if (has_kegg) run_kegg(
      all_sig, gene_list, "ncbi-geneid", background = universe, rank_info = rank_info,
      foregrounds = list(up = up_e, down = down_e, combined = all_sig),
      expected_name = expected_kegg_name, expected_taxon = expected_kegg_taxon)
    else list(ekegg_all = NULL, kegg_gse = NULL, n_ora = 0L, n_gsea = 0L,
              audit = list(status = "NOT_RUN", ora_status = "NOT_RUN",
                           gsea_status = "NOT_RUN"))
    go_bp_universe_n <- effective_ora_universe_n(ego_all)
    go_mf_universe_n <- effective_ora_universe_n(ego_mf)
    go_cc_universe_n <- effective_ora_universe_n(ego_cc)
    do_universe_n <- effective_ora_universe_n(ego_do)
    annotation_fractions <- c(
      GO_BP = mapping_fraction(go_bp_universe_n, length(universe)),
      GO_MF = mapping_fraction(go_mf_universe_n, length(universe)),
      GO_CC = mapping_fraction(go_cc_universe_n, length(universe)),
      DO = mapping_fraction(do_universe_n, length(universe)))
    annotation_status <- go_annotation_status(
      list(BP = ego_all, MF = ego_mf, CC = ego_cc), annotation_fractions)
    annotation_check_status <- resource_status_to_check(annotation_status)
    kegg_check_status <- if (has_kegg) resource_status_to_check(kegg$audit$status) else "PASS"

    # Persist the enrichment objects (+ ranked geneList and OrgDb name) so the
    # enrichment_figures rule can render dotplot/GSEA/network plots without re-running.
    # backend/gprofiler_table let the figures rule switch on obj$backend uniformly.
    saveRDS(list(ego_all = ego_all, ego_up = ego_up, ego_down = ego_down,
                 ego_mf = ego_mf, ego_cc = ego_cc,
                 gse = gse, ego_do = ego_do,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, rank_info = rank_info, orgdb = orgdb_name,
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "clusterprofiler", gprofiler_table = NULL),
            out[["objects"]])

    routed_one_to_many <- sum(mapping$exclusions$reason == "routed_one_to_many")
    unresolved_cross_keytype <- sum(mapping$exclusions$reason == "unresolved_cross_keytype")
    configured_route <- if (!identical(mapping$requested_keytype, mapping$effective_keytype))
      sprintf("%s (effective OrgDb keytype %s)", mapping$requested_keytype,
              mapping$effective_keytype) else mapping$effective_keytype
    route_counts <- sort(table(mapping$map$keytype), decreasing = TRUE)
    route_summary <- if (length(route_counts)) paste(
      sprintf("%s=%d", names(route_counts), as.integer(route_counts)), collapse = "; ") else "none"
    summary_lines <<- c(summary_lines,
      sprintf("Eligible ID mapping keytypes: %s",
              if (length(mapping$eligible_keytypes)) paste(mapping$eligible_keytypes, collapse = ", ") else "none"),
      sprintf(paste0("Identifier routing policy: AGI locus IDs -> TAIR; Ensembl gene IDs -> ENSEMBL; ",
                     "all other IDs -> configured %s; fallback accepted only when eligible keytypes agree."),
              configured_route),
      sprintf("Accepted ID mapping routes: %s", route_summary),
      sprintf("Tested input IDs retained after mapping/exclusion: %d/%d (%s)", tested_retained_n,
              mapping$total_inputs, mapping_percent(tested_fraction)),
      sprintf("Significant input IDs retained after mapping/exclusion: %d/%d (%s)",
              sig_mapping$retained, sig_mapping$total, mapping_percent(significant_fraction)),
      sprintf("Up-regulated input IDs retained after mapping/exclusion: %d/%d; unique Entrez IDs: %d",
              up_mapping$retained, up_mapping$total, up_mapping$unique_entrez),
      sprintf("Down-regulated input IDs retained after mapping/exclusion: %d/%d; unique Entrez IDs: %d",
              down_mapping$retained, down_mapping$total, down_mapping$unique_entrez),
      sprintf("Mapped tested-gene universe (unique Entrez IDs): %d", length(universe)),
      sprintf("GO effective annotated ORA universes: BP %s; MF %s; CC %s",
              format_annotation_coverage(go_bp_universe_n, length(universe)),
              format_annotation_coverage(go_mf_universe_n, length(universe)),
              format_annotation_coverage(go_cc_universe_n, length(universe))),
      sprintf("GO readable-symbol conversion: %s (%s)",
              if (go_readable) "enabled" else "disabled",
              if (go_readable) "OrgDb supplies SYMBOL" else
                "OrgDb has no SYMBOL column; Entrez identifiers retained"),
      sprintf("DO effective annotated ORA universe: %s",
              format_annotation_coverage(do_universe_n, length(universe))),
      sprintf("OrgDb annotation identity: organism=%s; taxon=%s (annotation package; may be strain-specific)",
              if (nzchar(expected_identity$name)) expected_identity$name else "not recorded",
              if (nzchar(expected_identity$taxon)) expected_identity$taxon else "not recorded"),
      if (has_kegg) kegg_evidence_lines(kegg) else
        "KEGG resource status: NOT_RUN; no KEGG organism code was configured.",
      sprintf("Unmapped input IDs excluded: %d", mapping$unmapped_inputs),
      sprintf(paste0("Ambiguous input IDs excluded: %d (routed one-to-many: %d; ",
                     "unresolved cross-keytype: %d)"),
              mapping$ambiguous_excluded, routed_one_to_many, unresolved_cross_keytype),
      sprintf("One-to-many mappings observed: %d; unresolved routed inputs excluded: %d",
              mapping$one_to_many_observed, routed_one_to_many),
      sprintf(paste0("Cross-keytype discordance observed: %d; resolved by explicit route: %d; ",
                     "unresolved inputs excluded: %d"),
              mapping$cross_discordance_observed, mapping$cross_discordance_resolved,
              unresolved_cross_keytype),
      sprintf("Many-to-one Entrez groups collapsed by median effect/statistic: %d (source rows removed: %d)",
              collapsed$many_to_one_groups, collapsed$duplicate_rows_collapsed),
      sprintf("Direction-conflict Entrez IDs excluded: %d; input IDs excluded: %d",
              length(conflict_entrez), direction_conflict_inputs),
      sprintf("Source IDs present in both up/down inputs: %d", length(collapsed$source_overlap)),
      sprintf("Foreground intersection (up/down Entrez) after exclusion: %d",
              length(intersect(up_e, down_e))),
      sprintf("Mapping interpretation gate: %s (WARNING below %.0f%%; REVIEW_REQUIRED below %.0f%%)",
              coverage_status, 100 * MAPPING_WARNING_FRACTION, 100 * MAPPING_REVIEW_FRACTION),
      sprintf("Direction-conflict gate: %s (any source overlap, conflicting Entrez group, or foreground overlap requires review)",
              direction_status),
      sprintf(paste0("GO/DO annotation-resource status: %s (coverage below %.0f%% is LIMITED_ANNOTATION; ",
                     "zero or malformed resource universes are NOT_INTERPRETABLE; this is separate from global ID mapping)"),
              annotation_status, 100 * ANNOTATION_WARNING_FRACTION),
      "Universe policy: all and only unambiguously mapped, direction-conflict-free tested Entrez genes; the same explicit universe is supplied to GO, DO, and KEGG ORA when run.",
      sprintf("ORA parameters: Benjamini-Hochberg (BH); pvalueCutoff=%s; qvalueCutoff=0.20; gene-set size 10-500; explicit tested-gene universe.",
              format(alpha, scientific = FALSE, trim = TRUE)),
      "ORA multiple-testing families: up, down, and combined queries are BH-corrected separately; their term counts must not be summed or interpreted as one experiment-wide FDR family.",
      sprintf(paste0("GSEA parameters: complete mapped, direction-conflict-free ranked Entrez list ",
                     "ranked on %s; Benjamini-Hochberg (BH); pvalueCutoff=%s; gene-set size 10-500; seed=42."),
              rank_info$statistic_name, format(alpha, scientific = FALSE, trim = TRUE)),
      enrichment_eligibility_lines(orgdb_probe$populations, "GSEA"),
      rank_evidence_lines(rank_info),
      "Mapping limitation: enrichment tests only the retained mapped subset; incomplete, ambiguous, or non-random identifier mapping can bias terms and pathways, so coverage and exclusions must accompany interpretation.",
      sprintf("Up-regulated: %d genes, %d GO BP terms (ORA)", length(up_e), n_up),
      sprintf("Down-regulated: %d genes, %d GO BP terms (ORA)", length(down_e), n_down),
      sprintf("Combined significant: %d genes, %d GO BP terms", length(all_sig), n_all),
      sprintf("GSEA GO BP gene sets meeting the adjusted criterion (directional, full ranked list): %d", n_gsea),
      sprintf("KEGG adjusted results meeting the criterion: %d (ORA), %d (GSEA)",
              kegg$n_ora, kegg$n_gsea))
    result_status <- status_max(
      if (length(all_sig) >= MIN_ORA_FOREGROUND_GENES) "PASS" else "REVIEW_REQUIRED",
      coverage_status, direction_status, annotation_check_status, kegg_check_status)
    list(status = result_status,
         message = sprintf(paste0(
           "Enrichment: GO up=%d, down=%d, combined=%d terms, GSEA=%d; KEGG ORA=%d, GSEA=%d. ",
           "Identifier mapping retained tested %d/%d (%s), significant %d/%d (%s); ambiguous excluded=%d, ",
           "direction-conflict Entrez excluded=%d, final up/down intersection=%d; mapped universe=%d; ",
           "gates mapping=%s, direction=%s, GO/DO-resource=%s, KEGG-resource=%s."),
           n_up, n_down, n_all, n_gsea, kegg$n_ora, kegg$n_gsea,
           tested_retained_n, mapping$total_inputs, mapping_percent(tested_fraction),
           sig_mapping$retained, sig_mapping$total, mapping_percent(significant_fraction),
           mapping$ambiguous_excluded, length(conflict_entrez), length(intersect(up_e, down_e)),
           length(universe), coverage_status, direction_status,
           annotation_status, kegg$audit$status))
  }, error = function(e) {
    summary_lines <<- c(summary_lines, paste("Enrichment failed:", conditionMessage(e)))
    list(status = "REVIEW_REQUIRED",
         message = paste("Enrichment could not run:", conditionMessage(e)))
  })
} else if (identical(backend, "gprofiler") || has_gprofiler) {
  # g:Profiler GO route: no usable OrgDb but a g:Profiler organism is set (or the
  # user forced backend="gprofiler"). gost provides GO:BP ORA only; clusterProfiler
  # enrichKEGG/gseKEGG below remains the sole source of the KEGG S4 objects so the
  # figures rule renders the KEGG dotplot/GSEA unchanged. gost is ORA-only -> GSEA
  # keys (gse) stay NULL. Figures labelled by term Description, never raw ids.
  result <- tryCatch({
    suppressMessages({ library(clusterProfiler) })
    full_res <- read.csv(results_file, stringsAsFactors = FALSE)
    ora_mask <- !is.na(full_res$padj) & !is.na(full_res$log2FoldChange)
    prepared <- prepare_main_enrichment_populations(
      full_res, ora_mask, !is.na(full_res$log2FoldChange))
    populations <- prepared$populations
    rank_stat <- list(name = prepared$rank_statistic)
    res <- populations$ora
    full_res$base_id <- strip_version(full_res$gene_id)
    res$base_id <- strip_version(res$gene_id)
    write_id_map(res)  # no entrez on this route; symbol/gene_id still bridge term extraction
    rank_info <- build_population_rank(populations, full_res$base_id, rank_stat$name)
    gene_list <- rank_info$values
    tested_genes <- unique(res$base_id)  # tested-gene background for gost custom_bg

    up_ids <- read_ids_csv(up_file)
    down_ids <- read_ids_csv(down_file)
    all_ids <- unique(c(up_ids, down_ids))

    additional_rank_res <- full_res[populations$additional_rank_mask, , drop = FALSE]
    ora_kegg_geneid_lookup <- kegg_geneid_lookup_for(
      kegg_keytype, KEGG_KEY_FORM_OBSERVED, res$base_id, res$ncbi_geneid)
    additional_kegg_geneid_lookup <- kegg_geneid_lookup_for(
      kegg_keytype, KEGG_KEY_FORM_OBSERVED, additional_rank_res$base_id,
      additional_rank_res$ncbi_geneid)
    kegg_geneid_lookup <- c(
      ora_kegg_geneid_lookup,
      additional_kegg_geneid_lookup[
        !names(additional_kegg_geneid_lookup) %in% names(ora_kegg_geneid_lookup)])
    kegg_bridged_n <- if (is.null(ora_kegg_geneid_lookup)) 0L else
      sum(!grepl("^[0-9]+$", all_ids) &
          !is.na(ora_kegg_geneid_lookup[all_ids]) &
          nzchar(ora_kegg_geneid_lookup[all_ids]))

    # gprofiler2 is a Stage-2 env addition and may be absent: wrap the load + gost
    # so a missing package or a network failure degrades to KEGG-only, never crashes.
    gp <- tryCatch({
      suppressMessages(library(gprofiler2))
      query <- all_ids
      gg <- gost(query = query, organism = gprofiler_org,
                 sources = c("GO:BP", "KEGG", "REAC"),
                 custom_bg = tested_genes, significant = TRUE,
                 user_threshold = alpha, correction_method = "g_SCS")
      # On a namespace mismatch (gost returns nothing because g:Profiler did not
      # recognise the query ids), retry once after gconvert maps the query into the
      # g:Profiler internal namespace.
      if (is.null(gg$result) || nrow(gg$result) == 0) {
        conv <- tryCatch(gconvert(query = query, organism = gprofiler_org),
                         error = function(e) NULL)
        if (!is.null(conv) && nrow(conv) > 0) {
          q2 <- unique(conv$target[!is.na(conv$target)])
          if (length(q2) > 0)
            gg <- gost(query = q2, organism = gprofiler_org,
                       sources = c("GO:BP", "KEGG", "REAC"),
                       custom_bg = tested_genes, significant = TRUE,
                       user_threshold = alpha, correction_method = "g_SCS")
        }
      }
      gg
    }, error = function(e) {
      message("g:Profiler gost unavailable: ", conditionMessage(e)); NULL
    })

    gprofiler_table <- if (!is.null(gp) && !is.null(gp$result) && nrow(gp$result) > 0)
                         gp$result else NULL
    # GO:BP ORA rows -> go_ora.csv. The gost result uses `term_name` as the term
    # Description; keep that column so downstream figures label by name, not GO id.
    n_go <- 0
    if (!is.null(gprofiler_table)) {
      go_rows <- gprofiler_table[gprofiler_table$source == "GO:BP", , drop = FALSE]
      n_go <- nrow(go_rows)
      # gost results carry list-columns (e.g. `parents`) that write.csv cannot
      # serialize ("unimplemented type 'list' in 'EncodeElement'"); the error would
      # otherwise abort the whole route, including the always-on KEGG block below.
      # Keep only atomic columns for the CSV (the full table is kept in the RDS).
      if (n_go > 0) {
        # gost returns source-grouped order; the report and its "top by adjusted p"
        # caption take file order, so sort here. gost's `p_value` IS the g:SCS-adjusted
        # value. term_id is a deterministic secondary key so exact ties are stable.
        if ("p_value" %in% names(go_rows)) {
          key <- if ("term_id" %in% names(go_rows)) as.character(go_rows$term_id) else
            rownames(go_rows)
          go_rows <- go_rows[order(suppressWarnings(as.numeric(go_rows$p_value)), key,
                                   method = "radix"), , drop = FALSE]
        }
        atomic <- vapply(go_rows, is.atomic, logical(1))
        write.csv(go_rows[, atomic, drop = FALSE], out[["go"]], row.names = FALSE)
      }
    }

    # KEGG ORA + GSEA via clusterProfiler on the raw locus-tag ids (always-on tail).
    # Same tested-gene background g:Profiler receives as custom_bg, in locus-tag space.
    route_gap <- if (has_kegg) kegg_route_gap(
      KEGG_KEY_FORM_OBSERVED, all_ids, geneid_column_usable(res$ncbi_geneid),
      DE_ROUTE, kegg_keytype) else NULL
    kegg_rank_info <- build_bridged_population_rank(
      populations, full_res$base_id, kegg_geneid_lookup, rank_stat$name)
    kegg <- if (has_kegg) run_kegg(
      bridge_kegg_geneid(all_ids, ora_kegg_geneid_lookup), kegg_rank_info$values,
      kegg_keytype,
      background = bridge_kegg_geneid(tested_genes, ora_kegg_geneid_lookup),
      rank_info = kegg_rank_info,
      foregrounds = list(up = bridge_kegg_geneid(up_ids, ora_kegg_geneid_lookup),
                         down = bridge_kegg_geneid(down_ids, ora_kegg_geneid_lookup),
                         combined = bridge_kegg_geneid(all_ids, ora_kegg_geneid_lookup)),
      expected_name = configured_organism_name, key_form_observed = KEGG_KEY_FORM_EVIDENCE)
    else list(ekegg_all = NULL, kegg_gse = NULL, n_ora = 0L, n_gsea = 0L,
              audit = list(status = "NOT_RUN", ora_status = "NOT_RUN",
                           gsea_status = "NOT_RUN"))

    saveRDS(list(ego_all = NULL, ego_up = NULL, ego_down = NULL,
                 gse = NULL, ego_do = NULL,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = kegg_rank_info$values, rank_info = kegg_rank_info, orgdb = "",
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "gprofiler", gprofiler_table = gprofiler_table),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      sprintf("GO route: g:Profiler (organism %s).", gprofiler_org),
      sprintf("GO BP terms (gost ORA): %d", n_go),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      sprintf("Ranked genes (GSEA input): %d", length(kegg_rank_info$values)),
      enrichment_eligibility_lines(populations, "GSEA"),
      rank_evidence_lines(kegg_rank_info),
      if (has_kegg) kegg_evidence_lines(kegg) else
        "KEGG resource status: NOT_RUN; no KEGG organism code was configured.",
      sprintf("KEGG locus-tag-to-GeneID bridge: %d/%d significant ids resolved via GTF db_xref.",
              kegg_bridged_n, length(all_ids)),
      sprintf("KEGG adjusted results meeting the criterion: %d (ORA), %d (GSEA)",
              kegg$n_ora, kegg$n_gsea))
    gp_check_status <- if (is.null(gp)) "REVIEW_REQUIRED" else "PASS"
    kegg_check_status <- if (has_kegg) resource_status_to_check(kegg$audit$status) else "PASS"
    result_status <- status_max(
      if (length(all_ids) >= MIN_ORA_FOREGROUND_GENES) "PASS" else "REVIEW_REQUIRED",
      gp_check_status, kegg_check_status,
      if (is.null(route_gap)) "PASS" else "REVIEW_REQUIRED")
    list(status = result_status,
         message = sprintf(paste0("g:Profiler GO=%d adjusted terms; KEGG ORA=%d, GSEA=%d adjusted pathways; ",
                                  "KEGG resource=%s; GeneID-bridged=%d/%d.%s"),
                           n_go, kegg$n_ora, kegg$n_gsea, kegg$audit$status,
                           kegg_bridged_n, length(all_ids),
                           if (is.null(route_gap)) "" else paste0(" ", route_gap)))
  }, error = function(e) {
    summary_lines <<- c(summary_lines, paste("g:Profiler enrichment failed:", conditionMessage(e)))
    list(status = "REVIEW_REQUIRED",
         message = paste("g:Profiler enrichment could not run:", conditionMessage(e)))
  })
} else {
  # KEGG-only path: no OrgDb and no g:Profiler organism, but a KEGG code exists. KEGG
  # keys genes by their native locus-tag ids (e.g. FGSG_xxxxx for Fusarium), so the
  # deseq2 gene ids are passed straight through with keyType = "kegg".
  result <- tryCatch({
    suppressMessages({ library(clusterProfiler) })
    full_res <- read.csv(results_file, stringsAsFactors = FALSE)
    ora_mask <- !is.na(full_res$padj) & !is.na(full_res$log2FoldChange)
    prepared <- prepare_main_enrichment_populations(
      full_res, ora_mask, !is.na(full_res$log2FoldChange))
    populations <- prepared$populations
    rank_stat <- list(name = prepared$rank_statistic)
    res <- populations$ora
    full_res$base_id <- strip_version(full_res$gene_id)
    res$base_id <- strip_version(res$gene_id)
    write_id_map(res)  # no entrez on this route; symbol/gene_id still bridge term extraction
    rank_info <- build_population_rank(populations, full_res$base_id, rank_stat$name)
    gene_list <- rank_info$values

    tested_genes <- unique(res$base_id)  # tested-gene background for the KEGG ORA universe

    up_ids <- read_ids_csv(up_file)
    down_ids <- read_ids_csv(down_file)
    all_ids <- unique(c(up_ids, down_ids))

    additional_rank_res <- full_res[populations$additional_rank_mask, , drop = FALSE]
    ora_kegg_geneid_lookup <- kegg_geneid_lookup_for(
      kegg_keytype, KEGG_KEY_FORM_OBSERVED, res$base_id, res$ncbi_geneid)
    additional_kegg_geneid_lookup <- kegg_geneid_lookup_for(
      kegg_keytype, KEGG_KEY_FORM_OBSERVED, additional_rank_res$base_id,
      additional_rank_res$ncbi_geneid)
    kegg_geneid_lookup <- c(
      ora_kegg_geneid_lookup,
      additional_kegg_geneid_lookup[
        !names(additional_kegg_geneid_lookup) %in% names(ora_kegg_geneid_lookup)])
    kegg_bridged_n <- if (is.null(ora_kegg_geneid_lookup)) 0L else
      sum(!grepl("^[0-9]+$", all_ids) &
          !is.na(ora_kegg_geneid_lookup[all_ids]) &
          nzchar(ora_kegg_geneid_lookup[all_ids]))
    route_gap <- if (has_kegg) kegg_route_gap(
      KEGG_KEY_FORM_OBSERVED, all_ids, geneid_column_usable(res$ncbi_geneid),
      DE_ROUTE, kegg_keytype) else NULL
    kegg_rank_info <- build_bridged_population_rank(
      populations, full_res$base_id, kegg_geneid_lookup, rank_stat$name)

    kegg <- run_kegg(
      bridge_kegg_geneid(all_ids, ora_kegg_geneid_lookup), kegg_rank_info$values,
      kegg_keytype,
      background = bridge_kegg_geneid(tested_genes, ora_kegg_geneid_lookup),
      rank_info = kegg_rank_info,
      foregrounds = list(up = bridge_kegg_geneid(up_ids, ora_kegg_geneid_lookup),
                        down = bridge_kegg_geneid(down_ids, ora_kegg_geneid_lookup),
                        combined = bridge_kegg_geneid(all_ids, ora_kegg_geneid_lookup)),
      expected_name = configured_organism_name, key_form_observed = KEGG_KEY_FORM_EVIDENCE)

    saveRDS(list(ego_all = NULL, ego_up = NULL, ego_down = NULL,
                 gse = NULL, ego_do = NULL,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = kegg_rank_info$values, rank_info = kegg_rank_info, orgdb = "",
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "clusterprofiler", gprofiler_table = NULL),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      "GO/disease enrichment: skipped (no Bioconductor OrgDb for this organism).",
      sprintf("Ranked genes (GSEA input): %d", length(kegg_rank_info$values)),
      enrichment_eligibility_lines(populations, "GSEA"),
      rank_evidence_lines(kegg_rank_info),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      kegg_evidence_lines(kegg),
      sprintf("KEGG locus-tag-to-GeneID bridge: %d/%d significant ids resolved via GTF db_xref.",
              kegg_bridged_n, length(all_ids)),
      sprintf("KEGG adjusted results meeting the criterion: %d (ORA), %d (GSEA)",
              kegg$n_ora, kegg$n_gsea))
    list(status = status_max(
           if (length(all_ids) >= MIN_ORA_FOREGROUND_GENES) "PASS" else "REVIEW_REQUIRED",
           resource_status_to_check(kegg$audit$status),
           if (is.null(route_gap)) "PASS" else "REVIEW_REQUIRED"),
         message = sprintf(paste0("KEGG-only enrichment: ORA=%d, GSEA=%d adjusted pathways; ",
                                  "resource=%s; %s; GeneID-bridged=%d/%d%s"),
                           kegg$n_ora, kegg$n_gsea, kegg$audit$status,
                           kegg$audit$reason, kegg_bridged_n, length(all_ids),
                           if (is.null(route_gap)) "" else paste0(" ", route_gap)))
  }, error = function(e) {
    summary_lines <<- c(summary_lines, paste("KEGG enrichment failed:", conditionMessage(e)))
    list(status = "REVIEW_REQUIRED",
         message = paste("KEGG enrichment could not run:", conditionMessage(e)))
  })
}

writeLines(summary_lines, out[["summary"]])
write_check(out[["check"]], result$status, result$message)
sink(type = "message")
close(log_con)
