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
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

out <- snakemake@output
organism <- tolower(as.character(snakemake@params[["organism"]]))
alpha <- as.numeric(snakemake@params[["alpha"]])
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- function(k, d) { v <- style[[k]]; if (is.null(v)) d else v }
fig_w <- as.numeric(getp("width_in", 7)); fig_h <- as.numeric(getp("height_in", 6))
fig_dpi <- as.integer(getp("dpi", 300))

write_check <- function(status, message) {
  msg <- gsub('"', '\\\\"', message)
  writeLines(sprintf('{\n  "check": "15_set_overlap",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                     status, status, msg), out[["check"]])
}
placeholder <- function(msg) {
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void()
  ggsave(out[["png"]], p, width = fig_w, height = fig_h, dpi = fig_dpi)
  ggsave(out[["svg"]], p, width = fig_w, height = fig_h)
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
    if (length(de) < 1) {
      placeholder("Set-overlap: no DE genes with symbols")
      message <- "Set-overlap skipped: no DE genes carry a gene symbol."
    } else {
      eo <- enricher(de, TERM2GENE = t2g, universe = universe, pvalueCutoff = alpha, pAdjustMethod = "BH")
      edf <- if (is.null(eo)) data.frame() else as.data.frame(eo)
      if (nrow(edf) > 0) {
        write.csv(edf, out[["csv"]], row.names = FALSE)
        dp <- enrichplot::dotplot(eo, showCategory = 15)
        ggsave(out[["png"]], dp, width = fig_w, height = fig_h, dpi = fig_dpi)
        ggsave(out[["svg"]], dp, width = fig_w, height = fig_h)
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
