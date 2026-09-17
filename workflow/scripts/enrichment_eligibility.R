# Separate the ORA tested family from the rows that may contribute a finite GSEA rank.
# The caller supplies its established ORA mask because built-in and custom routes have
# different tested-family contracts. Additional rank eligibility is deliberately added
# in one place so raw-p/QC exclusions stay identical across routes.
enrichment_raw_p <- function(results, raw_p_columns = c(
    "pvalue", "P.Value", "pval", "p_value", "p")) {
  raw_p_column <- raw_p_columns[raw_p_columns %in% names(results)][1]
  if (!length(raw_p_column) || is.na(raw_p_column)) raw_p_column <- NA_character_
  values <- if (is.na(raw_p_column)) rep(NA_real_, nrow(results)) else
    suppressWarnings(as.numeric(results[[raw_p_column]]))
  list(values = values, column = raw_p_column)
}

additional_gsea_candidate_mask <- function(
    results, additional_qc_mask = rep(TRUE, nrow(results)),
    raw_p_columns = c("pvalue", "P.Value", "pval", "p_value", "p")) {
  if (length(additional_qc_mask) != nrow(results)) {
    stop("Additional GSEA QC mask must describe every result row")
  }
  additional_qc_mask <- !is.na(additional_qc_mask) & as.logical(additional_qc_mask)
  missing_adjusted_p <- if ("padj" %in% names(results)) is.na(results$padj) else
    rep(FALSE, nrow(results))
  raw_p <- enrichment_raw_p(results, raw_p_columns)
  missing_adjusted_p & additional_qc_mask & is.finite(raw_p$values)
}

prepare_enrichment_populations <- function(results, rank_values, ora_mask,
                                           legacy_rank_mask = ora_mask,
                                           additional_qc_mask = rep(TRUE, nrow(results)),
                                           raw_p_columns = c("pvalue", "P.Value",
                                                             "pval", "p_value", "p")) {
  if (!is.data.frame(results) || length(rank_values) != nrow(results) ||
      length(ora_mask) != nrow(results) || length(legacy_rank_mask) != nrow(results) ||
      length(additional_qc_mask) != nrow(results)) {
    stop("Enrichment results, ranking values, and eligibility masks must describe the same rows")
  }
  rank_values <- suppressWarnings(as.numeric(rank_values))
  ora_mask <- !is.na(ora_mask) & as.logical(ora_mask)
  legacy_rank_mask <- !is.na(legacy_rank_mask) & as.logical(legacy_rank_mask)
  additional_qc_mask <- !is.na(additional_qc_mask) & as.logical(additional_qc_mask)
  finite_rank <- is.finite(rank_values)
  raw_p <- enrichment_raw_p(results, raw_p_columns)
  missing_adjusted_p <- if ("padj" %in% names(results)) is.na(results$padj) else
    rep(FALSE, nrow(results))
  additional_candidate <- missing_adjusted_p & additional_qc_mask & finite_rank
  additional_rank_mask <- additional_candidate & is.finite(raw_p$values)
  rank_mask <- legacy_rank_mask | additional_rank_mask
  list(
    ora = results[ora_mask, , drop = FALSE],
    rank = results[rank_mask, , drop = FALSE],
    additional_rank = results[additional_rank_mask, , drop = FALSE],
    ora_mask = ora_mask,
    legacy_rank_mask = legacy_rank_mask,
    additional_rank_mask = additional_rank_mask,
    rank_mask = rank_mask,
    rank_values = rank_values,
    raw_p_column = raw_p$column,
    additional_rank_n = sum(additional_rank_mask),
    missing_raw_p_excluded_n = sum(additional_candidate & !is.finite(raw_p$values)))
}

enrichment_eligibility_lines <- function(population, label = "GSEA") {
  raw_p <- if (is.na(population$raw_p_column)) "unavailable" else population$raw_p_column
  c(
    sprintf("%s additional rank rows: %d padj-missing row(s) admitted with a finite rank and finite raw p (%s).",
            label, population$additional_rank_n, raw_p),
    sprintf("%s QC exclusion: %d padj-missing finite-rank row(s) lacked a finite raw p and were not admitted.",
            label, population$missing_raw_p_excluded_n))
}
