# Transcriptomics figures (protocol section 9). Each figure is written as PNG
# (raster, dpi 300) and SVG (vector). Titles are omitted (captions live in text).

suppressMessages({
  library(DESeq2)
  library(ggplot2)
  library(ggrepel)
  library(pheatmap)
  library(RColorBrewer)
  library(svglite)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
dds <- obj$dds; res <- obj$res; resLFC <- obj$resLFC; vsd <- obj$vsd
out <- snakemake@output

save_gg <- function(plot, png_path, svg_path, w = 6, h = 5) {
  ggsave(png_path, plot, width = w, height = h, dpi = 300)
  ggsave(svg_path, plot, width = w, height = h)
}

save_grid <- function(gtable, png_path, svg_path, w = 6, h = 5) {
  png(png_path, width = w, height = h, units = "in", res = 300)
  grid::grid.newpage(); grid::grid.draw(gtable); dev.off()
  svglite(svg_path, width = w, height = h)
  grid::grid.newpage(); grid::grid.draw(gtable); dev.off()
}

# ---- PCA --------------------------------------------------------------------
pca <- plotPCA(vsd, intgroup = "condition", ntop = 500, returnData = TRUE)
pv <- round(100 * attr(pca, "percentVar"))
p_pca <- ggplot(pca, aes(PC1, PC2, colour = condition)) +
  geom_point(size = 3, alpha = 0.9) +
  geom_text_repel(aes(label = name), size = 3, seed = 1, show.legend = FALSE) +
  labs(x = paste0("PC1 (", pv[1], "%)"), y = paste0("PC2 (", pv[2], "%)")) +
  coord_fixed() + theme_bw(base_size = 12)
save_gg(p_pca, out[["pca_png"]], out[["pca_svg"]])

# ---- Sample-distance heatmap -----------------------------------------------
sampleDists <- dist(t(assay(vsd)), method = "euclidean")
mat <- as.matrix(sampleDists)
rownames(mat) <- colnames(mat) <- paste(vsd$condition, colnames(vsd), sep = " | ")
cols <- colorRampPalette(rev(brewer.pal(9, "Greys")))(255)
ph <- pheatmap(mat, clustering_distance_rows = sampleDists,
               clustering_distance_cols = sampleDists,
               clustering_method = "ward.D2", col = cols, silent = TRUE)
save_grid(ph$gtable, out[["dist_png"]], out[["dist_svg"]])

# ---- MA plot ----------------------------------------------------------------
ma <- as.data.frame(resLFC)
ma <- ma[!is.na(ma$padj), ]
ma$sig <- ma$padj < 0.05
p_ma <- ggplot(ma, aes(baseMean, log2FoldChange, colour = sig)) +
  geom_point(size = 1, alpha = 0.6) +
  geom_hline(yintercept = 0, colour = "grey40") +
  scale_x_log10() +
  scale_colour_manual(values = c("FALSE" = "grey75", "TRUE" = "black"), name = "padj < 0.05") +
  labs(x = "mean of normalised counts", y = "log2 fold change") +
  theme_bw(base_size = 12)
save_gg(p_ma, out[["ma_png"]], out[["ma_svg"]])

# ---- Volcano ----------------------------------------------------------------
vol <- as.data.frame(resLFC)
vol$gene <- rownames(vol)
vol <- vol[!is.na(vol$padj), ]
vol$neglog10padj <- -log10(vol$padj)
vol$direction <- "n.s."
vol$direction[vol$padj < 0.05 & vol$log2FoldChange >= 1] <- "Up"
vol$direction[vol$padj < 0.05 & vol$log2FoldChange <= -1] <- "Down"
lab <- vol[vol$direction != "n.s.", ]
lab <- head(lab[order(lab$padj), ], 15)
pal <- c(Up = "black", Down = "grey55", "n.s." = "grey80")
shp <- c(Up = 17, Down = 16, "n.s." = 16)
p_vol <- ggplot(vol, aes(log2FoldChange, neglog10padj)) +
  geom_vline(xintercept = c(-1, 1), linetype = "dashed", colour = "grey60", linewidth = 0.3) +
  geom_hline(yintercept = -log10(0.05), linetype = "dashed", colour = "grey60", linewidth = 0.3) +
  geom_point(aes(colour = direction, shape = direction), size = 1.6, alpha = 0.8) +
  geom_text_repel(data = lab, aes(label = gene), size = 3, seed = 42, max.overlaps = Inf) +
  scale_colour_manual(values = pal, name = NULL) +
  scale_shape_manual(values = shp, name = NULL) +
  labs(x = "log2 fold change", y = "-log10 adjusted p") +
  theme_classic(base_size = 12) + theme(legend.position = "top")
save_gg(p_vol, out[["volcano_png"]], out[["volcano_svg"]])

# ---- Top-DEG heatmap --------------------------------------------------------
top <- head(order(res$padj), 30)
hm <- assay(vsd)[top, , drop = FALSE]
hm <- t(scale(t(hm)))
ann <- as.data.frame(colData(dds)[, "condition", drop = FALSE])
ph2 <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                clustering_method = "ward.D2",
                color = colorRampPalette(c("#2C7BB6", "white", "#C0392B"))(255),
                border_color = NA, fontsize_row = 7, silent = TRUE)
save_grid(ph2$gtable, out[["heatmap_png"]], out[["heatmap_svg"]], w = 6, h = 7)

sink(type = "message")
close(log_con)
