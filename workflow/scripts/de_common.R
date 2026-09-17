# Shared helpers for the differential-expression engines (run_deseq2.R, run_edger.R,
# run_voom.R, run_limma.R). Sourced via snakemake@scriptdir before any library() call,
# so the load-warning muffling below is installed first. Carries no statistics: each
# engine script keeps its own model fit and result-column mapping.

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

# Hand-written check writer (no jsonlite dependency in the DE environment). The escape
# order is backslash first, then the quote, then the control characters: escaping the
# quote first would re-escape the backslash it just inserted. A message with none of
# those characters passes through unchanged, so existing check files keep their bytes.
json_escape <- function(s) {
  s <- gsub("\\", "\\\\", s, fixed = TRUE)
  s <- gsub('"', '\\"', s, fixed = TRUE)
  named <- c("\b" = "\\b", "\f" = "\\f", "\n" = "\\n", "\r" = "\\r", "\t" = "\\t")
  for (ch in names(named)) s <- gsub(ch, named[[ch]], s, fixed = TRUE)
  for (code in setdiff(1:31, utf8ToInt(paste(names(named), collapse = "")))) {
    s <- gsub(intToUtf8(code), sprintf("\\u%04x", code), s, fixed = TRUE)
  }
  s
}

write_check <- function(path, name, status, messages) {
  msg_json <- paste0(
    sprintf('    {"status": "%s", "message": "%s"}', vapply(messages, `[[`, "", "status"),
            vapply(lapply(messages, `[[`, "message"), json_escape, "")),
    collapse = ",\n")
  json <- sprintf('{\n  "check": "%s",\n  "status": "%s",\n  "messages": [\n%s\n  ]\n}',
                  name, status, msg_json)
  writeLines(json, path)
}

# Parse gene_id -> (gene_name, gene_biotype, NCBI GeneID) from a GTF attribute column
# and align to `gene_ids` (NA where unknown). Dependency-free regex parse; returns
# all-NA when the GTF is absent (e.g. count-matrix mode has no reference). The GeneID
# comes from db_xref "GeneID:<n>", which NCBI RefSeq GTFs carry on the gene record; it
# bridges a locus-tag gene id (e.g. S. pombe SPOM_SPAC212.11) to the numeric key KEGG
# expects, for organisms whose KEGG code maps genes by NCBI GeneID rather than by the
# locus tag itself (run_enrichment.R does the mapping; this only records the fact).
annotate_from_gtf <- function(gtf_path, gene_ids) {
  na_vec <- setNames(rep(NA_character_, length(gene_ids)), gene_ids)
  if (is.null(gtf_path) || length(gtf_path) < 1 || !nzchar(gtf_path[[1]]) ||
      !file.exists(gtf_path[[1]])) {
    return(list(symbol = na_vec, biotype = na_vec, geneid = na_vec))
  }
  gtf <- tryCatch(
    read.delim(gtf_path[[1]], header = FALSE, sep = "\t", quote = "", comment.char = "#",
               colClasses = c("NULL", "NULL", "character", "NULL", "NULL",
                              "NULL", "NULL", "NULL", "character")),
    error = function(e) NULL)
  if (is.null(gtf) || ncol(gtf) < 2) return(list(symbol = na_vec, biotype = na_vec, geneid = na_vec))
  names(gtf) <- c("feature", "attr")
  g <- gtf[gtf$feature == "gene", , drop = FALSE]
  if (nrow(g) == 0) g <- gtf  # some GTFs (e.g. minimal RefSeq) lack a gene feature
  a <- g$attr
  pull <- function(key) ifelse(grepl(paste0(key, ' "'), a),
                               sub(paste0('.*', key, ' "([^"]+)".*'), "\\1", a), NA_character_)
  gid <- pull("gene_id")
  sym <- pull("gene_name")
  bt <- pull("gene_biotype")
  gt <- pull("gene_type")           # GENCODE uses gene_type; Ensembl gene_biotype
  bt[is.na(bt)] <- gt[is.na(bt)]
  ncbi <- ifelse(grepl('db_xref "GeneID:[0-9]+"', a),
                sub('.*db_xref "GeneID:([0-9]+)".*', "\\1", a), NA_character_)
  keep <- !is.na(gid) & !duplicated(gid)
  gid <- gid[keep]; sym <- sym[keep]; bt <- bt[keep]; ncbi <- ncbi[keep]
  idx <- match(gene_ids, gid)
  list(symbol = setNames(sym[idx], gene_ids), biotype = setNames(bt[idx], gene_ids),
       geneid = setNames(ncbi[idx], gene_ids))
}

# featureCounts matrix -> integer count matrix keyed by sample_id (the tool names its
# columns by BAM path).
read_featurecounts <- function(path) {
  fc <- read.delim(path, comment.char = "#", check.names = FALSE)
  rownames(fc) <- fc$Geneid
  cts <- as.matrix(fc[, -(1:6)])
  mode(cts) <- "integer"
  colnames(cts) <- sub("_Aligned.sortedByCoord.out.bam$", "", basename(colnames(cts)))
  cts
}

# Contrast levels must be set, distinct, and present in the factor.
check_contrast <- function(numerator, denominator, level_set, con_factor) {
  if (!nzchar(numerator) || !nzchar(denominator)) {
    stop("Contrast numerator and denominator must both be set.")
  }
  if (identical(numerator, denominator)) {
    stop("Contrast numerator and denominator must differ.")
  }
  if (!(numerator %in% level_set) || !(denominator %in% level_set)) {
    stop(sprintf("Contrast levels '%s'/'%s' not found in factor '%s' (levels: %s).",
                 numerator, denominator, con_factor, paste(level_set, collapse = ", ")))
  }
  invisible(TRUE)
}

# Build the additive group-means design used by edgeR, limma-voom and limma, plus a
# positional numerator-minus-denominator contrast. Synthetic internal variable names keep
# sample columns such as `grp` from changing formula lookup.
# Coefficient names remain unique for readable fit objects, but the estimand does not depend
# on sanitizing a raw level name back into a coefficient name.
group_means_design_contrast <- function(grp, coldata, covariates, numerator, denominator) {
  level_set <- levels(grp)
  n_levels <- length(level_set)
  model_data <- data.frame(.contrast_group = grp, row.names = rownames(coldata),
                           check.names = FALSE)
  if (length(covariates)) {
    for (i in seq_along(covariates)) {
      model_data[[sprintf(".covariate_%d", i)]] <- coldata[[covariates[[i]]]]
    }
  }
  design <- stats::model.matrix(
    stats::reformulate(names(model_data), intercept = FALSE), data = model_data)
  if (ncol(design) < n_levels) {
    stop("The group-means design did not produce one coefficient per contrast level.")
  }
  group_columns <- seq_len(n_levels)
  colnames(design) <- make.unique(
    c(make.names(level_set, unique = TRUE), colnames(design)[-group_columns]), sep = ".")
  contrast <- numeric(ncol(design))
  contrast[match(numerator, level_set)] <- 1
  contrast[match(denominator, level_set)] <- -1
  names(contrast) <- colnames(design)
  list(design = design, contrast = contrast)
}

# A numeric column with few distinct values (batch coded 1/2/3) is fitted as a linear trend,
# not as a factor; flag it so the user relabels the levels if they meant groups.
numeric_covariate_checks <- function(coldata, covariates) {
  out <- list()
  for (v in covariates) {
    x <- coldata[[v]]
    n_lv <- length(unique(x[!is.na(x)]))
    if (is.numeric(x) && n_lv <= 10) out[[length(out) + 1]] <- list(
      status = "REVIEW_REQUIRED",
      message = sprintf(paste0(
        "Design term '%s' is numeric with %d distinct values and is fitted as a continuous covariate ",
        "(a linear trend), not as a factor. If these are group labels (batch, run, donor), use ",
        "non-numeric labels such as 'b1', 'b2' so they are modelled as levels."), v, n_lv))
  }
  out
}

# An interaction or nesting operator in the design formula. all.vars() strips these, so a
# group-means engine would silently refit the term as additive; DESeq2 fits the formula as
# typed but then reports a coefficient that is not the interaction.
has_interaction <- function(design_formula) {
  isTRUE(grepl("[:*^/]", design_formula))
}

# Moderated coefficient standard error behind limma's t statistic. topTable does not expose
# this column, but eBayes stores both factors needed to derive it without approximation:
# stdev.unscaled * sqrt(posterior residual variance).
moderated_lfc_se <- function(fit, row_names) {
  se <- as.numeric(fit$stdev.unscaled[, 1] * sqrt(fit$s2.post))
  names(se) <- rownames(fit$coefficients)
  se <- unname(se[row_names])
  if (length(se) != length(row_names) || any(!is.finite(se)) || any(se <= 0)) {
    stop("limma produced an invalid moderated log2-fold-change standard error.")
  }
  se
}

# PC1/PC2 for the covariate-structure screen (check 23), for the engines whose own expression
# matrix is already on a log scale (edgeR/limma-voom logCPM, microarray log2 intensity).
# prcomp on the `ntop` highest-variance rows is DESeq2::plotPCA's convention and 500 is its
# default; run_deseq2.R keeps calling plotPCA(vsd) itself. Sample ids come from the matrix the
# PCA was computed on, never from a separate table that could be ordered differently.
write_pca_coordinates <- function(mat, path, ntop = 500) {
  mat <- mat[stats::complete.cases(mat), , drop = FALSE]
  if (nrow(mat) < 2) {
    stop("Too few complete rows in the expression matrix to compute PCA coordinates.")
  }
  rv <- apply(mat, 1, stats::var)
  select <- order(rv, decreasing = TRUE)[seq_len(min(ntop, length(rv)))]
  pcs <- stats::prcomp(t(mat[select, , drop = FALSE]))$x
  write.csv(data.frame(sample_id = colnames(mat), PC1 = pcs[, 1], PC2 = pcs[, 2]),
            path, row.names = FALSE)
}

# Threshold for the informational |log2FC| > L companion test. DESeq2's altHypothesis="greaterAbs",
# limma's treat() and edgeR's glmTreat() all test H0: |log2FC| <= L, which is vacuous at L = 0, so
# refuse a non-positive threshold instead of writing a column that means nothing. Call sites pass
# the configured effect-size cut when it is set and 1.0 when it is not.
require_lfc_threshold <- function(L_eq) {
  if (!is.numeric(L_eq) || length(L_eq) != 1L || !is.finite(L_eq) || L_eq <= 0) {
    stop("The |log2FC| companion test requires a fold-change threshold greater than zero.")
  }
  L_eq
}

# Human-readable per-sample display label from the optional `library_name` column. The name may
# be blank and may repeat, so it is never a key: sample_id remains the sole identifier for file
# paths, matrix columns, results-table columns and every sample match. The label is the library
# name when it is present and unique among the samples handed in; "<library name> (<sample_id>)"
# when the name repeats, so no plot carries two identical point labels and no heatmap gets
# duplicate dimnames (R rejects those); and the sample id when the name is blank or the column is
# absent. Uniqueness of the disambiguated form follows from sample_id being unique, which the
# sample-sheet schema guarantees. read.delim types an all-blank column as logical NA, an
# all-numeric one as integer, and converts the bare token NA to missing, so the value is coerced
# to character and NA is treated as blank. Uniqueness is judged over the samples passed in; every
# call site passes every sample it draws. Mirrored by workflow/scripts/_sample_labels.py and
# pinned to it by tests/test_sample_labels.py.
sample_display_labels <- function(sample_ids, library_names = NULL) {
  ids <- as.character(sample_ids)
  if (is.null(library_names)) return(ids)
  nm <- as.character(library_names)
  if (length(nm) != length(ids)) {
    stop("library_name and sample_id have different lengths.")
  }
  nm[is.na(nm)] <- ""
  nm <- trimws(nm)
  nm[nm == "NA"] <- ""  # read.delim already blanks the bare token; match it for other readers
  named <- nzchar(nm)
  repeated <- named & (nm %in% nm[named][duplicated(nm[named])])
  out <- ids
  out[named] <- nm[named]
  out[repeated] <- sprintf("%s (%s)", nm[repeated], ids[repeated])
  out
}

# Display labels for a colData/sample frame, keyed by sample id. Returns the ids themselves when
# the frame carries no library_name column, so a caller can relabel unconditionally.
sample_label_map <- function(sample_frame) {
  cd <- as.data.frame(sample_frame)
  ids <- rownames(cd)
  setNames(sample_display_labels(ids, cd[["library_name"]]), ids)
}
