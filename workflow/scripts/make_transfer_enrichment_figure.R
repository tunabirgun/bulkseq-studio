# Muffle only the benign "package X was built under R version 4.5.3" load warning: the r45 ABI
# is stable, so the 4.5.3-built conda packages run correctly under the pinned r-base 4.5.2;
# real warnings still surface. Shadow library()/require() so it works under Snakemake's
# script runner at any call-stack depth (a top-level globalCallingHandlers does not).
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

# Dot plot of the annotation-transfer ORA (combined foreground): the most significant
# terms of each category, one facet row per category. The canvas height is computed from
# the wrapped label lines, so labels cannot overlap; a labelled placeholder when no term
# met the criterion.

suppressMessages({
  library(ggplot2)
  library(svglite)
  library(scales)
})
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

out <- snakemake@output
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- getp_for(style, "enrichment")
fig_dpi <- as.integer(getp("dpi", 300)); base_size <- as.numeric(getp("base_font_size", 12))
label_wrap <- as.integer(getp("enrich_label_wrap", 40))
per_category <- 6L
pal_spec <- palette_spec(as.character(getp("palette", "Blue-Red")))
style_theme <- make_style_theme(base_size = base_size,
                                base_family = resolve_font(as.character(getp("font_family", ""))),
                                label_bold = isTRUE(as.logical(getp("label_bold", FALSE))),
                                title_bold = isTRUE(as.logical(getp("title_bold", FALSE))))
save_gg <- make_save_gg(fig_dpi = fig_dpi)

ora <- tryCatch(read.csv(snakemake@input[["ora"]], stringsAsFactors = FALSE), error = function(e) data.frame())
if (nrow(ora) && all(c("category", "foreground", "Description", "p.adjust", "Count", "GeneRatio") %in% names(ora)))
  ora <- ora[ora$foreground == "combined", , drop = FALSE] else ora <- data.frame()

if (!nrow(ora)) {
  save_gg(ggplot() + annotate("text", x = 0, y = 0, size = 5,
                              label = "No annotation-transfer term met the criterion") + theme_void(),
          out[["dotplot_png"]], out[["dotplot_svg"]], w = 7, h = 3)
} else {
  categories <- unique(ora$category)
  top <- do.call(rbind, lapply(categories, function(cat) {
    d <- ora[ora$category == cat, , drop = FALSE]
    head(d[order(d$p.adjust, d$ID, method = "radix"), , drop = FALSE], per_category)
  }))
  parts <- strsplit(top$GeneRatio, "/", fixed = TRUE)
  top$ratio <- vapply(parts, function(x) as.numeric(x[1]) / as.numeric(x[2]), numeric(1))
  top$label <- vapply(top$Description, function(x) paste(strwrap(x, label_wrap), collapse = "\n"), "")
  # Unique y keys keep a term shared by two categories on its own row in each facet.
  top$key <- paste(top$category, top$ID, sep = "::")
  top$key <- factor(top$key, levels = rev(top$key))
  top$category <- factor(top$category, levels = categories)
  lines <- vapply(strsplit(top$label, "\n", fixed = TRUE), length, integer(1))
  line_in <- base_size / 72 * 1.25
  fig_h <- sum(pmax(lines, 1.6)) * line_in + length(categories) * 0.3 + 1.4
  p <- ggplot(top, aes(x = ratio, y = key, size = Count, colour = p.adjust)) +
    geom_point() +
    scale_y_discrete(labels = setNames(top$label, as.character(top$key))) +
    scale_x_continuous(expand = expansion(mult = 0.15)) +
    # The sequential ramps start near white; drop the palest quarter so no point
    # disappears into the panel background.
    scale_colour_gradientn(colours = pal_spec$seq(255)[64:255], name = "Adjusted p", transform = "reverse") +
    scale_size_area(max_size = 7, name = "Genes") +
    facet_grid(category ~ ., scales = "free_y", space = "free_y") +
    labs(x = "Gene ratio (genes in term / significant genes)", y = NULL) +
    style_theme(theme_bw) +
    theme(strip.text.y = element_text(angle = 0, hjust = 0), legend.position = "right")
  save_gg(p, out[["dotplot_png"]], out[["dotplot_svg"]], w = 9, h = fig_h)
}
sink(type = "message"); close(log_con)
