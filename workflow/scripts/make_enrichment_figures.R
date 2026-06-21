# Enrichment visualisations (0.5.0) from the persisted clusterProfiler objects
# (results/enrichment/enrichment_objects.rds). Best-effort: every figure degrades
# to a labelled placeholder when there is no result or a plot call fails, so the
# rule always produces its declared PNG+SVG outputs and never breaks the run.

suppressMessages({
  library(ggplot2)
  library(svglite)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- tryCatch(readRDS(snakemake@input[["objects"]]), error = function(e) list())
out <- snakemake@output

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- function(k, d) { v <- style[[k]]; if (is.null(v)) d else v }
fig_w <- as.numeric(getp("width_in", 7))
fig_h <- as.numeric(getp("height_in", 6))
fig_dpi <- as.integer(getp("dpi", 300))

save_gg <- function(p, png_path, svg_path, w = fig_w, h = fig_h) {
  ggsave(png_path, p, width = w, height = h, dpi = fig_dpi, limitsize = FALSE)
  ggsave(svg_path, p, width = w, height = h, limitsize = FALSE)
}
placeholder <- function(msg, png_path, svg_path) {
  save_gg(ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void(),
          png_path, svg_path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

# Render `expr` to PNG+SVG; placeholder when `ok` is FALSE or the plot errors.
# `expr` is lazily evaluated, so it never runs when there is no data.
render <- function(ok, expr, png_path, svg_path, empty_msg) {
  if (!isTRUE(ok)) { placeholder(empty_msg, png_path, svg_path); return(invisible()) }
  p <- tryCatch(expr, error = function(e) { message("plot failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) placeholder("Figure could not be generated", png_path, svg_path)
  else save_gg(p, png_path, svg_path)
}

have_ep <- requireNamespace("enrichplot", quietly = TRUE)
no_data <- "No enrichment results (organism unmapped or nothing significant)"
if (have_ep) suppressMessages(library(enrichplot))

ego_all <- obj$ego_all
gse <- obj$gse
geneList <- obj$geneList

# ORA dotplot (combined up+down GO BP terms).
render(have_ep && nrows(ego_all) > 0,
       dotplot(ego_all, showCategory = 15),
       out[["dotplot_png"]], out[["dotplot_svg"]], no_data)

# GSEA running-score for the top gene set, and a ridgeplot of the leading sets.
render(have_ep && nrows(gse) > 0,
       gseaplot2(gse, geneSetID = 1, title = as.data.frame(gse)$Description[1]),
       out[["gsea_png"]], out[["gsea_svg"]], no_data)
render(have_ep && nrows(gse) > 0,
       ridgeplot(gse, showCategory = 15),
       out[["ridge_png"]], out[["ridge_svg"]], no_data)

# Gene-concept network (fold-change coloured when possible) and term-similarity map.
set.seed(42)
render(have_ep && nrows(ego_all) > 0,
       tryCatch(cnetplot(ego_all, showCategory = 5, foldChange = geneList),
                error = function(e) cnetplot(ego_all, showCategory = 5)),
       out[["cnet_png"]], out[["cnet_svg"]], no_data)
render(have_ep && nrows(ego_all) > 1,
       emapplot(pairwise_termsim(ego_all), showCategory = 20),
       out[["emap_png"]], out[["emap_svg"]], no_data)

# Disease-ontology ORA dotplot (human/mouse only; placeholder otherwise).
render(have_ep && nrows(obj$ego_do) > 0,
       dotplot(obj$ego_do, showCategory = 15),
       out[["do_dotplot_png"]], out[["do_dotplot_svg"]],
       "No disease-ontology terms (human/mouse only)")

sink(type = "message")
close(log_con)
