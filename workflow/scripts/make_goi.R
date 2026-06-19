# Genes-of-interest figures (protocol sections 7.6 + 9.6 focused heatmap):
# a z-scored heatmap of a user-supplied gene list and a per-gene normalised-count
# comparison across conditions. Gene IDs are matched to the count-matrix rownames.

suppressMessages({
  library(DESeq2)
  library(ggplot2)
  library(pheatmap)
  library(RColorBrewer)
  library(svglite)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
dds <- obj$dds; vsd <- obj$vsd
out <- snakemake@output

style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
base_size <- tryCatch(as.numeric(style[["base_font_size"]]), error = function(e) 12)
if (length(base_size) != 1 || is.na(base_size)) base_size <- 12

group_var <- "condition"
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1 && !is.null(cons[[1]][["factor"]])) group_var <- as.character(cons[[1]][["factor"]])
}
if (!(group_var %in% colnames(colData(dds)))) group_var <- colnames(colData(dds))[1]

goi <- readLines(snakemake@input[["genes"]], warn = FALSE)
goi <- trimws(goi)
goi <- goi[nzchar(goi) & !startsWith(goi, "#")]
goi <- sub("\\..*$", "", goi)
rn <- sub("\\..*$", "", rownames(vsd))
idx <- match(goi, rn)
present <- idx[!is.na(idx)]
missing <- goi[is.na(idx)]

writeLines(c(
  sprintf("Genes of interest: %d requested, %d found, %d not matched.", length(goi), length(present), length(missing)),
  if (length(missing)) paste("Not matched:", paste(head(missing, 100), collapse = ", ")) else "All matched."),
  out[["report"]])

save_gg <- function(p, png_path, svg_path, w = 7, h = 5) {
  ggsave(png_path, p, width = w, height = h, dpi = 300)
  ggsave(svg_path, p, width = w, height = h)
}

if (length(present) < 1) {
  msg <- ggplot() + annotate("text", x = 0, y = 0, label = "No genes of interest matched the count matrix") + theme_void()
  save_gg(msg, out[["heatmap_png"]], out[["heatmap_svg"]])
  save_gg(msg, out[["expr_png"]], out[["expr_svg"]])
  writeLines("gene,note", out[["csv"]])
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}

# ---- Focused heatmap (z-scored VST) ----------------------------------------
mat <- assay(vsd)[present, , drop = FALSE]
rownames(mat) <- rownames(vsd)[present]
if (length(present) > 1) mat <- t(scale(t(mat)))
ann <- as.data.frame(colData(dds)[, group_var, drop = FALSE])
ph <- pheatmap(mat, scale = "none", annotation_col = ann, show_rownames = TRUE,
               cluster_rows = length(present) > 1,
               clustering_method = "ward.D2", fontsize = base_size, fontsize_row = 8,
               color = colorRampPalette(c("#2C7BB6", "white", "#C0392B"))(255),
               border_color = NA, silent = TRUE)
hh <- max(4, 0.25 * length(present) + 2)
png(out[["heatmap_png"]], width = 7, height = hh, units = "in", res = 300)
grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
svglite(out[["heatmap_svg"]], width = 7, height = hh)
grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()

# ---- Per-gene expression comparison across conditions -----------------------
nc <- counts(dds, normalized = TRUE)[present, , drop = FALSE]
rownames(nc) <- rownames(vsd)[present]
groups <- as.character(colData(dds)[[group_var]])
long <- do.call(rbind, lapply(seq_len(nrow(nc)), function(i) {
  data.frame(gene = rownames(nc)[i], sample = colnames(nc), count = as.numeric(nc[i, ]),
             group = groups, stringsAsFactors = FALSE)
}))
p_expr <- ggplot(long, aes(group, count, colour = group)) +
  geom_boxplot(outlier.shape = NA, alpha = 0.4) +
  geom_jitter(width = 0.15, size = 1.6) +
  facet_wrap(~ gene, scales = "free_y") +
  scale_y_log10() +
  labs(x = NULL, y = "normalised counts (log scale)") +
  theme_bw(base_size = base_size) +
  theme(legend.position = "none", axis.text.x = element_text(angle = 30, hjust = 1))
n_facet <- length(present)
save_gg(p_expr, out[["expr_png"]], out[["expr_svg"]],
        w = min(12, 3 * ceiling(sqrt(n_facet))), h = min(12, 2.5 * ceiling(n_facet / ceiling(sqrt(n_facet)))))

write.csv(as.data.frame(nc), out[["csv"]])
sink(type = "message"); close(log_con)
