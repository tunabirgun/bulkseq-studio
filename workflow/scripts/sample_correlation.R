# Sample-sample correlation matrix + hierarchical clustering (Pearson and
# Spearman) from the normalized expression matrix in deseq2_objects.rds. Both
# backends (VST counts / log2 intensity); organism-agnostic; no new dependency.

suppressMessages({
  library(SummarizedExperiment)
  library(pheatmap)
  library(svglite)
  library(RColorBrewer)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
vsd <- obj$vsd
out <- snakemake@output
m <- SummarizedExperiment::assay(vsd)

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- function(k, d) { v <- style[[k]]; if (is.null(v)) d else v }
fig_w <- as.numeric(getp("width_in", 6))
fig_h <- as.numeric(getp("height_in", 5))
fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(getp("base_font_size", 12))

# Annotate columns by the contrast factor (falls back to the first colData column).
group_var <- "condition"
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1 && !is.null(cons[[1]][["factor"]])) {
    group_var <- as.character(cons[[1]][["factor"]])
  }
}
cd <- as.data.frame(SummarizedExperiment::colData(vsd))
if (!(group_var %in% colnames(cd))) group_var <- colnames(cd)[1]
ann <- cd[, group_var, drop = FALSE]

placeholder <- function(png_path, svg_path, msg) {
  draw <- function() { plot.new(); text(0.5, 0.5, msg, cex = 1.1) }
  png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi); draw(); dev.off()
  svglite(svg_path, width = fig_w, height = fig_h); draw(); dev.off()
}
save_corr <- function(method, png_path, svg_path, csv_path) {
  # Best-effort: an intensity matrix with NA (e.g. microarray log2 of non-positive
  # values) must not abort the whole run. Use pairwise-complete correlation, skip
  # hclust when the matrix still has NA, and degrade to a placeholder on any error.
  ok <- tryCatch({
    cm <- cor(m, method = method, use = "pairwise.complete.obs")
    write.csv(cm, csv_path)
    cluster <- !anyNA(cm)  # hclust cannot handle NA distances
    ph <- pheatmap(cm, clustering_method = "ward.D2",
                   cluster_rows = cluster, cluster_cols = cluster,
                   display_numbers = TRUE, annotation_col = ann, fontsize = base_size,
                   color = colorRampPalette(rev(brewer.pal(9, "RdBu")))(255), silent = TRUE)
    png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi)
    grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
    svglite(svg_path, width = fig_w, height = fig_h)
    grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
    TRUE
  }, error = function(e) { message("sample_correlation (", method, ") failed: ", conditionMessage(e)); FALSE })
  if (!isTRUE(ok)) {
    if (!file.exists(csv_path)) tryCatch(writeLines("", csv_path), error = function(e) NULL)
    placeholder(png_path, svg_path, paste("Correlation unavailable:", method))
  }
}

save_corr("pearson", out[["pearson_png"]], out[["pearson_svg"]], out[["pearson_csv"]])
save_corr("spearman", out[["spearman_png"]], out[["spearman_svg"]], out[["spearman_csv"]])

sink(type = "message")
close(log_con)
