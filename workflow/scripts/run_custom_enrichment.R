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

# Custom gene-set enrichment (optional): clusterProfiler ORA (enricher) + GSEA against a
# user-supplied gene-set collection (a GMT and/or an id->term table), via TERM2GENE. This is
# organism-agnostic (no OrgDb/KEGG needed), so it works even for organisms run_enrichment.R
# skips (e.g. Fusarium graminearum). Implemented as a SEPARATE rule/script so run_enrichment.R
# is untouched. Best-effort: any failure degrades to empty outputs + a REVIEW_REQUIRED check
# so the pipeline still completes.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

results_file <- snakemake@input[["results"]]
up_file <- snakemake@input[["up"]]
down_file <- snakemake@input[["down"]]
gmt <- snakemake@params[["gmt"]]
annot <- snakemake@params[["annot"]]
bg <- snakemake@params[["background"]]
alpha <- as.numeric(snakemake@params[["alpha"]])
out <- snakemake@output

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "11_custom_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)
# Same id normalization as run_enrichment.R (Ensembl .version strip; NCBI LOC<id> strip).
strip_version <- function(id) {
  v <- grepl("^ENS", id); id[v] <- sub("\\.\\d+$", "", id[v])
  l <- grepl("^LOC[0-9]+$", id); id[l] <- sub("^LOC", "", id[l])
  id
}
read_ids_csv <- function(p) {
  if (!file.exists(p)) return(character(0))
  d <- tryCatch(read.csv(p, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(d) || !"gene_id" %in% names(d) || nrow(d) == 0) return(character(0))
  unique(strip_version(as.character(d$gene_id)))
}

# Custom GSEA uses the same explicit reproducibility contract as the built-in
# enrichment routes. Invalid ids and non-finite scores are removed first, then
# repeated canonical ids are collapsed by their median score so the result does
# not depend on source-row order. Statistics are ordered decreasingly and exact
# ties use canonical-id byte order after UTF-8 encoding.
build_custom_deterministic_rank <- function(statistic, canonical_id) {
  if (length(statistic) != length(canonical_id)) {
    stop("Custom GSEA statistics and canonical ids must have equal lengths")
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

  rank_order <- order(-statistic, canonical_id, method = "radix")
  values <- statistic[rank_order]
  names(values) <- canonical_id[rank_order]
  tie_sizes <- if (length(values)) {
    runs <- rle(unname(values))$lengths
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
    policy = paste0("invalid IDs and non-finite statistics removed; duplicate canonical IDs ",
                    "collapsed by median; finite statistic descending; exact ties by canonical ",
                    "gene ID ascending in bytewise UTF-8/C-locale order")
  )
}

custom_rank_evidence_lines <- function(rank_info) {
  c(
    sprintf("Custom GSEA ranking order: %s.", rank_info$policy),
    sprintf(paste0("Custom GSEA exact-score ties: %.0f pair(s) across %d tie group(s), ",
                   "involving %d/%d ranked genes."),
            rank_info$tie_pair_n, rank_info$tie_group_n,
            rank_info$tied_gene_n, rank_info$ranked_gene_n),
    sprintf(paste0("Custom GSEA duplicate canonical-ID collapse: %d group(s) containing %d ",
                   "finite source row(s); %d row(s) collapsed by median; %d invalid-ID ",
                   "and %d non-finite-score row(s) removed before collapse."),
            rank_info$duplicate_id_group_n, rank_info$duplicate_source_row_n,
            rank_info$duplicate_rows_collapsed, rank_info$invalid_id_removed,
            rank_info$nonfinite_removed)
  )
}

# Pinned fgsea 1.36.2 re-sorts a decreasing vector stably, so the canonical-id
# secondary order reaches its enrichment calculation. Replace only fgsea's
# generic arbitrary-order notice with evidence; every unrelated warning passes.
with_deterministic_custom_gsea_ties <- function(expr, rank_info) {
  withCallingHandlers(expr, warning = function(w) {
    warning_text <- conditionMessage(w)
    is_fgsea_tie_notice <-
      grepl("There are ties in the preranked stats", warning_text, fixed = TRUE) &&
      grepl("order of those tied genes will be arbitrary", tolower(warning_text),
            fixed = TRUE)
    if (is_fgsea_tie_notice) {
      message(sprintf(paste0("Custom GSEA deterministic tie handling: %.0f exact-score ",
                             "pair(s) across %d group(s), involving %d/%d ranked genes; %s."),
                      rank_info$tie_pair_n, rank_info$tie_group_n,
                      rank_info$tied_gene_n, rank_info$ranked_gene_n,
                      rank_info$policy))
      invokeRestart("muffleWarning")
    }
  })
}

# Always create outputs first so the rule succeeds even on failure.
writeLines("", out[["ora"]]); writeLines("", out[["gsea"]])
saveRDS(list(), out[["objects"]])
summary_lines <- c("Custom gene-set enrichment summary", "=================================", "")

result <- tryCatch({
  suppressMessages(library(clusterProfiler))
  # --- TERM2GENE (+ optional TERM2NAME) from a GMT and/or an id->term table ---
  t2g <- NULL; t2n <- NULL
  if (nzchar(gmt) && file.exists(gmt)) {
    g <- clusterProfiler::read.gmt(gmt)            # columns: term, gene
    g$gene <- strip_version(as.character(g$gene))
    t2g <- g[, c("term", "gene")]
  }
  if (nzchar(annot) && file.exists(annot)) {       # col1 gene-id, col2 term, optional col3 term-name
    sep <- if (grepl("\\.csv$", annot, ignore.case = TRUE)) "," else "\t"
    a <- read.delim(annot, sep = sep, stringsAsFactors = FALSE, header = TRUE)
    a[[1]] <- strip_version(as.character(a[[1]]))
    add <- data.frame(term = as.character(a[[2]]), gene = a[[1]], stringsAsFactors = FALSE)
    t2g <- if (is.null(t2g)) add else rbind(t2g, add)
    if (ncol(a) >= 3) t2n <- unique(data.frame(term = as.character(a[[2]]), name = as.character(a[[3]]), stringsAsFactors = FALSE))
  }
  if (is.null(t2g) || nrow(t2g) == 0) stop("no usable custom gene sets (empty GMT / annotation table)")
  t2g <- unique(t2g[!is.na(t2g$gene) & nzchar(t2g$gene), ])

  # --- universe: background file if given, else tested genes (non-NA padj), like GO ORA ---
  res <- read.csv(results_file, stringsAsFactors = FALSE)
  tested <- unique(strip_version(as.character(res$gene_id[!is.na(res$padj)])))
  if (nzchar(bg) && file.exists(bg)) {
    universe <- unique(strip_version(trimws(readLines(bg, warn = FALSE))))
    universe <- universe[nzchar(universe) & !startsWith(universe, "#")]
  } else universe <- tested

  # --- significant set (up+down) + ranked list (log2FC) ---
  all_sig <- unique(c(read_ids_csv(up_file), read_ids_csv(down_file)))
  res2 <- res[!is.na(res$padj) & !is.na(res$log2FoldChange), ]
  res2$base_id <- strip_version(as.character(res2$gene_id))
  rank_info <- build_custom_deterministic_rank(res2$log2FoldChange, res2$base_id)
  ranked <- rank_info$values
  rank_evidence <- custom_rank_evidence_lines(rank_info)
  message(paste(rank_evidence, collapse = "\n"))

  overlap <- length(intersect(unique(t2g$gene), c(all_sig, names(ranked))))
  args_t2n <- if (is.null(t2n)) list() else list(TERM2NAME = t2n)

  eora <- if (length(all_sig) >= 1) tryCatch(do.call(enricher, c(list(
            gene = all_sig, universe = universe, TERM2GENE = t2g, pvalueCutoff = alpha,
            pAdjustMethod = "BH", qvalueCutoff = 0.20, minGSSize = 10, maxGSSize = 500), args_t2n)),
          error = function(e) { message("enricher failed: ", conditionMessage(e)); NULL }) else NULL
  if (nrows(eora) > 0) write.csv(as.data.frame(eora), out[["ora"]], row.names = FALSE)

  egse <- NULL
  if (length(ranked) > 0) {
    set.seed(42)
    egse <- tryCatch(with_deterministic_custom_gsea_ties(
              do.call(GSEA, c(list(geneList = ranked, TERM2GENE = t2g, pvalueCutoff = alpha,
                pAdjustMethod = "BH", minGSSize = 10, maxGSSize = 500, eps = 0,
                seed = TRUE, verbose = FALSE), args_t2n)), rank_info),
            error = function(e) { message("GSEA failed: ", conditionMessage(e)); NULL })
  }
  if (nrows(egse) > 0) write.csv(as.data.frame(egse), out[["gsea"]], row.names = FALSE)

  saveRDS(list(eora = eora, egse = egse, n_terms = length(unique(t2g$term))), out[["objects"]])
  summary_lines <<- c(summary_lines,
    sprintf("Custom gene sets (terms): %d", length(unique(t2g$term))),
    sprintf("Universe: %d (%s)", length(universe), if (nzchar(bg)) "background file" else "tested genes"),
    sprintf("Significant genes (ORA input): %d", length(all_sig)),
    rank_evidence,
    sprintf("Custom ORA terms: %d", nrows(eora)),
    sprintf("Custom GSEA sets: %d", nrows(egse)))
  if (overlap == 0)
    list(status = "REVIEW_REQUIRED", message = sprintf("Custom enrichment empty: 0/%d gene-set genes overlap the DE gene id space -- the gene sets are probably in a different identifier namespace.", length(unique(t2g$gene))))
  else
    list(status = "PASS", message = sprintf("Custom enrichment: ORA=%d, GSEA=%d terms.", nrows(eora), nrows(egse)))
}, error = function(e) {
  summary_lines <<- c(summary_lines, paste("Custom enrichment failed:", conditionMessage(e)))
  list(status = "REVIEW_REQUIRED", message = paste("Custom enrichment could not run:", conditionMessage(e)))
})

writeLines(summary_lines, out[["summary"]])
write_check(out[["check"]], result$status, result$message)
sink(type = "message"); close(log_con)
