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
# getp_for merges the 'enrichment' per-figure-group override on top of the global style, so this
# custom-gene-set dotplot honours an enrichment-group palette/font override like the built-in
# enrichment figures do (make_getp alone would only read the global defaults).
getp <- getp_for(style, "enrichment")
fig_w <- as.numeric(getp("width_in", 7)); fig_h <- as.numeric(getp("height_in", 6))
fig_dpi <- as.integer(getp("dpi", 300)); base_size <- as.numeric(getp("base_font_size", 12))
font_family <- as.character(getp("font_family", "")); label_bold <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold <- isTRUE(as.logical(getp("title_bold", FALSE)))
palette_name <- as.character(getp("palette", "Blue-Red"))
show_cat <- as.integer(getp("enrich_show_category", 15)); label_wrap <- as.integer(getp("enrich_label_wrap", 40))

pal_spec <- palette_spec(palette_name)
base_family <- resolve_font(font_family)  # map a Windows font name to an installed WSL/Linux one, like the built-in enrichment figures
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = label_bold, title_bold = title_bold)
save_gg <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)
placeholder <- function(msg, png_path, svg_path) {
  save_gg(ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void(), png_path, svg_path)
}

eora <- tryCatch(obj$eora, error = function(e) NULL)
if (requireNamespace("enrichplot", quietly = TRUE) && nrows(eora) > 0) {
  # enrichplot's dotplot maps p.adjust to the FILL aesthetic, so a colour-only scale is a no-op and
  # the palette is ignored — set BOTH colour and fill (mirrors themed_dotplot in make_enrichment_figures.R).
  # suppressWarnings hushes the benign "Scale for fill is already present" from replacing enrichplot's default.
  p <- tryCatch(suppressWarnings(
    enrichplot::dotplot(eora, showCategory = show_cat,
                        label_format = function(lbl) scales::label_wrap(label_wrap)(lbl)) +
      scale_color_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse") +
      scale_fill_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse") +
      labs(title = NULL) + style_theme(theme_bw)),
    error = function(e) { message("custom dotplot failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) {
    placeholder("Custom gene-set dotplot could not be generated", out[["dotplot_png"]], out[["dotplot_svg"]])
  } else {
    # Guard the draw too (enrichplot renders lazily at ggsave time), degrading to a placeholder so
    # a draw-time error never aborts the rule and leaves the declared PNG/SVG unwritten.
    tryCatch({
      ggsave(out[["dotplot_png"]], p, width = fig_w, height = fig_h, units = "in", dpi = fig_dpi, limitsize = FALSE)
      ggsave(out[["dotplot_svg"]], p, width = fig_w, height = fig_h, units = "in", limitsize = FALSE)
    }, error = function(e) { message("custom dotplot draw failed: ", conditionMessage(e))
                            placeholder("Custom gene-set dotplot could not be generated", out[["dotplot_png"]], out[["dotplot_svg"]]) })
  }
} else {
  placeholder("No custom gene-set ORA terms passed the cutoff", out[["dotplot_png"]], out[["dotplot_svg"]])
}
sink(type = "message"); close(log_con)
