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

cat(if (ok) "ALL_META_TESTS_PASS\n" else "META_TESTS_FAILED\n")
