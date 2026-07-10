# Multi-study differential-expression META-ANALYSIS.
# Per-study DESeq2 (subset the merged matrix by study-of-origin) -> HTSFilter per study ->
# intersect the gene space -> combine per-study two-sided Wald p-values with metaRNASeq::invnorm
# (replicate-weighted inverse normal) + post-hoc sign concordance, and co-report a metafor
# random/fixed-effect effect-size summary on the UNSHRUNKEN log2FC + lfcSE. Directionless
# combined p-values with conflicting per-study signs are FLAGGED (common_direction=discordant,
# never called a meta-DEG), not silently dropped. Method: Rau/Marot/Jaffrezic (BMC Bioinf 2014).
#
# The two functions below are pure and unit-tested; the snakemake driver runs only under Snakemake.

# Muffle only the benign "package X was built under R version 4.5.3" load warning (same shim as
# run_deseq2.R); the r45 ABI is stable so 4.5.3-built conda packages run under the pinned 4.5.2.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

suppressMessages({
  library(DESeq2)
  library(metaRNASeq)
  library(metafor)
  library(HTSFilter)
})

# Reproducibility: seed any stochastic step (HTSFilter subsampling, etc.).
set.seed(42)


# --- Pure combination core (unit-tested against the metaRNASeq vignette) ------------------------
# per_study: named list of per-study data.frames, rownames = gene_id, with numeric columns
#   log2FoldChange (UNSHRUNKEN), lfcSE, pvalue, padj.  nrep: NAMED integer vector, per-study
#   replicate count in the two contrast arms.  Returns one row per gene in the intersected,
#   NA-free set, ordered by combined FDR.
combine_meta <- function(per_study, nrep, alpha = 0.05) {
  studies <- names(per_study)
  k <- length(studies)
  if (k < 2) stop("meta-analysis needs at least 2 admissible studies")
  empty_result <- function() data.frame(
    gene_id = character(0), combined_pvalue = numeric(0), combined_padj = numeric(0),
    common_direction = character(0), n_studies_sig = integer(0), rem_log2FC = numeric(0),
    rem_ci_lo = numeric(0), rem_ci_hi = numeric(0), rem_pvalue = numeric(0), tau2 = numeric(0),
    I2 = numeric(0), QEp = numeric(0), meta_sig = logical(0), stringsAsFactors = FALSE)
  common <- Reduce(intersect, lapply(per_study, rownames))
  # No shared gene ids -> almost always a gene-id namespace / organism mismatch; return an empty
  # result (the driver reports it) rather than stop()-ing the whole rule.
  if (length(common) == 0) return(empty_result())
  grab <- function(col) {
    m <- vapply(studies, function(s) as.numeric(per_study[[s]][common, col]), numeric(length(common)))
    if (is.null(dim(m))) m <- matrix(m, ncol = k, dimnames = list(NULL, studies))
    m
  }
  pmat <- grab("pvalue"); lmat <- grab("log2FoldChange"); smat <- grab("lfcSE"); padjmat <- grab("padj")
  # p-value combination needs the p defined in every study; drop genes NA in any study (reported).
  keep <- stats::complete.cases(pmat) & stats::complete.cases(lmat) & stats::complete.cases(smat)
  common <- common[keep]
  # Every shared gene NA in >=1 study (e.g. a degenerate arm) collapses the combinable set; return
  # empty instead of calling invnorm on length-0 vectors (which errors with non-conformable arrays).
  if (length(common) == 0) return(empty_result())
  pmat <- pmat[keep, , drop = FALSE]; lmat <- lmat[keep, , drop = FALSE]
  smat <- smat[keep, , drop = FALSE]; padjmat <- padjmat[keep, , drop = FALSE]
  # Clamp p away from {0,1}: DESeq2 emits exact-0 p-values and qnorm(1)/qnorm(0) = +/-Inf would
  # corrupt the combined Z-statistic.
  pmat_c <- pmin(pmax(pmat, .Machine$double.xmin), 1 - 1e-16)
  indpval <- lapply(seq_len(k), function(j) pmat_c[, j])
  fc <- metaRNASeq::invnorm(indpval, nrep = as.integer(nrep[studies]), BHth = alpha)

  # Direction: concordant only when every study agrees on the sign of the UNSHRUNKEN LFC.
  signs <- sign(lmat)
  commonsgn <- ifelse(abs(rowSums(signs)) == k, sign(rowSums(signs)), 0L)  # 0 = discordant

  # Effect-size companion: DerSimonian-Laird random effects (k>=3) or fixed effect (k=2).
  method <- if (k >= 3) "DL" else "FE"
  rc <- c("beta", "ci.lb", "ci.ub", "pval", "tau2", "I2", "QEp")
  rem <- t(vapply(seq_along(common), function(i) {
    m <- tryCatch(suppressWarnings(
      metafor::rma.uni(yi = lmat[i, ], sei = pmax(smat[i, ], 1e-6), method = method)),
      error = function(e) NULL)
    if (is.null(m)) return(setNames(rep(NA_real_, 7L), rc))
    setNames(c(as.numeric(m$beta)[1], m$ci.lb[1], m$ci.ub[1], m$pval[1], m$tau2[1], m$I2[1], m$QEp[1]), rc)
  }, setNames(numeric(7L), rc)))
  if (!is.matrix(rem)) rem <- matrix(rem, ncol = 7L, dimnames = list(NULL, rc))
  if (k < 3) rem[, c("tau2", "I2", "QEp")] <- NA_real_   # heterogeneity not estimable at k=2

  out <- data.frame(
    gene_id          = common,
    combined_pvalue  = fc$rawpval,
    combined_padj    = fc$adjpval,
    common_direction = ifelse(commonsgn > 0, "up", ifelse(commonsgn < 0, "down", "discordant")),
    n_studies_sig    = as.integer(rowSums(!is.na(padjmat) & padjmat < alpha)),
    rem_log2FC = rem[, "beta"], rem_ci_lo = rem[, "ci.lb"], rem_ci_hi = rem[, "ci.ub"],
    rem_pvalue = rem[, "pval"], tau2 = rem[, "tau2"], I2 = rem[, "I2"], QEp = rem[, "QEp"],
    stringsAsFactors = FALSE)
  # A meta-DEG = significant combined FDR AND concordant direction. Discordant genes keep their
  # row (searchable) but are never called significant.
  out$meta_sig <- !is.na(out$combined_padj) & out$combined_padj < alpha & out$common_direction != "discordant"
  for (s in studies) {
    out[[paste0("study_", s, "_log2FC")]] <- lmat[, s]
    out[[paste0("study_", s, "_padj")]]   <- padjmat[, s]
  }
  out[order(out$combined_padj, out$combined_pvalue), ]
}


# --- Per-study DESeq2 on the merged matrix -----------------------------------------------------
# Subsets the merged count matrix by the study-of-origin column, runs DESeq2 per study on the two
# contrast arms, filters each study's own dds with HTSFilter (independent filtering OFF in
# results() to avoid double filtering), and returns per-study unshrunken results + nrep + the list
# of studies dropped (single-arm or < min_reps) with the reason.
per_study_deseq <- function(cts, samples, dataset_col, contrast_factor, num, den, min_reps = 2L) {
  rownames(samples) <- samples$sample_id
  samples[[dataset_col]] <- trimws(as.character(samples[[dataset_col]]))  # match the Python gates
  studies <- unique(samples[[dataset_col]])
  studies <- studies[nzchar(studies)]
  per_study <- list(); nrep <- integer(); excluded <- character()
  for (ds in studies) {
    ss <- samples[as.character(samples[[dataset_col]]) == ds, , drop = FALSE]
    ss <- ss[as.character(ss[[contrast_factor]]) %in% c(num, den), , drop = FALSE]
    tab <- table(factor(as.character(ss[[contrast_factor]]), levels = c(num, den)))
    if (length(tab) < 2 || any(tab < min_reps)) {
      excluded[ds] <- sprintf("excluded (needs >= %d samples in both '%s' and '%s'; has %d/%d)",
                              min_reps, num, den, tab[[num]], tab[[den]])
      next
    }
    sub <- cts[, ss$sample_id, drop = FALSE]
    cd <- ss
    cd[[contrast_factor]] <- factor(as.character(cd[[contrast_factor]]), levels = c(den, num))  # den = reference
    dds <- DESeqDataSetFromMatrix(sub, cd, design = as.formula(paste0("~ ", contrast_factor)))
    dds <- DESeq(dds, quiet = TRUE)
    dds <- tryCatch(HTSFilter::HTSFilter(dds, plot = FALSE)$filteredData, error = function(e) dds)
    r <- results(dds, contrast = c(contrast_factor, num, den), independentFiltering = FALSE)
    per_study[[ds]] <- as.data.frame(r)[, c("baseMean", "log2FoldChange", "lfcSE", "pvalue", "padj")]
    nrep[ds] <- nrow(ss)
  }
  list(per_study = per_study, nrep = nrep, excluded = excluded)
}


# --- Snakemake driver (runs only under Snakemake) ----------------------------------------------
if (exists("snakemake")) {
  log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")
  counts_file <- snakemake@input[["counts"]]
  samples_file <- snakemake@input[["samples"]]
  con_factor <- snakemake@params[["contrast_factor"]]
  num <- snakemake@params[["numerator"]]; den <- snakemake@params[["denominator"]]
  ds_col <- tryCatch(snakemake@params[["dataset_column"]], error = function(e) "dataset")
  alpha <- as.numeric(snakemake@params[["alpha"]])

  fc_tab <- read.delim(counts_file, comment.char = "#", check.names = FALSE)
  rownames(fc_tab) <- fc_tab$Geneid
  cts <- as.matrix(fc_tab[, -(1:6)]); mode(cts) <- "integer"
  colnames(cts) <- sub("_Aligned.sortedByCoord.out.bam$", "", basename(colnames(cts)))
  samples <- read.delim(samples_file, stringsAsFactors = FALSE)

  fanout <- per_study_deseq(cts, samples, ds_col, con_factor, num, den)
  # write per-study tables + the excluded report
  for (s in names(fanout$per_study)) {
    d <- fanout$per_study[[s]]; d$gene_id <- rownames(d)
    write.csv(d, file.path(dirname(snakemake@output[["results"]]), paste0("per_study_", s, ".csv")), row.names = FALSE)
  }
  writeLines(if (length(fanout$excluded)) paste(names(fanout$excluded), fanout$excluded)
             else "All studies admissible.",
             file.path(dirname(snakemake@output[["results"]]), "excluded_studies.txt"))

  n_datasets <- length(unique(as.character(samples[[ds_col]])))
  if (length(fanout$per_study) < 2) {
    # Fewer than 2 studies contain both contrast arms -> groups are confounded with study and no
    # meta-analysis can separate them. Emit an empty result + an explanatory FAIL check instead of
    # crashing (the pre-launch confounding gate normally blocks this; this is a defensive backstop).
    write.csv(data.frame(gene_id = character(0), combined_pvalue = numeric(0),
                         combined_padj = numeric(0), common_direction = character(0),
                         meta_sig = logical(0)),
              snakemake@output[["results"]], row.names = FALSE)
    msg <- sprintf("Meta-analysis not run: only %d of %d studies contain both '%s' and '%s' with >=2 replicates (the groups are confounded with study-of-origin). %s",
                   length(fanout$per_study), n_datasets, num, den,
                   paste(fanout$excluded, collapse = "; "))
    status <- "FAIL"
  } else {
    meta <- combine_meta(fanout$per_study, fanout$nrep, alpha)
    write.csv(meta, snakemake@output[["results"]], row.names = FALSE)
    n_sig <- sum(meta$meta_sig, na.rm = TRUE)
    min_genes <- min(vapply(fanout$per_study, nrow, integer(1)))
    warn <- if (nrow(meta) == 0)
              " No genes are shared across all studies -- check that every study uses the SAME gene-id namespace and organism."
            else if (nrow(meta) < 0.5 * min_genes)
              sprintf(" Only %d of ~%d genes are shared -- a low overlap can signal mismatched gene-id namespaces or annotations.", nrow(meta), min_genes)
            else ""
    msg <- sprintf("Meta-analysis over %d of %d studies (%s): %d shared genes; %d meta-DEGs at FDR<%.3g; %d study(ies) excluded.%s",
                   length(fanout$per_study), n_datasets, paste(names(fanout$per_study), collapse = ", "),
                   nrow(meta), n_sig, alpha, length(fanout$excluded), warn)
    status <- if (nrow(meta) == 0) "FAIL" else if (n_sig > 0) "PASS" else "REVIEW_REQUIRED"
  }
  esc <- function(s) gsub('"', '\\\\"', s)
  writeLines(sprintf('{\n  "check": "17_meta_analysis_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                     status, status, esc(msg)), snakemake@output[["meta_check"]])
  sink(type = "message"); close(log_con)
}
