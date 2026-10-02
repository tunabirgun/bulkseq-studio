source("workflow/scripts/run_meta_analysis.R")

assert <- function(condition, message) if (!isTRUE(condition)) stop(message)
flags <- commandArgs(trailingOnly = TRUE)
genes <- c("up", "opposite", "neutral", "missing_p", "missing_se")
mk <- function(effect, se, p) data.frame(row.names = genes, baseMean = rep(100, length(genes)),
  log2FoldChange = effect, lfcSE = se, pvalue = p,
  padj = stats::p.adjust(p, "BH"))
a <- mk(c(0.4, 1.1, 0, 0.3, 0.5), c(0.2, 0.4, 0.3, 0.2, NA),
        c(0.04, 0.01, 0.8, NA, 0.2))
b <- mk(c(1.1, -0.2, 0.5, 0.4, 0.6), c(0.4, 0.3, 0.3, 0.2, 0.2),
        c(0.01, 0.3, 0.2, 0.1, 0.2))
two <- combine_meta(list(A = a, B = b), c(A = 4, B = 4))
two <- two[match(c("up", "opposite", "neutral"), two$gene_id), ]
fe <- metafor::rma.uni(yi = c(0.4, 1.1), sei = c(0.2, 0.4), method = "FE")
inverse_variance <- sum(c(0.4, 1.1) / c(0.2, 0.4)^2) /
  sum(1 / c(0.2, 0.4)^2)
assert(abs(two$rem_log2FC[1] - inverse_variance) < 1e-12,
       "Fixed-effect estimate differs from inverse-variance closed form")
assert(abs(two$rem_log2FC[1] - as.numeric(fe$beta)) < 1e-12 &&
         abs(two$rem_ci_lo[1] - fe$ci.lb) < 1e-12 &&
         abs(two$rem_ci_hi[1] - fe$ci.ub) < 1e-12,
       "Fixed-effect estimate or interval differs from direct metafor")

c <- mk(c(0.6, 0.8, 0.4, 0.5, 0.7), rep(0.3, 5),
        c(0.03, 0.04, 0.2, 0.1, 0.1))
three <- combine_meta(list(A = a, B = b, C = c), c(A = 4, B = 4, C = 4))
dl <- metafor::rma.uni(yi = c(0.4, 1.1, 0.6), sei = c(0.2, 0.4, 0.3), method = "DL")
up <- three[three$gene_id == "up", ]
assert(abs(up$rem_log2FC - as.numeric(dl$beta)) < 1e-12 &&
         abs(up$rem_ci_lo - dl$ci.lb) < 1e-12 &&
         abs(up$tau2 - dl$tau2) < 1e-12 && abs(up$QEp - dl$QEp) < 1e-12,
       "Random-effect estimate or heterogeneity differs from direct metafor DL")

tested <- is.finite(two$rem_pvalue)
if ("--inject-old-family" %in% flags) {
  selected <- two$common_direction != "discordant"
  two$rem_padj[] <- NA_real_
  two$rem_padj[selected] <- stats::p.adjust(two$rem_pvalue[selected], "BH")
}
assert(isTRUE(all.equal(two$rem_padj[tested],
                        stats::p.adjust(two$rem_pvalue[tested], "BH"), tolerance = 1e-12)),
       "Pooled BH family must include all estimable effects, including opposite signs and zero")
assert(is.na(two$combined_padj[two$gene_id == "opposite"]) &&
         !two$meta_sig[two$gene_id == "opposite"],
       "Combined family and meta calls changed for opposite signs")

source_text <- readLines("workflow/scripts/run_meta_analysis.R", warn = FALSE)
needle <- '  rem_tested <- is.finite(rem[, "pval"])'
assert(sum(source_text == needle) == 1L, "Cannot construct prior-family comparison")
legacy_text <- source_text
legacy_text[legacy_text == needle] <- '  rem_tested <- conc & is.finite(rem[, "pval"])'
legacy_env <- new.env(parent = globalenv())
eval(parse(text = legacy_text), envir = legacy_env)
old <- legacy_env$combine_meta(list(A = a, B = b), c(A = 4, B = 4))
new <- combine_meta(list(A = a, B = b), c(A = 4, B = 4))
protected <- setdiff(names(new), "rem_padj")
if ("--inject-protected-column" %in% flags) new$rem_log2FC[1] <- new$rem_log2FC[1] + 0.1
assert(identical(old[protected], new[protected]),
       "A protected result column changed beyond pooled-effect adjustment")

samples <- data.frame(dataset = rep(c("A", "B"), each = 4),
                      group = rep(c("ctrl", "case"), 4))
ledger_a <- a
ledger_a["missing_p", "lfcSE"] <- NA_real_
fanout <- list(per_study = list(A = ledger_a, B = b), nrep = c(A = 4L, B = 4L),
               excluded = character())
ledger <- meta_eligibility(samples, "dataset", "group", "case", "ctrl", fanout, new,
                           input_count_rows = 8L)
assert(ledger$genes$identifier_intersection == 5L &&
         ledger$genes$original_count_rows == 8L &&
         ledger$genes$missing_pvalue == 1L && ledger$genes$missing_se == 2L &&
         ledger$genes$exclusion_union == 2L && ledger$genes$complete_case_retained == 3L,
       "Ledger missing-value and union counts do not reconcile")
assert(ledger$genes$direction$up == 1L &&
         ledger$genes$direction$opposite_sign == 1L &&
         ledger$genes$direction$neutral == 1L &&
         ledger$families$pooled$size == 3L && ledger$families$combined$size == 1L,
       "Ledger family or direction counts do not reconcile")
assert(supported_meta_design("~ dataset + group", "group") &&
         supported_meta_design("~ group + dataset", "group") &&
         !supported_meta_design("~ group + batch", "group") &&
         !supported_meta_design("~ group:dataset", "group") &&
         !supported_meta_design("~ log(group)", "group") &&
         !supported_meta_design("~ 0 + group", "group"),
       "Supported-design boundary changed")
bad_design <- tryCatch(per_study_deseq(matrix(1L, 1L, 1L), samples,
                                       "dataset", "group", "case", "ctrl",
                                       design = "~ group + subject"), error = identity)
assert(inherits(bad_design, "error") &&
         grepl("Unsupported per-study meta design", conditionMessage(bad_design), fixed = TRUE),
       "Unsupported design did not fail before DESeq2")
fixture_arg <- grep("^--fixture-dir=", flags, value = TRUE)
if (length(fixture_arg) == 1L) {
  fixture_dir <- sub("^--fixture-dir=", "", fixture_arg)
  dir.create(fixture_dir, recursive = TRUE, showWarnings = FALSE)
  write.csv(new, file.path(fixture_dir, "meta_analysis_results.csv"), row.names = FALSE)
  for (study in names(fanout$per_study)) {
    table <- fanout$per_study[[study]]
    table$gene_id <- rownames(table)
    write.csv(table, file.path(fixture_dir, paste0("per_study_", study, ".csv")),
              row.names = FALSE)
  }
  jsonlite::write_json(ledger, file.path(fixture_dir, "meta_eligibility.json"),
                       auto_unbox = TRUE, pretty = TRUE, null = "null")
}
cat("META_POOLED_METHODS_PASS\n")
print(utils::sessionInfo())
