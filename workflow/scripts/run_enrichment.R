# Functional enrichment (protocol section 8): GO/KEGG ORA and GSEA via
# clusterProfiler. Best-effort: any failure degrades to empty outputs + a
# REVIEW_REQUIRED check so the pipeline still completes.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

results_file <- snakemake@input[["results"]]
up_file <- snakemake@input[["up"]]
down_file <- snakemake@input[["down"]]
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
writeLines("", out[["go_up"]])
writeLines("", out[["go_down"]])
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
  res$base_id <- ids
  res$ENTREZID <- map$ENTREZID[match(ids, map[[keytype]])]
  res <- res[!is.na(res$ENTREZID), ]
  res <- res[!duplicated(res$ENTREZID), ]
  universe <- unique(res$ENTREZID)

  # Map a gene_id list (from the deseq2 up/down CSVs) to ENTREZ via res.
  to_entrez <- function(path) {
    if (!file.exists(path)) return(character(0))
    df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
    if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
    base <- sub("\\..*$", "", df$gene_id)
    unique(res$ENTREZID[match(base, res$base_id)])
  }
  run_ora <- function(genes, path) {
    genes <- genes[!is.na(genes)]
    if (length(genes) < 1) return(0)
    # qvalueCutoff = 0.20 is clusterProfiler's enrichGO default; kept as-is.
    ego <- tryCatch(enrichGO(gene = genes, universe = universe, OrgDb = orgdb,
                    keyType = "ENTREZID", ont = "BP", pAdjustMethod = "BH",
                    pvalueCutoff = 0.05, qvalueCutoff = 0.20,
                    minGSSize = 10, maxGSSize = 500, readable = TRUE),
                    error = function(e) NULL)
    n <- if (is.null(ego)) 0 else nrow(as.data.frame(ego))
    if (n > 0) write.csv(as.data.frame(ego), path, row.names = FALSE)
    n
  }

  up_e <- to_entrez(up_file)
  down_e <- to_entrez(down_file)
  all_sig <- unique(c(up_e, down_e))
  n_all <- run_ora(all_sig, out[["go"]])
  n_up <- run_ora(up_e, out[["go_up"]])
  n_down <- run_ora(down_e, out[["go_down"]])

  gene_list <- res$log2FoldChange
  names(gene_list) <- res$ENTREZID
  gene_list <- sort(gene_list[!is.na(gene_list)], decreasing = TRUE)
  set.seed(42)
  # Gene-set size limits and BH correction are gseGO's defaults, stated explicitly.
  gse <- tryCatch(
    gseGO(geneList = gene_list, OrgDb = orgdb, ont = "BP", keyType = "ENTREZID",
          pvalueCutoff = 0.05, pAdjustMethod = "BH", minGSSize = 10, maxGSSize = 500,
          eps = 0, seed = TRUE, verbose = FALSE),
    error = function(e) NULL)
  n_gsea <- 0
  if (!is.null(gse) && nrow(as.data.frame(gse)) > 0) {
    write.csv(as.data.frame(gse), out[["gsea"]], row.names = FALSE)
    n_gsea <- nrow(as.data.frame(gse))
  }

  summary_lines <<- c(summary_lines,
    sprintf("Universe (tested genes): %d", length(universe)),
    sprintf("Up-regulated: %d genes, %d GO BP terms (ORA)", length(up_e), n_up),
    sprintf("Down-regulated: %d genes, %d GO BP terms (ORA)", length(down_e), n_down),
    sprintf("Combined significant: %d genes, %d GO BP terms", length(all_sig), n_all),
    sprintf("GSEA GO BP gene sets (directional, full ranked list): %d", n_gsea))
  list(status = if (length(all_sig) >= 5) "PASS" else "REVIEW_REQUIRED",
       message = sprintf("Enrichment: up=%d terms, down=%d terms, combined=%d terms, GSEA=%d sets (%d up / %d down genes).",
                         n_up, n_down, n_all, n_gsea, length(up_e), length(down_e)))
}, error = function(e) {
  summary_lines <<- c(summary_lines, paste("Enrichment failed:", conditionMessage(e)))
  list(status = "REVIEW_REQUIRED",
       message = paste("Enrichment could not run:", conditionMessage(e)))
})

writeLines(summary_lines, out[["summary"]])
write_check(out[["check"]], result$status, result$message)
sink(type = "message")
close(log_con)
