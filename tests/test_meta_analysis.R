# Validates workflow/scripts/run_meta_analysis.R against known metaRNASeq values + synthetic data.
# Run in the bulkseq env: micromamba run -n bulkseq Rscript tests/test_meta_analysis.R
suppressMessages(source("workflow/scripts/run_meta_analysis.R"))
ok <- TRUE
check <- function(cond, msg) {
  if (!isTRUE(cond)) { cat("FAIL:", msg, "\n"); ok <<- FALSE } else cat("ok:", msg, "\n")
}

# ---- combine_meta vs golden metaRNASeq::invnorm (nrep=c(3,3)) ----
mk <- function(p, lfc) data.frame(row.names = paste0("g", 1:5), baseMean = rep(100, 5),
  log2FoldChange = lfc, lfcSE = rep(0.3, 5), pvalue = p, padj = p.adjust(p, "BH"))
A <- mk(c(0.001, 0.20, 0.80, 1e-30, 0.50), c(2.0, 1.0, -0.5, 3.0,  0.2))
B <- mk(c(0.002, 0.30, 0.90, 1e-20, 0.60), c(2.1, 1.1, -0.4, 3.2, -0.3))  # g5 sign-discordant
res <- combine_meta(list(A = A, B = B), c(A = 3, B = 3), alpha = 0.05)
rownames(res) <- res$gene_id
check(abs(res["g1", "combined_pvalue"] - 0.000012) < 1e-5, "g1 combined p ~ 1.2e-5 (golden)")
check(abs(res["g3", "combined_pvalue"] - 0.933362) < 1e-4, "g3 combined p ~ 0.933 (golden)")
check(res["g5", "common_direction"] == "discordant", "g5 flagged discordant (not dropped)")
check("g5" %in% res$gene_id, "g5 row still present (searchable)")
check(isFALSE(res["g5", "meta_sig"]), "g5 not called a meta-DEG")
check(res["g1", "common_direction"] == "up", "g1 concordant up")
check(all(c("rem_log2FC", "tau2", "I2", "combined_padj", "n_studies_sig") %in% colnames(res)),
      "meta table has the expected columns")
check(all(is.na(res$tau2)), "k=2 heterogeneity NA (fixed-effect)")

# ---- p-clamp: exact-0 p-value must not corrupt the combined statistic ----
A0 <- mk(c(0, 0.5, 0.5, 0.5, 0.5), c(2, 1, 1, 1, 1))
B0 <- mk(c(0, 0.5, 0.5, 0.5, 0.5), c(2, 1, 1, 1, 1))
r0 <- combine_meta(list(A = A0, B = B0), c(A = 3, B = 3))
check(is.finite(r0$combined_pvalue[1]) && r0$combined_pvalue[1] >= 0, "exact-0 p clamped, finite combined p")

# ---- tail resolution: the combined p must stay rankable past |Z| ~ 8.29 -------------------------
# metaRNASeq::invnorm evaluates qnorm(1 - p) and 1 - pnorm(statc). 1 - 1e-30 and 1 - 1e-18 are both
# exactly 1 in double precision, so every gene past |Z| ~ 8.29 collapsed to a combined p of exactly
# 0: the strongest meta-DEGs were tied at the floor and unrankable, and -log10 of the FDR was Inf.
# The tail-form statistic separates them. Own fixture (the golden one's BH family must not move).
local({
  mk2 <- function(p) data.frame(row.names = c("strong", "weaker", "null"),
    log2FoldChange = c(3, 3, 0.1), lfcSE = rep(0.3, 3), pvalue = p, padj = p.adjust(p, "BH"))
  r <- combine_meta(list(A = mk2(c(1e-30, 1e-18, 0.5)), B = mk2(c(1e-20, 1e-18, 0.6))),
                    c(A = 3, B = 3))
  rownames(r) <- r$gene_id
  check(all(r$combined_pvalue > 0), "tail: no combined p underflows to exactly 0")
  check(r["strong", "combined_pvalue"] < r["weaker", "combined_pvalue"],
        "tail: 1e-30/1e-20 ranks strictly above 1e-18/1e-18")
  check(all(is.finite(r$combined_z)) && !any(r$combined_z_offscale),
        "tail: combined_z finite for every gene")
})

# ---- hyphenated study name survives the results-CSV round trip ---------------------------------
# A dataset may legitimately be named E-MTAB-2523 (the metadata gate admits [A-Za-z0-9_.-]). The
# meta engine writes study_E-MTAB-2523_log2FC; read.csv()'s default check.names = TRUE rewrites it
# to study_E.MTAB.2523_log2FC and every downstream lookup then misses silently. Mirror the exact
# write -> read -> derive -> per_study_<S>.csv chain that make_meta_figures.R walks.
local({
  s_name <- "E-MTAB-2523"
  d <- data.frame(gene_id = "g1", combined_pvalue = 0.01, check.names = FALSE, stringsAsFactors = FALSE)
  d[[paste0("study_", s_name, "_log2FC")]] <- 1.5
  d[[paste0("study_", s_name, "_padj")]] <- 0.02
  dir <- tempfile(); dir.create(dir); on.exit(unlink(dir, recursive = TRUE), add = TRUE)
  f <- file.path(dir, "meta_analysis_results.csv")
  write.csv(d, f, row.names = FALSE)
  write.csv(data.frame(gene_id = "g1", log2FoldChange = 1.5, lfcSE = 0.3, pvalue = 0.01, padj = 0.02),
            file.path(dir, paste0("per_study_", s_name, ".csv")), row.names = FALSE)

  back <- read.csv(f, stringsAsFactors = FALSE, check.names = FALSE)
  cols <- grep("^study_.*_log2FC$", colnames(back), value = TRUE)
  got <- sub("^study_(.*)_log2FC$", "\\1", cols)
  check(identical(got, s_name), "hyphenated study name survives the results-CSV read")
  check(file.exists(file.path(dir, paste0("per_study_", got, ".csv"))),
        "per_study_<S>.csv resolves from the column-derived study name")
  check(!is.null(back[[paste0("study_", s_name, "_padj")]]),
        "study_<S>_padj column resolves by name")
})

# ---- per_study_deseq on synthetic balanced 2-study counts ----
set.seed(1)
ng <- 400; ns <- 8
cts <- matrix(rnbinom(ng * ns, mu = 300, size = 12), ng, ns,
              dimnames = list(paste0("ENSG", 1:ng), paste0("S", 1:ns)))
trt <- c(2, 4, 6, 8)
cts[1:30, trt] <- as.integer(cts[1:30, trt] * 5)   # inject strong DE in 30 genes
samples <- data.frame(sample_id = paste0("S", 1:ns),
  condition = rep(c("ctrl", "trt"), ns / 2),
  dataset = rep(c("D1", "D2"), each = ns / 2), stringsAsFactors = FALSE)
fan <- per_study_deseq(cts, samples, "dataset", "condition", "trt", "ctrl")
check(length(fan$per_study) == 2, "two studies analysed")
check(all(fan$nrep == 4), "nrep = 4 per study")
check(length(fan$excluded) == 0, "no studies excluded (balanced)")
meta <- combine_meta(fan$per_study, fan$nrep, 0.05)
check(sum(meta$meta_sig, na.rm = TRUE) >= 10, "recovers injected DE genes via meta")

# ---- single-arm study is dropped, not analysed ----
s2 <- samples; s2$condition[s2$dataset == "D2"] <- "trt"   # D2 has only 'trt'
fan2 <- per_study_deseq(cts, s2, "dataset", "condition", "trt", "ctrl")
check(length(fan2$per_study) == 1 && length(fan2$excluded) == 1, "single-arm study D2 dropped + reported")

# ---- robustness: a study with all-NA p collapses the combinable set -> empty df, not a crash ----
An <- mk(c(0.01, 0.02, 0.03, 0.04, 0.05), rep(1, 5))
Bn <- mk(rep(NA_real_, 5), rep(1, 5))
r_empty <- tryCatch(combine_meta(list(A = An, B = Bn), c(A = 3, B = 3)), error = function(e) e)
check(is.data.frame(r_empty) && nrow(r_empty) == 0, "all-NA study -> empty result, no crash")

# ---- multiplicity family: discordant genes must not inflate the threshold ----------------------
# Benjamini-Hochberg was applied across every gene and the direction filter applied to the result,
# so the reported set was a rejection set minus a post-hoc subset and the guarantee did not
# transfer. Discordant genes crowd the low tail -- strong opposing per-study effects give a small
# combined p -- so they raised the cutoff and admitted null genes that survived the filter. B19's
# discordance arm reported 58 such genes across five runs, every one a false positive.
#
# Here every gene carrying signal is discordant and the rest are null, so a correct implementation
# calls nothing: it adjusts within the concordant family, which contains no signal.
local({
  set.seed(11)
  n <- 4000L; n_disc <- 800L
  p1 <- runif(n); p2 <- runif(n)
  l1 <- rnorm(n); l2 <- rnorm(n)
  idx <- seq_len(n_disc)
  p1[idx] <- runif(n_disc, 1e-12, 1e-9); p2[idx] <- runif(n_disc, 1e-12, 1e-9)
  l1[idx] <- 3; l2[idx] <- -3
  mkd <- function(p, l) data.frame(row.names = paste0("g", seq_len(n)),
    log2FoldChange = l, lfcSE = rep(0.2, n), pvalue = p, padj = p.adjust(p, "BH"))
  res <- combine_meta(list(A = mkd(p1, l1), B = mkd(p2, l2)), c(A = 5L, B = 5L), alpha = 0.05)
  disc <- res$common_direction == "discordant"

  check(sum(disc) >= n_disc * 0.9, "discordance: planted opposite-sign genes flagged")
  check(!any(res$meta_sig[disc]), "discordance: no discordant gene called")
  check(all(is.na(res$combined_padj[disc])),
        "discordance: discordant genes carry no adjusted p (outside the tested family)")
  check(!any(is.na(res$combined_pvalue[disc])),
        "discordance: discordant genes keep a searchable raw combined p")
  called <- sum(res$meta_sig)
  # 800 discordant rejections set the old cutoff, so the pre-fix implementation admits on the
  # order of alpha x 800 = 40 null genes here; the corrected one admits ~0. A bound of 5 sits far
  # from both and is crossed only by a genuine regression. The earlier bound was also 5 but the
  # fixture planted 200, which put the defective behaviour at ~3 -- under the bound, so the check
  # passed on the bug it was written for.
  check(called <= 5L, sprintf(
    "discordance: %d meta-DEG(s) from a null concordant background (expect ~0 corrected, ~%d if the multiplicity correction is applied before the direction filter)",
    called, as.integer(0.05 * n_disc)))
})

# ---- rem_pvalue carries its own correction ----------------------------------------------------
# The pooled effect-size test is a different hypothesis from the combined p-value, so it was
# exported raw and could be read as if it were adjusted. Its family is the concordant genes whose
# pooled effect metafor could estimate.
local({
  set.seed(3)
  n <- 500L
  mkr <- function(p, l) data.frame(row.names = paste0("g", seq_len(n)),
    log2FoldChange = l, lfcSE = rep(0.2, n), pvalue = p, padj = p.adjust(p, "BH"))
  l1 <- rnorm(n); l2 <- l1 + rnorm(n, sd = 0.05)
  l2[1:50] <- -l1[1:50]                                    # a discordant block
  r <- combine_meta(list(A = mkr(runif(n), l1), B = mkr(runif(n), l2)), c(A = 5L, B = 5L))
  check("rem_padj" %in% colnames(r), "rem_padj is exported next to rem_pvalue")
  disc <- r$common_direction == "discordant"
  check(all(is.na(r$rem_padj[disc])), "rem_padj: discordant genes are outside the tested family")
  tested <- !disc & is.finite(r$rem_pvalue)
  check(any(tested), "rem_padj: the tested family is non-empty in this fixture")
  check(all(r$rem_padj[tested] >= r$rem_pvalue[tested] - 1e-12),
        "rem_padj: adjustment never reports a value below its raw p")
  check(isTRUE(all.equal(r$rem_padj[tested],
                         p.adjust(r$rem_pvalue[tested], method = "BH"), tolerance = 1e-12)),
        "rem_padj: BH computed over the tested family only (not the whole table)")
})

cat(if (ok) "ALL_META_TESTS_PASS\n" else "META_TESTS_FAILED\n")
