# Muffle only the benign "package X was built under R version 4.5.3" load warning: the r45 ABI
# is stable, so the 4.5.3-built conda packages run correctly under the pinned r-base 4.5.2;
# real warnings still surface. Shadow library()/require() so it works under Snakemake's
# script runner at any call-stack depth (a top-level globalCallingHandlers does not).
# Aligning r-base to 4.5.3 would force salmon off 1.10.3 onto the 2.x Rust rewrite, so we
# muffle the harmless warning instead of changing the benchmarked environment.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

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
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
dds <- obj$dds; vsd <- obj$vsd
out <- snakemake@output

# A DESeq2-results upload carries no per-sample counts (dds/vsd are NULL in the synthetic RDS), so
# the focused heatmap and per-gene panels cannot be built. Write graceful placeholders and exit 0 so
# the rule never crashes on colData(NULL) (the GUI also gates this button, but a direct CLI run and a
# future caller reach here). count-matrix / microarray runs keep a real dds, so GOI still works there.
if (is.null(dds) || is.null(vsd)) {
  msg <- ggplot() + annotate("text", x = 0, y = 0,
    label = "Genes-of-interest figures need per-sample counts,\nabsent in a DESeq2-results upload.") +
    theme_void()
  for (pair in list(c(out[["heatmap_png"]], out[["heatmap_svg"]]),
                    c(out[["expr_png"]], out[["expr_svg"]]))) {
    ggsave(pair[[1]], msg, width = 7, height = 5, dpi = 150)
    ggsave(pair[[2]], msg, width = 7, height = 5)
  }
  writeLines("gene,note", out[["csv"]])
  writeLines("Genes-of-interest figures need per-sample counts, absent in a DESeq2-results upload.",
             out[["report"]])
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}
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
assay_kind <- tryCatch(obj$assay_kind, error = function(e) NULL)
# Treat logCPM (limma-voom / edgeR engines) like intensity: their dds/vsd wrap only a log-scale assay
# with NO counts slot, so counts(dds, normalized=TRUE) below would error. Mirrors make_figures.R; both
# assay kinds are already on a log scale (no scale_y_log10).
is_intensity <- isTRUE(assay_kind %in% c("log2_intensity", "log2_cpm"))

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (is.null(style) || !is.list(style)) style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
# Inherit the project palette + font instead of hardcoding them: getp_for merges any 'core'
# per-figure-group override onto the global style, so GOI matches the other core figures.
gp <- getp_for(style, "core")
base_size <- tryCatch(as.numeric(gp("base_font_size", 12)), error = function(e) 12)
if (length(base_size) != 1 || is.na(base_size)) base_size <- 12
pal_spec <- palette_spec(as.character(gp("palette", "Blue-Red")))
base_family <- resolve_font(as.character(gp("font_family", "")))
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = isTRUE(as.logical(gp("label_bold", FALSE))),
                                title_bold = isTRUE(as.logical(gp("title_bold", FALSE))))
# Italicise gene-symbol row labels (default TRUE). pheatmap has no per-row fontface,
# so pass a plotmath expression vector to labels_row; quotes keep special chars literal.
gene_symbol_italic <- { gsi <- style[["gene_symbol_italic"]]; if (is.null(gsi)) TRUE else isTRUE(as.logical(gsi)) }
# Per-sample column labels; default TRUE. Off (the Figure Style toggle) declutters a many-sample run.
sample_labels <- { sl <- style[["sample_labels"]]; if (is.null(sl)) TRUE else isTRUE(as.logical(sl)) }
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
               show_colnames = sample_labels,  # hide sample names to declutter a many-sample run
               labels_row = italic_labels(rownames(mat), gene_symbol_italic),
               # A constant (zero-variance) gene becomes an all-NaN row after t(scale(t(mat))) (SD=0),
               # so guard clustering on finiteness too (not just row count) — hclust aborts on NaN.
               cluster_rows = length(present) > 1 && all(is.finite(mat)),
               clustering_method = "ward.D2", fontsize = base_size, fontsize_row = 8,
               color = pal_spec$div(255),  # project diverging ramp (was a hardcoded Blue-Red)
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
# Colour the groups from the project discrete palette (was ggplot defaults), recycled to cover levels.
grp_levels <- sort(unique(long$group))
grp_cols <- setNames(rep(pal_spec$discrete, length.out = length(grp_levels)), grp_levels)
p_expr <- ggplot(long, aes(group, count, colour = group)) +
  geom_boxplot(outlier.shape = NA, alpha = 0.4) +
  geom_jitter(width = 0.15, size = 1.6) +
  facet_wrap(~ gene, scales = "free_y") +
  scale_colour_manual(values = grp_cols) +
  labs(x = NULL, y = if (identical(assay_kind, "log2_cpm")) "log2 CPM"
                    else if (identical(assay_kind, "log2_intensity")) "normalized log2 intensity"
                    else "normalised counts (log scale)") +
  style_theme(theme_bw) +
  theme(legend.position = "none", axis.text.x = element_text(angle = 30, hjust = 1))
if (!is_intensity) p_expr <- p_expr + scale_y_log10()
n_facet <- length(present)
save_gg(p_expr, out[["expr_png"]], out[["expr_svg"]],
        w = min(12, 3 * ceiling(sqrt(n_facet))), h = min(12, 2.5 * ceiling(n_facet / ceiling(sqrt(n_facet)))))

write.csv(as.data.frame(nc), out[["csv"]])
sink(type = "message"); close(log_con)
