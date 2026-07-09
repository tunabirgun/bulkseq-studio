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

# DE-list vs gene-set overlap significance (0.6.0): hypergeometric ORA of the DE
# genes against MSigDB Hallmark via clusterProfiler::enricher (same statistic,
# reuses the enrichplot dotplot). Within-run (one contrast). Organism-gated:
# human/mouse native, fly/worm/zebrafish/yeast by human-ortholog projection,
# others (e.g. Arabidopsis, fungi) skip cleanly.

suppressMessages({
  library(clusterProfiler)
  library(msigdbr)
  library(ggplot2)
  library(svglite)
  library(scales)
  library(RColorBrewer)
})

# Shared palette/theme/getp helpers (sourced; resolved via scriptdir).
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

out <- snakemake@output
organism <- tolower(as.character(snakemake@params[["organism"]]))
alpha <- as.numeric(snakemake@params[["alpha"]])
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style)
fig_w <- as.numeric(getp("width_in", 7)); fig_h <- as.numeric(getp("height_in", 6))
fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(getp("base_font_size", 12))
font_family <- as.character(getp("font_family", ""))
label_bold <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold <- isTRUE(as.logical(getp("title_bold", FALSE)))
palette_name <- as.character(getp("palette", "Blue-Red"))
show_cat <- as.integer(getp("enrich_show_category", 15))
label_wrap <- as.integer(getp("enrich_label_wrap", 40))
pal_spec <- palette_spec(palette_name)
base_family <- if (nzchar(font_family)) font_family else NULL
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = label_bold, title_bold = title_bold)

write_check <- function(status, message) {
  msg <- gsub('"', '\\\\"', message)
  writeLines(sprintf('{\n  "check": "15_set_overlap",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                     status, status, msg), out[["check"]])
}
placeholder <- function(msg) {
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void()
  ggsave(out[["png"]], p, width = fig_w, height = fig_h, units = "in", dpi = fig_dpi)
  ggsave(out[["svg"]], p, width = fig_w, height = fig_h, units = "in")
}
writeLines("ID,Description,note", out[["csv"]])  # default; overwritten on success

# Organism -> msigdbr species + db_species + Hallmark collection code.
spec <- NULL; dbsp <- "HS"; coll <- "H"
if (grepl("homo sapiens|human", organism)) { spec <- "Homo sapiens"; dbsp <- "HS"; coll <- "H" }
if (grepl("mus musculus|mouse", organism)) { spec <- "Mus musculus"; dbsp <- "MM"; coll <- "MH" }
if (grepl("drosophila", organism)) { spec <- "Drosophila melanogaster"; dbsp <- "HS"; coll <- "H" }
if (grepl("caenorhabditis|elegans", organism)) { spec <- "Caenorhabditis elegans"; dbsp <- "HS"; coll <- "H" }
if (grepl("danio|zebrafish", organism)) { spec <- "Danio rerio"; dbsp <- "HS"; coll <- "H" }
if (grepl("cerevisiae|yeast", organism)) { spec <- "Saccharomyces cerevisiae"; dbsp <- "HS"; coll <- "H" }

status <- "PASS"; message <- NULL
if (is.null(spec)) {
  placeholder("Set-overlap skipped: organism not in MSigDB")
  message <- sprintf("Set-overlap skipped: %s is not covered by MSigDB/msigdbr.", organism)
} else {
  tryCatch({
    msig <- msigdbr(species = spec, db_species = dbsp, collection = coll)
    t2g <- as.data.frame(msig[, c("gs_name", "gene_symbol")])
    res <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE, check.names = FALSE)
    universe <- unique(res$symbol[!is.na(res$symbol) & nzchar(res$symbol)])
    up <- tryCatch(read.csv(snakemake@input[["up"]], stringsAsFactors = FALSE), error = function(e) data.frame())
    down <- tryCatch(read.csv(snakemake@input[["down"]], stringsAsFactors = FALSE), error = function(e) data.frame())
    de <- unique(c(up$symbol, down$symbol)); de <- de[!is.na(de) & nzchar(de)]
    # Count-matrix mode has no GTF, so symbols are all NA; user count matrices are
    # commonly keyed by symbol, so fall back to gene_id (MSigDB TERM2GENE is by
    # gene_symbol, so this only finds overlap when the IDs are themselves symbols).
    if (length(universe) < 1 || length(de) < 1) {
      universe <- unique(res$gene_id[!is.na(res$gene_id) & nzchar(res$gene_id)])
      de <- unique(c(up$gene_id, down$gene_id)); de <- de[!is.na(de) & nzchar(de)]
    }
    if (length(de) < 1) {
      placeholder("Set-overlap: no DE genes with symbols")
      message <- "Set-overlap skipped: no DE genes carry a gene symbol."
    } else {
      eo <- enricher(de, TERM2GENE = t2g, universe = universe, pvalueCutoff = alpha, pAdjustMethod = "BH")
      edf <- if (is.null(eo)) data.frame() else as.data.frame(eo)
      if (nrow(edf) > 0) {
        write.csv(edf, out[["csv"]], row.names = FALSE)
        # Sequential p.adjust ramp (reversed so most significant is darkest), long
        # Hallmark names wrapped, no embedded title. Description (set NAME) kept.
        n_show <- min(show_cat, nrow(edf))
        dp <- enrichplot::dotplot(eo, showCategory = n_show,
                                  label_format = function(lbl) scales::label_wrap(label_wrap)(lbl)) +
          scale_colour_gradientn(colours = pal_spec$seq(255), name = "p.adjust",
                                 transform = "reverse") +
          labs(title = NULL) +
          style_theme(theme_bw)
        # Canvas height scales with the number of plotted sets so the dot does not
        # float in an oversized panel; capped so very large sets stay readable.
        ov_h <- max(fig_h, 1.2 + 0.32 * n_show)
        ggsave(out[["png"]], dp, width = fig_w, height = ov_h, units = "in", dpi = fig_dpi)
        ggsave(out[["svg"]], dp, width = fig_w, height = ov_h, units = "in")
      } else {
        placeholder("No significant MSigDB Hallmark overlap")
      }
      message <- sprintf("MSigDB Hallmark (%s) overlap with %d DE genes: %d significant sets (padj < %.3g).",
                         coll, length(de), nrow(edf), alpha)
    }
  }, error = function(e) {
    placeholder(paste("Set-overlap failed:", conditionMessage(e)))
    status <<- "REVIEW_REQUIRED"
    message <<- paste("Set-overlap could not run:", conditionMessage(e))
  })
}
write_check(status, message)

sink(type = "message")
close(log_con)
