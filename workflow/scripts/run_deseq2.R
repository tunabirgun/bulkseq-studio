# DESeq2 differential expression (protocol sections 7.1-7.4).
# Driven by the Snakemake `script:` directive via the `snakemake` S4 object.

# Shared engine helpers: load-warning muffling, write_check, GTF annotation, the
# featureCounts reader, contrast guards and the design-term detectors. Sourced before any
# library() call so the muffling is installed first.
source(file.path(snakemake@scriptdir, "de_common.R"))

suppressMessages({
  library(DESeq2)
})

# Reproducibility: seed any stochastic step (e.g. the ashr shrinkage fallback).
set.seed(42)

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

counts_file <- snakemake@input[["counts"]]
samples_file <- snakemake@input[["samples"]]
design_formula <- snakemake@params[["design"]]
ref_factor <- snakemake@params[["ref_factor"]]
ref_level <- snakemake@params[["ref_level"]]
con_factor <- snakemake@params[["contrast_factor"]]
numerator <- snakemake@params[["numerator"]]
denominator <- snakemake@params[["denominator"]]
alpha <- as.numeric(snakemake@params[["alpha"]])
lfc_thr <- as.numeric(snakemake@params[["lfc_threshold"]])
shrink_type <- snakemake@params[["shrink"]]
min_count <- tryCatch(as.integer(snakemake@params[["min_count"]]), error = function(e) NA_integer_)
if (length(min_count) != 1 || is.na(min_count) || min_count < 0) min_count <- 10L

# Effect-size threshold: >= 0 is valid (0 means no fold-change filter).
if (is.na(lfc_thr) || lfc_thr < 0) {
  stop("deseq2.lfc_threshold must be a number >= 0 (0 disables the fold-change filter).")
}

# ---- Import featureCounts matrix --------------------------------------------
cts <- read_featurecounts(counts_file)

# ---- Sample metadata --------------------------------------------------------
samples <- read.delim(samples_file, stringsAsFactors = FALSE)
rownames(samples) <- samples$sample_id
coldata <- samples[colnames(cts), , drop = FALSE]
stopifnot(all(rownames(coldata) == colnames(cts)))
# Guard: the contrast factor must be a real column in the sample sheet.
if (!nzchar(con_factor) || !(con_factor %in% colnames(coldata))) {
  stop(sprintf("Contrast factor '%s' is not a column in the sample sheet (columns: %s).",
               con_factor, paste(colnames(coldata), collapse = ", ")))
}
coldata[[con_factor]] <- factor(coldata[[con_factor]])
form_vars <- tryCatch(all.vars(as.formula(design_formula)), error = function(e) character(0))
covariates <- setdiff(form_vars, con_factor)
covariates <- covariates[covariates %in% colnames(coldata)]

coef_name <- paste0(con_factor, "_", numerator, "_vs_", denominator)
design_checks <- list()
full_rank <- TRUE
tryCatch({
  mm <- model.matrix(as.formula(design_formula), data = coldata)
  if (qr(mm)$rank < ncol(mm)) full_rank <- FALSE
}, error = function(e) { full_rank <<- FALSE })
design_checks[[1]] <- list(status = if (full_rank) "PASS" else "FAIL",
                           message = if (full_rank) sprintf("Design %s is full rank.", design_formula)
                                     else sprintf("Design %s is not full rank.", design_formula))
n_per_group <- table(coldata[[con_factor]])
if (min(n_per_group) < 2) {
  design_checks[[length(design_checks) + 1]] <- list(status = "WARNING",
    message = "At least one condition has fewer than two replicates.")
}
design_checks <- c(design_checks, numeric_covariate_checks(coldata, covariates))
# The contrast below tests one coefficient. Under an interaction or nesting design that
# coefficient is the simple effect at the reference level of the other factor(s), not the
# interaction itself, so say which coefficient was reported rather than affirm the design.
if (has_interaction(design_formula)) {
  design_checks[[length(design_checks) + 1]] <- list(
    status = "REVIEW_REQUIRED",
    message = sprintf(paste0(
      "Design %s contains an interaction or nesting operator. The reported result is the ",
      "coefficient '%s', i.e. the %s vs %s difference at the reference level of the other ",
      "design factor(s), not the interaction effect. To test the interaction term itself, ",
      "request it as an explicit contrast or coefficient."),
      design_formula, coef_name, numerator, denominator))
}
design_status <- if (!full_rank) "FAIL" else if (any(vapply(design_checks, function(m)
  identical(m$status, "REVIEW_REQUIRED"), logical(1)))) "REVIEW_REQUIRED" else "PASS"
write_check(snakemake@output[["design_check"]], "08_metadata_design_qc", design_status, design_checks)

# ---- DESeq2 -----------------------------------------------------------------
dds <- DESeqDataSetFromMatrix(countData = cts, colData = coldata,
                              design = as.formula(design_formula))
smallest_group <- min(table(coldata[[con_factor]]))
# Prefilter: keep genes with >= min_count reads in at least the smallest group
# (default 10; a light, recommended DESeq2 prefilter).
keep <- rowSums(counts(dds) >= min_count) >= smallest_group
dds <- dds[keep, ]
if (nzchar(ref_level) && ref_factor %in% colnames(coldata)) {
  dds[[ref_factor]] <- relevel(factor(dds[[ref_factor]]), ref = ref_level)
}
dds <- DESeq(dds)

# Guard: contrast levels must be set, distinct, and present in the factor.
check_contrast(numerator, denominator, levels(coldata[[con_factor]]), con_factor)
res <- results(dds, contrast = c(con_factor, numerator, denominator), alpha = alpha)
# lfcShrink(type=apeglm) can fail (e.g. apeglm needs a single model coefficient, which a
# multi-level contrast does not give it) and fall back to ashr inside this tryCatch. Track
# the REALISED method (and why), not just the requested shrink_type, so the run summary
# reports what actually ran (see the provenance note near sessionInfo() below).
shrink_method_used <- shrink_type
shrink_fallback_reason <- NA_character_
resLFC <- tryCatch(
  lfcShrink(dds, coef = coef_name, type = shrink_type),
  error = function(e) {
    shrink_method_used <<- "ashr"
    shrink_fallback_reason <<- conditionMessage(e)
    warning(sprintf("lfcShrink type='%s' failed (%s); falling back to ashr.",
                    shrink_type, conditionMessage(e)))
    lfcShrink(dds, contrast = c(con_factor, numerator, denominator), type = "ashr")
  })

vsd <- tryCatch(vst(dds, blind = FALSE), error = function(e) rlog(dds, blind = FALSE))

# PC1/PC2 coordinates for the covariate-structure screen (check 23): same ntop default
# make_figures.R uses for the PCA plot. The alternative engines write the same three columns
# from their own log matrix via de_common.R's write_pca_coordinates().
pca_coords <- plotPCA(vsd, intgroup = con_factor, ntop = 500, returnData = TRUE)
pca_out <- data.frame(sample_id = rownames(pca_coords), PC1 = pca_coords$PC1, PC2 = pca_coords$PC2)
write.csv(pca_out, snakemake@output[["pca_coordinates"]], row.names = FALSE)

# ---- Gene annotation (symbol + biotype from the GTF) ------------------------
# Adds human-readable columns to the results CSV and a gene_id->symbol map the
# figure/GOI scripts use for labels. Behaviour-preserving: DE statistics, row
# order, and the up/down cutoffs below are unchanged.
gtf_path <- tryCatch(snakemake@params[["gtf"]], error = function(e) NULL)
annot <- annotate_from_gtf(gtf_path, rownames(res))

# Informational companion to the primary padj/|LFC| call above: H0: |LFC| <= L,
# so padj < alpha is positive evidence the effect EXCEEDS the threshold. Does
# not feed the up/down classification below; lfc_thr may be 0, so fall back to
# 1.0 (a positive threshold is required by greaterAbs, same as the lessAbs
# equivalence test further down).
L_eq <- require_lfc_threshold(if (lfc_thr > 0) lfc_thr else 1.0)
res_greater <- results(dds, contrast = c(con_factor, numerator, denominator),
                       lfcThreshold = L_eq, altHypothesis = "greaterAbs", alpha = alpha)

# ---- Outputs ----------------------------------------------------------------
res_out <- as.data.frame(res)
res_out$gene_id <- rownames(res_out)
res_out$symbol <- unname(annot$symbol[rownames(res_out)])
res_out$biotype <- unname(annot$biotype[rownames(res_out)])
res_out$ncbi_geneid <- unname(annot$geneid[rownames(res_out)])
res_out$padj_lfc_ge_threshold <- res_greater$padj[match(res_out$gene_id, rownames(res_greater))]
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)
write.csv(as.data.frame(counts(dds, normalized = TRUE)), snakemake@output[["normalized"]])
saveRDS(list(dds = dds, res = res, resLFC = resLFC, vsd = vsd,
             lfc_threshold_test = "greaterAbs", symbol_map = annot$symbol),
        snakemake@output[["rds"]])

# Up- and down-regulated sets: padj < alpha AND a raw-LFC effect-size cut
# (protocol: threshold on padj + res$log2FoldChange, not the shrunken values).
sig <- !is.na(res_out$padj) & res_out$padj < alpha
up <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange >= lfc_thr, ]
down <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange <= -lfc_thr, ]
up <- up[order(-up$log2FoldChange), ]
down <- down[order(down$log2FoldChange), ]
write.csv(up, snakemake@output[["up"]], row.names = FALSE)
write.csv(down, snakemake@output[["down"]], row.names = FALSE)

n_sig <- sum(sig)
deseq_checks <- list(list(status = if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
                          message = sprintf("%d genes padj < %.3g (%s); %d up / %d down at |log2FC| >= %.2g.",
                                            n_sig, alpha, coef_name, nrow(up), nrow(down), lfc_thr)))
write_check(snakemake@output[["deseq_check"]], "09_deseq2_qc",
            if (n_sig > 0) "PASS" else "REVIEW_REQUIRED", deseq_checks)

# ---- Equivalence / no-change test (TOST-style) ------------------------------
# results(altHypothesis="lessAbs") tests H0: |LFC| >= L; padj < alpha is positive
# evidence the gene's effect is SMALLER than L (not differentially expressed),
# complementing the usual "is it different" test. L_eq is computed above,
# alongside the greaterAbs companion test written into res_out.
res_eq <- results(dds, contrast = c(con_factor, numerator, denominator),
                  lfcThreshold = L_eq, altHypothesis = "lessAbs", alpha = alpha)
eq_out <- as.data.frame(res_eq)
eq_out$gene_id <- rownames(eq_out)
eq_out$symbol <- unname(annot$symbol[rownames(eq_out)])
eq_out <- eq_out[!is.na(eq_out$padj) & eq_out$padj < alpha, ]
eq_out <- eq_out[order(eq_out$padj), ]
write.csv(eq_out, snakemake@output[["unchanged"]], row.names = FALSE)
write_check(snakemake@output[["equivalence_check"]], "13_equivalence_qc", "PASS",
            list(list(status = "PASS",
              message = sprintf("%d genes equivalent to no change (|log2FC| < %.2g at padj < %.3g, TOST lessAbs).",
                                nrow(eq_out), L_eq, alpha))))

# Provenance line make_run_summary.py parses to report the REALISED shrinkage method
# (config only records what was requested; a silent apeglm -> ashr fallback above would
# otherwise be invisible to anyone trying to reproduce the run).
shrink_line <- if (identical(shrink_method_used, shrink_type)) {
  sprintf("Shrinkage method used: %s (as requested)", shrink_method_used)
} else {
  sprintf("Shrinkage method used: %s (requested '%s' failed and fell back: %s)",
          shrink_method_used, shrink_type, shrink_fallback_reason)
}
lfc_test_line <- sprintf("Fold-change threshold test: greaterAbs (H0 |log2FC| <= %g)", L_eq)
writeLines(c(shrink_line, lfc_test_line, "", capture.output(sessionInfo())),
           snakemake@output[["session"]])
sink(type = "message")
close(log_con)
