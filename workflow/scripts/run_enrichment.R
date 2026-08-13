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

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "10_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

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

# Build every GSEA input under one explicit ordering contract. Statistics must be
# finite and are ordered decreasingly. Exact-score ties use the canonical gene id:
# all-digit ids (Entrez) compare by exact numeric value, while all other id spaces
# compare bytewise after UTF-8 encoding (the C-locale/radix order). After invalid
# ids and non-finite scores are removed, repeated canonical ids are collapsed by
# their median score, matching the deterministic many-to-one reducer used above
# for OrgDb mappings and making the rank invariant to source-row order.
build_deterministic_rank <- function(statistic, canonical_id) {
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
  list(
    values = values,
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
    policy = paste0("invalid IDs and non-finite statistics removed; duplicate canonical IDs ",
                    "collapsed by median; finite statistic descending; exact ties by ",
                    id_policy)
  )
}

rank_evidence_lines <- function(rank_info) {
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
            rank_info$nonfinite_removed)
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

# Resolve mappings without choosing an arbitrary member of a one-to-many result.
# Exact identifier formats route to their matching namespace (currently AGI/TAIR
# and Ensembl gene ids); all other ids use the configured namespace declared by
# the project. If that route has no hit, a fallback is accepted only when every
# eligible keytype that maps the id agrees on one Entrez id. Unresolved one-to-many
# and cross-keytype-discordant ids are excluded from both foreground and universe.
merge_mapping_candidates <- function(ids, candidates, eligible_keytypes,
                                     configured_keytype) {
  ids <- unique(as.character(ids[!is.na(ids) & nzchar(as.character(ids))]))
  normalize <- function(mapped) {
    if (is.null(mapped) || !is.data.frame(mapped) ||
        !all(c("input_id", "ENTREZID") %in% names(mapped))) {
      return(data.frame(input_id = character(0), ENTREZID = character(0)))
    }
    mapped$input_id <- as.character(mapped$input_id)
    mapped$ENTREZID <- as.character(mapped$ENTREZID)
    unique(mapped[
      mapped$input_id %in% ids & !is.na(mapped$input_id) & nzchar(mapped$input_id) &
      !is.na(mapped$ENTREZID) & nzchar(mapped$ENTREZID),
      c("input_id", "ENTREZID"), drop = FALSE])
  }
  candidates <- lapply(candidates, normalize)
  route_for <- function(id) {
    upper <- toupper(id)
    if ("TAIR" %in% eligible_keytypes && grepl("^AT[1-5CM]G[0-9]{5}(\\.[0-9]+)?$", upper)) {
      return("TAIR")
    }
    if ("ENSEMBL" %in% eligible_keytypes && grepl("^ENS[A-Z]*G[0-9]+(\\.[0-9]+)?$", upper)) {
      return("ENSEMBL")
    }
    if (configured_keytype %in% eligible_keytypes) configured_keytype else NA_character_
  }

  accepted <- list()
  excluded <- list()
  one_to_many_observed <- 0L
  cross_discordance_observed <- 0L
  cross_discordance_resolved <- 0L
  for (id in ids) {
    by_key <- lapply(eligible_keytypes, function(key) {
      table <- candidates[[key]]
      unique(table$ENTREZID[table$input_id == id])
    })
    names(by_key) <- eligible_keytypes
    nonempty <- by_key[lengths(by_key) > 0L]
    all_hits <- unique(unlist(nonempty, use.names = FALSE))
    has_one_to_many <- any(lengths(nonempty) > 1L)
    has_cross_discordance <- length(nonempty) > 1L && length(all_hits) > 1L
    one_to_many_observed <- one_to_many_observed + as.integer(has_one_to_many)
    cross_discordance_observed <- cross_discordance_observed + as.integer(has_cross_discordance)
    route <- route_for(id)
    routed_hits <- if (!is.na(route) && route %in% names(by_key)) by_key[[route]] else character(0)

    resolution <- NULL
    if (length(routed_hits) == 1L) {
      resolution <- list(entrez = routed_hits[[1]], keytype = route,
                         method = paste0("routed:", route))
      if (has_cross_discordance) cross_discordance_resolved <- cross_discordance_resolved + 1L
    } else if (length(routed_hits) > 1L) {
      excluded[[length(excluded) + 1L]] <- data.frame(
        input_id = id, reason = "routed_one_to_many", routed_keytype = route,
        candidate_entrez = paste(sort(routed_hits), collapse = ";"))
      next
    } else if (length(all_hits) == 1L) {
      supporting <- names(nonempty)[vapply(nonempty, function(values) all_hits[[1]] %in% values,
                                           logical(1))]
      resolution <- list(entrez = all_hits[[1]], keytype = paste(supporting, collapse = "+"),
                         method = "eligible_keytypes_agree")
    } else if (length(all_hits) > 1L) {
      excluded[[length(excluded) + 1L]] <- data.frame(
        input_id = id, reason = "unresolved_cross_keytype", routed_keytype = route,
        candidate_entrez = paste(sort(all_hits), collapse = ";"))
      next
    } else {
      excluded[[length(excluded) + 1L]] <- data.frame(
        input_id = id, reason = "unmapped", routed_keytype = route,
        candidate_entrez = "")
      next
    }
    accepted[[length(accepted) + 1L]] <- data.frame(
      input_id = id, ENTREZID = as.character(resolution$entrez),
      keytype = resolution$keytype, resolution = resolution$method)
  }

  mapping <- if (length(accepted)) do.call(rbind, accepted) else
    data.frame(input_id = character(0), ENTREZID = character(0),
               keytype = character(0), resolution = character(0))
  exclusions <- if (length(excluded)) do.call(rbind, excluded) else
    data.frame(input_id = character(0), reason = character(0),
               routed_keytype = character(0), candidate_entrez = character(0))
  ambiguous_reasons <- c("routed_one_to_many", "unresolved_cross_keytype")
  list(
    map = mapping,
    exclusions = exclusions,
    eligible_keytypes = eligible_keytypes,
    configured_keytype = configured_keytype,
    total_inputs = length(ids),
    mapped_inputs = nrow(mapping),
    unmapped_inputs = sum(exclusions$reason == "unmapped"),
    ambiguous_excluded = sum(exclusions$reason %in% ambiguous_reasons),
    one_to_many_observed = one_to_many_observed,
    cross_discordance_observed = cross_discordance_observed,
    cross_discordance_resolved = cross_discordance_resolved,
    duplicate_entrez = sum(duplicated(mapping$ENTREZID))
  )
}

resolve_configured_keytype <- function(configured_keytype, supported, orgdb_package = "") {
  configured_keytype <- as.character(configured_keytype)
  if (configured_keytype %in% supported) return(configured_keytype)
  # org.Sc.sgd.db exposes the official yeast gene-name namespace as GENENAME
  # (and older releases may expose only COMMON), not SYMBOL. This translation is
  # semantic, not a permissive alias fallback: exact-name hits remain the routed
  # authority and unresolved one-to-many mappings are still excluded below.
  if (identical(configured_keytype, "SYMBOL") &&
      identical(as.character(orgdb_package), "org.Sc.sgd.db")) {
    equivalents <- c("GENENAME", "COMMON")
    available <- equivalents[equivalents %in% supported]
    if (length(available)) return(available[[1]])
  }
  configured_keytype
}


map_ids_with_routing <- function(ids, orgdb, configured_keytype, orgdb_package = "") {
  ids <- unique(as.character(ids[!is.na(ids) & nzchar(as.character(ids))]))
  supported <- AnnotationDbi::keytypes(orgdb)
  effective_keytype <- resolve_configured_keytype(
    configured_keytype, supported, orgdb_package)
  eligible <- unique(c(effective_keytype, configured_keytype, "TAIR", "ENSEMBL",
                       "ENTREZID", "SYMBOL", "ALIAS"))
  eligible <- eligible[!is.na(eligible) & nzchar(eligible) & eligible %in% supported]
  candidates <- list()
  for (candidate in eligible) {
    mapped <- if (identical(candidate, "ENTREZID")) {
      # bitr rejects fromType == toType. Confirm that numeric-looking inputs are
      # real keys in this OrgDb before accepting the identity mapping.
      valid <- tryCatch(AnnotationDbi::keys(orgdb, keytype = "ENTREZID"),
                        error = function(e) character(0))
      hits <- intersect(ids, as.character(valid))
      data.frame(input_id = hits, ENTREZID = hits, stringsAsFactors = FALSE)
    } else tryCatch(
      suppressWarnings(clusterProfiler::bitr(
        ids, fromType = candidate, toType = "ENTREZID", OrgDb = orgdb,
        drop = TRUE)),
      error = function(e) NULL)
    if (!is.null(mapped) && nrow(mapped) > 0 && "ENTREZID" %in% names(mapped)) {
      if (candidate %in% names(mapped)) names(mapped)[names(mapped) == candidate] <- "input_id"
      candidates[[candidate]] <- mapped[, c("input_id", "ENTREZID"), drop = FALSE]
    }
  }
  resolved <- merge_mapping_candidates(ids, candidates, eligible, effective_keytype)
  resolved$requested_keytype <- configured_keytype
  resolved$effective_keytype <- effective_keytype
  resolved
}

# Collapse the accepted source rows once, in Entrez space, before deriving any
# enrichment foreground or ranked list. Median effect/statistic summaries avoid
# privileging an arbitrary alias row. A mapped Entrez group is excluded entirely
# when its source rows imply both directions or when its collapsed effect has the
# opposite sign from the one declared direction. This keeps foreground and
# universe in the same conflict-free gene space.
collapse_entrez_results <- function(res, up_ids, down_ids) {
  required <- c("base_id", "ENTREZID", "log2FoldChange")
  if (!all(required %in% names(res))) {
    stop("Entrez collapse requires columns: ", paste(required, collapse = ", "))
  }
  empty_table <- data.frame(
    gene_id = character(0), base_id = character(0), source_ids = character(0),
    symbol = character(0), ENTREZID = character(0), log2FoldChange = numeric(0),
    stat = numeric(0), baseMean = numeric(0), direction = character(0),
    stringsAsFactors = FALSE)
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
    reason <- character(0)
    if ("conflict" %in% directions || all(c("up", "down") %in% nonneutral)) {
      reason <- c(reason, "opposed_source_directions")
    }
    declared <- if (identical(nonneutral, "up")) "up" else
                if (identical(nonneutral, "down")) "down" else "neutral"
    if (identical(declared, "up") && is.finite(lfc) && lfc <= 0) {
      reason <- c(reason, "collapsed_effect_not_positive")
    }
    if (identical(declared, "down") && is.finite(lfc) && lfc >= 0) {
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
      stat = if ("stat" %in% names(res)) finite_median(res$stat[idx]) else NA_real_,
      baseMean = if ("baseMean" %in% names(res)) finite_median(res$baseMean[idx]) else NA_real_,
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

assess_kegg_resource <- function(identity, retrieval_success, retrieval_error,
                                 supplied_universe, effective_universe, pathway_sets,
                                 foregrounds, ora_hypotheses_n, ora_adjusted_n,
                                 gsea_adjusted_n,
                                 min_size = KEGG_MIN_GENE_SET_SIZE,
                                 max_size = KEGG_MAX_GENE_SET_SIZE) {
  clean_ids <- function(values) mapped_unique(as.character(values))
  supplied <- clean_ids(supplied_universe)
  effective <- clean_ids(effective_universe)
  foregrounds <- lapply(foregrounds, clean_ids)
  for (name in c("up", "down", "combined")) {
    if (is.null(foregrounds[[name]])) foregrounds[[name]] <- character(0)
  }
  result <- list(
    status = "NOT_INTERPRETABLE", reason = "", identity = identity,
    retrieval_success = isTRUE(retrieval_success), retrieval_error = as.character(retrieval_error),
    supplied_universe_n = length(supplied), effective_universe_n = length(effective),
    effective_universe_fraction = mapping_fraction(length(effective), length(supplied)),
    pathway_collection_n = if (is.list(pathway_sets)) length(pathway_sets) else 0L,
    eligible_gene_sets_n = 0L, eligible_universe_n = 0L,
    supported_foreground = list(up = 0L, down = 0L, combined = 0L),
    foreground_total = lapply(foregrounds, length),
    ora_hypotheses_n = as.integer(ora_hypotheses_n),
    ora_adjusted_n = as.integer(ora_adjusted_n),
    gsea_adjusted_n = as.integer(gsea_adjusted_n),
    min_size = as.integer(min_size), max_size = as.integer(max_size))
  if (!identical(identity$status, "PASS")) {
    result$reason <- identity$reason
    return(result)
  }
  if (!isTRUE(retrieval_success)) {
    result$reason <- paste("KEGG retrieval failed:", retrieval_error)
    return(result)
  }
  if (!length(supplied) || !length(effective) || any(!effective %in% supplied)) {
    result$reason <- "KEGG effective universe is zero, malformed, or outside the supplied tested universe"
    return(result)
  }
  if (!is.list(pathway_sets) || !length(pathway_sets)) {
    result$reason <- "KEGG retrieval returned no pathway gene-set collection"
    return(result)
  }
  pathway_sets <- lapply(pathway_sets, clean_ids)
  effective_sets <- lapply(pathway_sets, function(values) intersect(values, effective))
  sizes <- lengths(effective_sets)
  eligible <- effective_sets[sizes >= min_size & sizes <= max_size]
  result$eligible_gene_sets_n <- length(eligible)
  eligible_ids <- clean_ids(unlist(eligible, use.names = FALSE))
  result$eligible_universe_n <- length(eligible_ids)
  if (!length(eligible) || !length(eligible_ids)) {
    result$reason <- sprintf("KEGG returned no eligible %d-%d gene pathway hypotheses",
                             min_size, max_size)
    return(result)
  }
  result$supported_foreground <- lapply(
    foregrounds, function(values) length(intersect(values, eligible_ids)))
  if (result$supported_foreground$combined < 1L) {
    result$reason <- "no combined foreground genes are supported by eligible KEGG pathways"
    return(result)
  }
  if (!is.finite(result$ora_hypotheses_n) || result$ora_hypotheses_n < 1L) {
    result$reason <- "KEGG produced no foreground-overlapping ORA hypotheses after size filtering"
    return(result)
  }
  support_fractions <- c(
    result$effective_universe_fraction,
    vapply(c("up", "down", "combined"), function(name) {
      total <- result$foreground_total[[name]]
      if (total > 0L) result$supported_foreground[[name]] / total else NA_real_
    }, numeric(1)))
  result$status <- if (any(support_fractions[is.finite(support_fractions)] <
                           ANNOTATION_WARNING_FRACTION)) "LIMITED_ANNOTATION" else "PASS"
  result$reason <- if (identical(result$status, "LIMITED_ANNOTATION"))
    "valid KEGG resource with limited universe or foreground annotation coverage" else
    "valid KEGG resource and supported foreground"
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
  interpretation <- if (audit$status %in% c("PASS", "LIMITED_ANNOTATION") &&
                        audit$ora_adjusted_n == 0L && audit$gsea_adjusted_n == 0L) {
    paste0("no supported KEGG pathways met the adjusted criterion; this is not evidence ",
           "that no pathway biology is present")
  } else if (audit$status %in% c("PASS", "LIMITED_ANNOTATION")) {
    sprintf("%d ORA and %d GSEA pathways met the adjusted criterion",
            audit$ora_adjusted_n, audit$gsea_adjusted_n)
  } else audit$reason
  c(
    sprintf(paste0("KEGG identity verification: %s; configured code=%s; registry code=%s; ",
                   "organism=%s; taxon=%s; expected organism=%s; expected taxon=%s; source=%s"),
            identity$status, identity$configured_code, identity$registry_code,
            identity$registry_name, identity$registry_taxon,
            identity$expected_name, identity$expected_taxon, identity$registry_source),
    sprintf("KEGG retrieval: %s; pathway collection=%d; detail=%s",
            if (audit$retrieval_success) "SUCCESS" else "FAILED",
            audit$pathway_collection_n,
            if (nzchar(audit$retrieval_error)) audit$retrieval_error else "none"),
    sprintf("KEGG effective resource universe: %s; eligible %d-%d pathway universe=%d",
            format_fraction_count(audit$effective_universe_n, audit$supplied_universe_n),
            audit$min_size, audit$max_size, audit$eligible_universe_n),
    sprintf("KEGG supported foreground: up %s; down %s; combined %s",
            format_fraction_count(audit$supported_foreground$up, audit$foreground_total$up),
            format_fraction_count(audit$supported_foreground$down, audit$foreground_total$down),
            format_fraction_count(audit$supported_foreground$combined, audit$foreground_total$combined)),
    sprintf(paste0("KEGG eligible hypotheses/gene sets: %d after %d-%d filter; ",
                   "foreground-overlapping ORA hypotheses adjusted=%d"),
            audit$eligible_gene_sets_n, audit$min_size, audit$max_size,
            audit$ora_hypotheses_n),
    sprintf("KEGG adjusted results: ORA=%d; GSEA=%d; BH pvalueCutoff=%s; qvalueCutoff=0.20",
            audit$ora_adjusted_n, audit$gsea_adjusted_n,
            format(alpha, scientific = FALSE, trim = TRUE)),
    sprintf("KEGG resource status: %s; %s", audit$status, interpretation))
}

# KEGG ORA (combined significant set) + GSEA (ranked list). The returned audit
# distinguishes a valid but sparsely annotated resource from an invalid or
# unverifiable resource, and preserves zero adjusted results as a negative result.
run_kegg <- function(genes_all, ranked, kegg_keytype, background = NULL,
                     foregrounds = list(up = character(0), down = character(0),
                                        combined = genes_all),
                     expected_name = configured_organism_name,
                     expected_taxon = configured_taxon_id,
                     rank_info = build_deterministic_rank(ranked, names(ranked))) {
  genes_all <- mapped_unique(genes_all)
  background <- mapped_unique(background)
  identity <- validate_kegg_identity(kegg_org, expected_name, expected_taxon)
  empty_return <- function(reason) {
    audit <- assess_kegg_resource(
      identity, FALSE, reason, background, character(0), list(), foregrounds,
      0L, 0L, 0L)
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
  ek_error <- ""
  ek <- if (length(genes_all) >= 1) tryCatch(
    do.call(enrichKEGG, kegg_args), error = function(e) {
      ek_error <<- conditionMessage(e); message("enrichKEGG failed: ", ek_error); NULL
    }) else { ek_error <- "combined foreground is empty"; NULL }
  kg <- NULL
  kg_error <- ""
  if (length(ranked) > 0) {
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
  retrieval_error <- paste(c(ek_error, kg_error)[nzchar(c(ek_error, kg_error))], collapse = "; ")
  pathway_sets <- safe_slot(kg, "geneSets", list())
  audit <- assess_kegg_resource(
    identity = identity,
    retrieval_success = !is.null(ek) && !is.null(kg) && !nzchar(retrieval_error),
    retrieval_error = retrieval_error,
    supplied_universe = background,
    effective_universe = safe_slot(ek, "universe", character(0)),
    pathway_sets = pathway_sets,
    foregrounds = foregrounds,
    ora_hypotheses_n = raw_result_count(ek),
    ora_adjusted_n = nrows(ek),
    gsea_adjusted_n = nrows(kg))
  if (audit$status %in% c("PASS", "LIMITED_ANNOTATION")) {
    if (nrows(ek) > 0) write.csv(as.data.frame(ek), out[["kegg"]], row.names = FALSE)
    if (nrows(kg) > 0) write.csv(as.data.frame(kg), out[["kegg_gsea"]], row.names = FALSE)
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
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj), ]
    ids <- strip_version(res$gene_id)
    mapping <- map_ids_with_routing(ids, orgdb, keytype, orgdb_name)
    list(orgdb = orgdb, res = res, ids = ids, mapping = mapping,
         n_in = mapping$total_inputs, n_mapped = mapping$mapped_inputs)
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
    res$base_id <- ids
    res$ENTREZID <- map$ENTREZID[match(ids, map$input_id)]
    res$mapping_keytype <- map$keytype[match(ids, map$input_id)]
    res <- res[!is.na(res$ENTREZID) & nzchar(res$ENTREZID), , drop = FALSE]

    # The up/down CSVs declare which source rows passed the DE cutoffs. Annotate
    # those source rows, collapse the complete accepted DE table exactly once in
    # Entrez space, and only then derive foregrounds and the tested universe.
    up_input <- read_ids_csv(up_file)
    down_input <- read_ids_csv(down_file)
    sig_input <- unique(c(up_input, down_input))
    collapsed <- collapse_entrez_results(res, up_input, down_input)
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
    write_id_map(res)  # entrez<->symbol/gene_id bridge for term-gene extraction
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

    rank_info <- build_deterministic_rank(res$log2FoldChange, res$ENTREZID)
    gene_list <- rank_info$values
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
              audit = list(status = "NOT_RUN"))
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
                 geneList = gene_list, orgdb = orgdb_name,
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
      sprintf("GSEA parameters: complete mapped, direction-conflict-free ranked Entrez list; Benjamini-Hochberg (BH); pvalueCutoff=%s; gene-set size 10-500; seed=42.",
              format(alpha, scientific = FALSE, trim = TRUE)),
      rank_evidence_lines(rank_info),
      "Mapping limitation: enrichment tests only the retained mapped subset; incomplete, ambiguous, or non-random identifier mapping can bias terms and pathways, so coverage and exclusions must accompany interpretation.",
      sprintf("Up-regulated: %d genes, %d GO BP terms (ORA)", length(up_e), n_up),
      sprintf("Down-regulated: %d genes, %d GO BP terms (ORA)", length(down_e), n_down),
      sprintf("Combined significant: %d genes, %d GO BP terms", length(all_sig), n_all),
      sprintf("GSEA GO BP gene sets meeting the adjusted criterion (directional, full ranked list): %d", n_gsea),
      sprintf("KEGG adjusted results meeting the criterion: %d (ORA), %d (GSEA)",
              kegg$n_ora, kegg$n_gsea))
    result_status <- status_max(
      if (length(all_sig) >= 5) "PASS" else "REVIEW_REQUIRED",
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
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj) & !is.na(res$log2FoldChange), ]
    res$base_id <- strip_version(res$gene_id)
    write_id_map(res)  # no entrez on this route; symbol/gene_id still bridge term extraction
    rank_info <- build_deterministic_rank(res$log2FoldChange, res$base_id)
    gene_list <- rank_info$values
    tested_genes <- unique(res$base_id)  # tested-gene background for gost custom_bg

    up_ids <- read_ids_csv(up_file)
    down_ids <- read_ids_csv(down_file)
    all_ids <- unique(c(up_ids, down_ids))

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
        atomic <- vapply(go_rows, is.atomic, logical(1))
        write.csv(go_rows[, atomic, drop = FALSE], out[["go"]], row.names = FALSE)
      }
    }

    # KEGG ORA + GSEA via clusterProfiler on the raw locus-tag ids (always-on tail).
    # Same tested-gene background g:Profiler receives as custom_bg, in locus-tag space.
    kegg <- if (has_kegg) run_kegg(
      all_ids, gene_list, "kegg", background = tested_genes, rank_info = rank_info,
      foregrounds = list(up = up_ids, down = down_ids, combined = all_ids),
      expected_name = configured_organism_name)
    else list(ekegg_all = NULL, kegg_gse = NULL, n_ora = 0L, n_gsea = 0L,
              audit = list(status = "NOT_RUN"))

    saveRDS(list(ego_all = NULL, ego_up = NULL, ego_down = NULL,
                 gse = NULL, ego_do = NULL,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, orgdb = "",
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "gprofiler", gprofiler_table = gprofiler_table),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      sprintf("GO route: g:Profiler (organism %s).", gprofiler_org),
      sprintf("GO BP terms (gost ORA): %d", n_go),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      rank_evidence_lines(rank_info),
      if (has_kegg) kegg_evidence_lines(kegg) else
        "KEGG resource status: NOT_RUN; no KEGG organism code was configured.",
      sprintf("KEGG adjusted results meeting the criterion: %d (ORA), %d (GSEA)",
              kegg$n_ora, kegg$n_gsea))
    gp_check_status <- if (is.null(gp)) "REVIEW_REQUIRED" else "PASS"
    kegg_check_status <- if (has_kegg) resource_status_to_check(kegg$audit$status) else "PASS"
    result_status <- status_max(gp_check_status, kegg_check_status)
    list(status = result_status,
         message = sprintf(paste0("g:Profiler GO=%d adjusted terms; KEGG ORA=%d, GSEA=%d adjusted pathways; ",
                                  "KEGG resource=%s."),
                           n_go, kegg$n_ora, kegg$n_gsea, kegg$audit$status))
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
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj) & !is.na(res$log2FoldChange), ]
    res$base_id <- strip_version(res$gene_id)
    write_id_map(res)  # no entrez on this route; symbol/gene_id still bridge term extraction
    rank_info <- build_deterministic_rank(res$log2FoldChange, res$base_id)
    gene_list <- rank_info$values

    tested_genes <- unique(res$base_id)  # tested-gene background for the KEGG ORA universe

    up_ids <- read_ids_csv(up_file)
    down_ids <- read_ids_csv(down_file)
    all_ids <- unique(c(up_ids, down_ids))

    kegg <- run_kegg(
      all_ids, gene_list, "kegg", background = tested_genes, rank_info = rank_info,
      foregrounds = list(up = up_ids, down = down_ids, combined = all_ids),
      expected_name = configured_organism_name)

    saveRDS(list(ego_all = NULL, ego_up = NULL, ego_down = NULL,
                 gse = NULL, ego_do = NULL,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, orgdb = "",
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "clusterprofiler", gprofiler_table = NULL),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      "GO/disease enrichment: skipped (no Bioconductor OrgDb for this organism).",
      sprintf("Ranked genes (GSEA input): %d", length(gene_list)),
      rank_evidence_lines(rank_info),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      kegg_evidence_lines(kegg),
      sprintf("KEGG adjusted results meeting the criterion: %d (ORA), %d (GSEA)",
              kegg$n_ora, kegg$n_gsea))
    list(status = resource_status_to_check(kegg$audit$status),
         message = sprintf(paste0("KEGG-only enrichment: ORA=%d, GSEA=%d adjusted pathways; ",
                                  "resource=%s; %s"),
                           kegg$n_ora, kegg$n_gsea, kegg$audit$status,
                           kegg$audit$reason))
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
