# Wilcoxon rank-sum per-gene SENSITIVITY / concordance diagnostic (0.6.0).
# A non-parametric cross-check on the DESeq2/limma calls. NOT a DEG caller: at
# small n per group (e.g. 3v3) the exact two-sided p cannot reach 0.05, so this
# is reported as a rank-concordance diagnostic and a small-n warning, never as
# thresholded significance. Backend-agnostic (reads assay(vsd) + the contrast).

suppressMessages({
  library(SummarizedExperiment)
  library(ggplot2)
  library(svglite)
  library(scales)
  library(RColorBrewer)
})

# Shared palette/theme/getp/save_gg helpers (sourced; resolved via scriptdir).
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
vsd <- obj$vsd
m <- SummarizedExperiment::assay(vsd)
out <- snakemake@output
factor_name <- snakemake@params[["factor"]]
num <- snakemake@params[["numerator"]]
den <- snakemake@params[["denominator"]]

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style)
fig_w <- as.numeric(getp("width_in", 6)); fig_h <- as.numeric(getp("height_in", 5))
fig_dpi <- as.integer(getp("dpi", 300)); base_size <- as.numeric(getp("base_font_size", 12))
font_family <- as.character(getp("font_family", ""))
label_bold <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold <- isTRUE(as.logical(getp("title_bold", FALSE)))
palette_name <- as.character(getp("palette", "Blue-Red"))
pal_spec <- palette_spec(palette_name)
base_family <- if (nzchar(font_family)) font_family else NULL
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = label_bold, title_bold = title_bold)
save_gg <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "14_wilcoxon_sensitivity",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}

cd <- as.data.frame(SummarizedExperiment::colData(vsd))
if (!(factor_name %in% colnames(cd))) factor_name <- colnames(cd)[1]
grp <- as.character(cd[[factor_name]])
lv <- unique(grp)
# Fall back to the two most-populated levels if the configured contrast is unusable.
if (!(num %in% lv) || !(den %in% lv) || identical(num, den)) {
  tab <- sort(table(grp), decreasing = TRUE)
  num <- names(tab)[1]; den <- names(tab)[2]
}
i_num <- which(grp == num); i_den <- which(grp == den)
n_min <- min(length(i_num), length(i_den))

# Per-gene two-sided Wilcoxon; skip constant/all-tie genes (NA), muffle and count
# the "cannot compute exact p-value with ties" warnings instead of spamming logs.
ties <- 0
pvals <- withCallingHandlers(
  apply(m, 1, function(x) {
    a <- x[i_num]; b <- x[i_den]
    # Drop NA/Inf per group; a gene with an entirely-missing group (common on the
    # microarray intensity matrix) must return NA, not crash the whole rule.
    a <- a[is.finite(a)]; b <- b[is.finite(b)]
    if (length(a) < 1 || length(b) < 1 || length(unique(c(a, b))) < 2) return(NA_real_)
    wilcox.test(a, b)$p.value
  }),
  warning = function(w) {
    if (grepl("ties", conditionMessage(w))) { ties <<- ties + 1; invokeRestart("muffleWarning") }
  })
ok <- !is.na(pvals)
padj <- rep(NA_real_, length(pvals))
padj[ok] <- p.adjust(pvals[ok], method = "BH")

res <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE, check.names = FALSE)
df <- data.frame(gene_id = rownames(m),
                 symbol = res$symbol[match(rownames(m), res$gene_id)],
                 wilcox_p = pvals, wilcox_padj = padj,
                 de_stat = res$stat[match(rownames(m), res$gene_id)],
                 stringsAsFactors = FALSE)
df <- df[order(df$wilcox_padj), ]
write.csv(df, out[["csv"]], row.names = FALSE)

# Concordance: DE statistic vs Wilcoxon evidence (NOT thresholded calls). The
# thousands of genes overplot as a black smear, so bin the density (gene count per
# 2D cell on a log fill) rather than jitter (banding is intrinsic at small n).
pd <- df[!is.na(df$wilcox_p) & !is.na(df$de_stat), ]
pd$neglp <- -log10(pd$wilcox_p)
p <- ggplot(pd, aes(de_stat, neglp)) +
  geom_bin2d(bins = 60) +
  scale_fill_gradientn(colours = pal_spec$seq(256), transform = "log10", name = "genes") +
  labs(x = "DESeq2 / limma statistic",
       y = expression(-log[10]~"Wilcoxon rank-sum p")) +
  style_theme(theme_bw)
save_gg(p, out[["png"]], out[["svg"]])

small <- n_min < 5
status <- if (small) "WARNING" else "PASS"
msg <- sprintf("Wilcoxon sensitivity DIAGNOSTIC (not for calling DEGs): %s vs %s, n=%d/%d; %d genes tested, %d skipped (constant), %d with ties.%s",
               num, den, length(i_num), length(i_den), sum(ok), sum(!ok), ties,
               if (small) sprintf(" Small n/group (%d): Wilcoxon is underpowered here (exact p cannot reach 0.05); use as a rank-concordance check only.", n_min) else "")
write_check(out[["check"]], status, msg)

sink(type = "message")
close(log_con)
