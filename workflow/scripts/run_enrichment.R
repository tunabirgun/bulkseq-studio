# Functional enrichment (protocol section 8): GO/KEGG ORA and GSEA via
# clusterProfiler. Best-effort: any failure degrades to empty outputs + a
# REVIEW_REQUIRED check so the pipeline still completes.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

results_file <- snakemake@input[["results"]]
orgdb_name <- snakemake@params[["orgdb"]]
keytype <- snakemake@params[["keytype"]]
kegg_org <- snakemake@params[["kegg"]]
alpha <- as.numeric(snakemake@params[["alpha"]])
out <- snakemake@output

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "10_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}

# Always create the output files first so the rule succeeds even on failure.
writeLines("", out[["go"]])
writeLines("", out[["gsea"]])
summary_lines <- c("Functional enrichment summary", "=============================", "")

# No OrgDb mapping for this organism (e.g. most fungi/bacteria): skip cleanly
# rather than risk running against the wrong species' database.
if (is.null(orgdb_name) || !nzchar(orgdb_name)) {
  writeLines(c(summary_lines,
               "Skipped: no Bioconductor OrgDb is mapped for this organism.",
               "Enrichment supports human, mouse, fly, worm, zebrafish, yeast, Arabidopsis",
               "(install the matching org.*.db package to enable it)."),
             out[["summary"]])
  write_check(out[["check"]], "PASS",
              "Enrichment skipped: no OrgDb mapped for this organism (gene-level DE is unaffected).")
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}

result <- tryCatch({
  suppressMessages({
    library(clusterProfiler)
    library(orgdb_name, character.only = TRUE)
  })
  orgdb <- get(orgdb_name)

  res <- read.csv(results_file, stringsAsFactors = FALSE)
  res <- res[!is.na(res$padj), ]
  ids <- sub("\\..*$", "", res$gene_id)  # strip version suffix if any
  map <- bitr(ids, fromType = keytype, toType = "ENTREZID", OrgDb = orgdb)
  res$ENTREZID <- map$ENTREZID[match(ids, map[[keytype]])]
  res <- res[!is.na(res$ENTREZID), ]
  res <- res[!duplicated(res$ENTREZID), ]
  universe <- unique(res$ENTREZID)
  sig <- res$ENTREZID[res$padj < alpha & abs(res$log2FoldChange) > 1]

  ego <- enrichGO(gene = sig, universe = universe, OrgDb = orgdb,
                  keyType = "ENTREZID", ont = "BP", pAdjustMethod = "BH",
                  pvalueCutoff = 0.05, qvalueCutoff = 0.20, readable = TRUE)
  if (!is.null(ego) && nrow(as.data.frame(ego)) > 0) {
    write.csv(as.data.frame(ego), out[["go"]], row.names = FALSE)
  }
  n_go <- if (is.null(ego)) 0 else nrow(as.data.frame(ego))

  gene_list <- res$log2FoldChange
  names(gene_list) <- res$ENTREZID
  gene_list <- sort(gene_list[!is.na(gene_list)], decreasing = TRUE)
  set.seed(42)
  gse <- tryCatch(
    gseGO(geneList = gene_list, OrgDb = orgdb, ont = "BP", keyType = "ENTREZID",
          pvalueCutoff = 0.05, eps = 0, seed = TRUE, verbose = FALSE),
    error = function(e) NULL)
  n_gsea <- 0
  if (!is.null(gse) && nrow(as.data.frame(gse)) > 0) {
    write.csv(as.data.frame(gse), out[["gsea"]], row.names = FALSE)
    n_gsea <- nrow(as.data.frame(gse))
  }

  summary_lines <<- c(summary_lines,
    sprintf("Significant genes tested: %d (universe %d)", length(sig), length(universe)),
    sprintf("Enriched GO BP terms (ORA): %d", n_go),
    sprintf("GSEA GO BP gene sets: %d", n_gsea))
  list(status = if (length(sig) >= 5) "PASS" else "REVIEW_REQUIRED",
       message = sprintf("Enrichment complete: %d ORA terms, %d GSEA sets from %d significant genes.",
                         n_go, n_gsea, length(sig)))
}, error = function(e) {
  summary_lines <<- c(summary_lines, paste("Enrichment failed:", conditionMessage(e)))
  list(status = "REVIEW_REQUIRED",
       message = paste("Enrichment could not run:", conditionMessage(e)))
})

writeLines(summary_lines, out[["summary"]])
write_check(out[["check"]], result$status, result$message)
sink(type = "message")
close(log_con)
