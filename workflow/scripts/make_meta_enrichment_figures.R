# Cross-study enrichment figure: clusterProfiler::compareCluster dotplot (term x gene-set), the
# shared-vs-distinct pathway view. Reads results/meta/meta_enrichment_objects.rds (the persisted
# compareClusterResult); degrades to a labelled placeholder when enrichment was skipped/empty.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})
suppressMessages({ library(ggplot2); library(svglite); library(scales); library(RColorBrewer) })
# clusterProfiler/enrichplot MUST load before readRDS so the compareClusterResult S4 class is
# defined when the persisted object is deserialised (else as.data.frame(cc) fails -> placeholder).
.have_cp <- suppressMessages(requireNamespace("clusterProfiler", quietly = TRUE) &&
                            requireNamespace("enrichplot", quietly = TRUE))
if (.have_cp) suppressMessages({ library(clusterProfiler); library(enrichplot) })
source(file.path(snakemake@scriptdir, "figure_style.R"))
log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")
out <- snakemake@output
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style); gp <- getp_for(style, "comparative_meta")
palette_name <- as.character(gp("palette", "Blue-Red"))
pal_spec <- palette_spec(palette_name)
font_family <- as.character(gp("font_family", ""))
if (!nzchar(font_family)) font_family <- "Times New Roman"
base_family <- resolve_font(font_family)
if (!identical(base_family, font_family)) message("Requested figure font ", font_family, " substituted with ", base_family)
if (!base_family %in% systemfonts::system_fonts()$family)
  stop("Comparative enrichment figure font is unavailable: ", base_family)
base_size <- as.numeric(gp("base_font_size", 12))
base_style_theme <- make_style_theme(base_size = base_size, base_family = base_family)
style_theme <- function() base_style_theme() +
  theme(text = element_text(family = base_family, colour = "black"),
        axis.text = element_text(colour = "black"))
base_save_gg <- make_save_gg(fig_w = as.numeric(gp("width_in", 8)), fig_h = as.numeric(gp("height_in", 7)),
                             fig_dpi = as.integer(getp("dpi", 300)))
save_gg <- function(plot, png_path, svg_path, w = as.numeric(gp("width_in", 8)),
                    h = as.numeric(gp("height_in", 7))) {
  base_save_gg(plot, png_path, svg_path, w = w, h = h)
  svg <- readLines(svg_path, warn = FALSE)
  svg <- gsub(".svglite", "svg", svg, fixed = TRUE)
  svg <- gsub(" class='svglite'", "", svg, fixed = TRUE)
  writeLines(svg, svg_path, useBytes = TRUE)
}
show_cat <- as.integer(getp("meta_enrich_show_category", 6))
empty_plotted <- data.frame(Cluster = character(), Description = character(),
                            GeneRatio = character(), p.adjust = numeric(), Count = integer())

placeholder <- function(msg) {
  write.csv(empty_plotted, out[["plotted_data"]], row.names = FALSE)
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 4.2,
                           family = base_family, colour = "black") + theme_void() + xlim(-1, 1) + ylim(-1, 1)
  save_gg(p, out[["dotplot_png"]], out[["dotplot_svg"]])
}

cc <- tryCatch(readRDS(snakemake@input[["objects"]]), error = function(e) NULL)
n <- tryCatch(nrow(as.data.frame(cc)), error = function(e) 0)
if (is.null(cc) || is.null(n) || n == 0) {
  placeholder("Cross-study enrichment unavailable\n(organism unmapped or no significant terms)")
} else {
  p <- tryCatch({
    # enrichplot's compareCluster dotplot maps p.adjust to `fill` (1.30.4; older versions
    # used `colour`), so set BOTH: the colour-only scale silently did nothing and the
    # figure kept enrichplot's default ramp instead of the project palette.
    d <- suppressWarnings(
      enrichplot::dotplot(cc, showCategory = show_cat, includeAll = TRUE) +
        scale_color_gradientn(colours = rev(pal_spec$seq(255)), name = "p.adjust") +
        scale_fill_gradientn(colours = rev(pal_spec$seq(255)), name = "p.adjust")) +
      style_theme() + theme(axis.text.x = element_text(angle = 40, hjust = 1),
                            axis.text.y = element_text(size = 8)) + labs(x = NULL, y = NULL)
    d
  }, error = function(e) { message("dotplot failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) placeholder("Cross-study enrichment dotplot\ncould not be rendered")
  else tryCatch({
                  fields <- names(empty_plotted)
                  if (!all(fields %in% names(p$data))) stop("Enrichment dotplot rows unavailable")
                  write.csv(p$data[, fields, drop = FALSE], out[["plotted_data"]], row.names = FALSE)
                  save_gg(p, out[["dotplot_png"]], out[["dotplot_svg"]],
                        w = max(7, 1.1 * length(unique(as.data.frame(cc)$Cluster)) + 4),
                        h = max(6, 0.32 * show_cat * length(unique(as.data.frame(cc)$Cluster)) + 3))
                },
                error = function(e) placeholder("Cross-study enrichment dotplot\ncould not be rendered"))
}
sink(type = "message"); close(log_con)
