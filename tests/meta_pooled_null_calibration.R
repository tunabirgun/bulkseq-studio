source("workflow/scripts/run_meta_analysis.R")

set.seed(20261002)
n_experiments <- 10000L
n_genes <- 100L
alpha <- 0.05
z1 <- matrix(stats::rnorm(n_experiments * n_genes), nrow = n_experiments)
z2 <- matrix(stats::rnorm(n_experiments * n_genes), nrow = n_experiments)
effect_p <- 2 * stats::pnorm(abs((z1 + z2) / sqrt(2)), lower.tail = FALSE)
concordant <- sign(z1) == sign(z2)
bh_any <- function(p) {
  n <- length(p)
  n > 0L && any(sort(p) <= alpha * seq_len(n) / n)
}
all_family <- vapply(seq_len(n_experiments), function(i) bh_any(effect_p[i, ]), logical(1))
old_family <- vapply(seq_len(n_experiments), function(i)
  bh_any(effect_p[i, concordant[i, ]]), logical(1))
all_rate <- mean(all_family)
old_rate <- mean(old_family)
if ("--inject-old-family" %in% commandArgs(trailingOnly = TRUE)) all_rate <- old_rate
if (!(all_rate >= 0.04 && all_rate <= 0.06 &&
      old_rate >= 0.08 && old_rate <= 0.12 && old_rate - all_rate >= 0.03))
  stop(sprintf("Null calibration failed: complete %.5f; selected %.5f", all_rate, old_rate))
cat(sprintf("NULL_CALIBRATION_PASS: complete %.5f; selected %.5f; %d null experiments\n",
            all_rate, old_rate, n_experiments))
print(utils::sessionInfo())
