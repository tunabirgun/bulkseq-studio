# Custom gene-set enrichment dotplot from results/enrichment/custom_enrichment_objects.rds.
# Separate from run_custom_enrichment.R so the figure can be restyled without re-running
# enrichment (matches the enrichment / enrichment_figures split). Best-effort: a labelled
# placeholder when there is no custom ORA result, so the rule always writes its PNG+SVG.

suppressMessages({
  library(ggplot2)
  library(svglite)
  library(scales)
  library(RColorBrewer)
})
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- tryCatch(readRDS(snakemake@input[["objects"]]), error = function(e) list())
out <- snakemake@output
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style)
fig_w <- as.numeric(getp("width_in", 7)); fig_h <- as.numeric(getp("height_in", 6))
fig_dpi <- as.integer(getp("dpi", 300)); base_size <- as.numeric(getp("base_font_size", 12))
font_family <- as.character(getp("font_family", "")); label_bold <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold <- isTRUE(as.logical(getp("title_bold", FALSE)))
palette_name <- as.character(getp("palette", "Blue-Red"))
show_cat <- as.integer(getp("enrich_show_category", 15)); label_wrap <- as.integer(getp("enrich_label_wrap", 40))

pal_spec <- palette_spec(palette_name)
base_family <- if (nzchar(font_family)) font_family else NULL
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = label_bold, title_bold = title_bold)
save_gg <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)
placeholder <- function(msg, png_path, svg_path) {
  save_gg(ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void(), png_path, svg_path)
}

eora <- tryCatch(obj$eora, error = function(e) NULL)
if (requireNamespace("enrichplot", quietly = TRUE) && nrows(eora) > 0) {
  p <- tryCatch(
    enrichplot::dotplot(eora, showCategory = show_cat,
                        label_format = function(lbl) scales::label_wrap(label_wrap)(lbl)) +
      scale_color_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse") +
      labs(title = NULL) + style_theme(theme_bw),
    error = function(e) { message("custom dotplot failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) {
    placeholder("Custom gene-set dotplot could not be generated", out[["dotplot_png"]], out[["dotplot_svg"]])
  } else {
    ggsave(out[["dotplot_png"]], p, width = fig_w, height = fig_h, units = "in", dpi = fig_dpi, limitsize = FALSE)
    ggsave(out[["dotplot_svg"]], p, width = fig_w, height = fig_h, units = "in", limitsize = FALSE)
  }
} else {
  placeholder("No custom gene-set ORA terms passed the cutoff", out[["dotplot_png"]], out[["dotplot_svg"]])
}
sink(type = "message"); close(log_con)
