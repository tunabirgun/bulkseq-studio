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

save_corr <- function(method, png_path, svg_path, csv_path) {
  cm <- cor(m, method = method)
  write.csv(cm, csv_path)
  ph <- pheatmap(cm, clustering_method = "ward.D2", display_numbers = TRUE,
                 annotation_col = ann, fontsize = base_size,
                 color = colorRampPalette(rev(brewer.pal(9, "RdBu")))(255),
                 silent = TRUE)
  png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi)
  grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
  svglite(svg_path, width = fig_w, height = fig_h)
  grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
}

save_corr("pearson", out[["pearson_png"]], out[["pearson_svg"]], out[["pearson_csv"]])
save_corr("spearman", out[["spearman_png"]], out[["spearman_svg"]], out[["spearman_csv"]])

sink(type = "message")
close(log_con)
