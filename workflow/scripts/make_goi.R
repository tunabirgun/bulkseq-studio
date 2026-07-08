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
# Gene-id -> symbol map (from the DE step) lets the user list match either ids or
# symbols, and labels the figures by symbol. Falls back to ids when unknown.
symbol_map <- tryCatch(obj$symbol_map, error = function(e) NULL)
lab_for <- function(ids) {
  if (is.null(symbol_map)) return(ids)
  s <- unname(symbol_map[ids])
  ifelse(is.na(s) | !nzchar(s), ids, s)
}
# Microarray (limma): there are no counts; use the log2 intensity matrix and a
# linear y axis for the per-gene panel instead of normalized-count / log scale.
is_intensity <- identical(tryCatch(obj$assay_kind, error = function(e) NULL), "log2_intensity")

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (is.null(style) || !is.list(style)) style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
base_size <- tryCatch(as.numeric(style[["base_font_size"]]), error = function(e) 12)
if (length(base_size) != 1 || is.na(base_size)) base_size <- 12
# Italicise gene-symbol row labels (default TRUE). pheatmap has no per-row fontface,
# so pass a plotmath expression vector to labels_row; quotes keep special chars literal.
gene_symbol_italic <- { gsi <- style[["gene_symbol_italic"]]; if (is.null(gsi)) TRUE else isTRUE(as.logical(gsi)) }
italic_labels <- function(x, italic = TRUE) {
  if (!isTRUE(italic)) return(x)
  parse(text = paste0('italic("', gsub('"', '', as.character(x)), '")'))
}

group_var <- "condition"
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1 && !is.null(cons[[1]][["factor"]])) group_var <- as.character(cons[[1]][["factor"]])
}
if (!(group_var %in% colnames(colData(dds)))) group_var <- colnames(colData(dds))[1]

# Defensive: the rule is only defined when a gene list exists, but guard anyway
# so an empty/missing input writes graceful outputs instead of crashing.
genes_path <- snakemake@input[["genes"]]
if (length(genes_path) < 1 || !nzchar(genes_path[[1]]) || !file.exists(genes_path[[1]])) {
  writeLines("No gene list supplied (gene_sets.custom_gene_list).", out[["report"]])
  empty <- ggplot() + annotate("text", x = 0, y = 0, label = "No genes of interest supplied") + theme_void()
  ggsave(out[["heatmap_png"]], empty, width = 7, height = 5, dpi = 300)
  ggsave(out[["heatmap_svg"]], empty, width = 7, height = 5)
  ggsave(out[["expr_png"]], empty, width = 7, height = 5, dpi = 300)
  ggsave(out[["expr_svg"]], empty, width = 7, height = 5)
  writeLines("gene,note", out[["csv"]])
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}
goi <- readLines(genes_path[[1]], warn = FALSE)
goi <- trimws(goi)
goi <- goi[nzchar(goi) & !startsWith(goi, "#")]
# Match full ids first (no collision); fall back to Ensembl-version-stripped only for ENS*
# ids, so distinct dotted non-Ensembl ids (locus tags etc.) are never collapsed to a sibling.
strip_ver <- function(x) { e <- grepl("^ENS", x); x[e] <- sub("\\.\\d+$", "", x[e]); x }
rn <- rownames(vsd)                 # this run's actual gene IDs (used in the mismatch hint below)
idx <- match(goi, rn)
un <- is.na(idx)
if (any(un)) idx[un] <- match(strip_ver(goi[un]), strip_ver(rn))
# Second pass: match any still-unmatched entries against gene symbols (case-
# insensitive), so a user can paste either Ensembl ids or symbols.
if (!is.null(symbol_map)) {
  sym <- toupper(unname(symbol_map[rownames(vsd)]))
  un <- is.na(idx)
  if (any(un)) idx[un] <- match(toupper(goi[un]), sym)
}
present <- idx[!is.na(idx)]
missing <- goi[is.na(idx)]

# Flag a likely identifier-format mismatch (e.g. symbols pasted into a run keyed on
# locus tags) and show what this run's IDs actually look like so the list can be fixed.
match_rate <- if (length(goi)) length(present) / length(goi) else 0
example_ids <- head(rn[nzchar(rn)], 3)
sv <- if (!is.null(symbol_map)) unname(symbol_map) else character(0)
example_syms <- head(unique(sv[!is.na(sv) & nzchar(sv)]), 3)
id_hint <- sprintf("This run's gene IDs look like: %s%s",
                   paste(example_ids, collapse = ", "),
                   if (length(example_syms)) sprintf("  (symbols: %s)", paste(example_syms, collapse = ", ")) else "")
flag <- character(0)
if (length(goi) && length(present) == 0) {
  flag <- c("WARNING: none of the supplied genes matched this run -- the identifiers are probably in a different format than the run uses.", id_hint)
} else if (match_rate < 0.5) {
  flag <- c(sprintf("WARNING: only %d of %d genes matched (%.0f%%); check that the unmatched IDs use this run's identifier format.",
                    length(present), length(goi), 100 * match_rate), id_hint)
}
writeLines(c(
  flag,
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
rownames(mat) <- lab_for(rownames(vsd)[present])
if (length(present) > 1) mat <- t(scale(t(mat)))
ann <- as.data.frame(colData(dds)[, group_var, drop = FALSE])
ph <- pheatmap(mat, scale = "none", annotation_col = ann, show_rownames = TRUE,
               labels_row = italic_labels(rownames(mat), gene_symbol_italic),
               cluster_rows = length(present) > 1,
               clustering_method = "ward.D2", fontsize = base_size, fontsize_row = 8,
               color = colorRampPalette(c("#2C7BB6", "white", "#C0392B"))(255),
               border_color = NA, silent = TRUE)
# Size from BOTH axes: height from gene count, width from sample count (a many-sample
# GOI heatmap otherwise crushed its columns). Mirrors figure_style.R::heatmap_dim.
gutter <- min(2.6, 0.6 + 0.070 * max(nchar(rownames(mat)), 1))
ww <- min(max(gutter + ncol(mat) * 10 / 72 + 1.7, 7), 44)
hh <- min(max(length(present) * 12 / 72 + 3.1, 4), 44)
png(out[["heatmap_png"]], width = ww, height = hh, units = "in", res = 300)
grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
svglite(out[["heatmap_svg"]], width = ww, height = hh)
grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()

# ---- Per-gene expression comparison across conditions -----------------------
# Counts route through counts(dds, normalized=TRUE); microarray has no counts, so
# use the normalized log2 intensity matrix (assay(vsd)) on a linear axis.
nc <- if (is_intensity) assay(vsd)[present, , drop = FALSE] else counts(dds, normalized = TRUE)[present, , drop = FALSE]
rownames(nc) <- lab_for(rownames(vsd)[present])
groups <- as.character(colData(dds)[[group_var]])
long <- do.call(rbind, lapply(seq_len(nrow(nc)), function(i) {
  data.frame(gene = rownames(nc)[i], sample = colnames(nc), count = as.numeric(nc[i, ]),
             group = groups, stringsAsFactors = FALSE)
}))
p_expr <- ggplot(long, aes(group, count, colour = group)) +
  geom_boxplot(outlier.shape = NA, alpha = 0.4) +
  geom_jitter(width = 0.15, size = 1.6) +
  facet_wrap(~ gene, scales = "free_y") +
  labs(x = NULL, y = if (is_intensity) "normalized log2 intensity" else "normalised counts (log scale)") +
  theme_bw(base_size = base_size) +
  theme(legend.position = "none", axis.text.x = element_text(angle = 30, hjust = 1))
if (!is_intensity) p_expr <- p_expr + scale_y_log10()
n_facet <- length(present)
save_gg(p_expr, out[["expr_png"]], out[["expr_svg"]],
        w = min(12, 3 * ceiling(sqrt(n_facet))), h = min(12, 2.5 * ceiling(n_facet / ceiling(sqrt(n_facet)))))

write.csv(as.data.frame(nc), out[["csv"]])
sink(type = "message"); close(log_con)
