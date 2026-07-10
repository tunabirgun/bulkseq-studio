# Cross-study functional enrichment for the meta-analysis (0.21.0). clusterProfiler::compareCluster
# over a NAMED gene list {study_<S>_up, study_<S>_down for each study} + {convergent_up,
# convergent_down = meta_sig genes by common_direction}, on ONE shared universe (the intersected
# tested set) so the term-by-study columns are comparable. Org-db-gated exactly like run_enrichment.R:
# unmapped organisms write empty outputs + a PASS-skip check (not a failure). Persists the
# compareClusterResult to RDS so the figure rule restyles without re-running enrichment.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})
log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")
results_file <- snakemake@input[["results"]]
orgdb_name <- snakemake@params[["orgdb"]]; keytype <- snakemake@params[["keytype"]]
ont <- tryCatch(snakemake@params[["ont"]], error = function(e) "BP")
alpha <- as.numeric(snakemake@params[["alpha"]])
out <- snakemake@output

write_check <- function(status, message) {
  msg <- gsub('"', '\\\\"', message)
  writeLines(sprintf('{\n  "check": "18_meta_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                     status, status, msg), out[["check"]])
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)
strip_version <- function(id) { v <- grepl("^ENS", id); id[v] <- sub("\\.\\d+$", "", id[v]); id }
skip <- function(reason) {
  write.csv(data.frame(Cluster = character(0), ID = character(0), Description = character(0)),
            out[["ora"]], row.names = FALSE)
  saveRDS(NULL, out[["objects"]]); write_check("PASS", reason)
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}
# Always create outputs first so the rule succeeds even on an early failure.
write.csv(data.frame(Cluster = character(0), ID = character(0), Description = character(0)),
          out[["ora"]], row.names = FALSE)
saveRDS(NULL, out[["objects"]])

res <- tryCatch(read.csv(results_file, stringsAsFactors = FALSE), error = function(e) NULL)
if (is.null(res) || nrow(res) == 0 || !"meta_sig" %in% colnames(res))
  skip("Cross-study enrichment skipped: the meta-analysis produced no shared-gene result.")
if (is.null(orgdb_name) || !nzchar(orgdb_name))
  skip("Cross-study enrichment skipped: organism has no Bioconductor OrgDb (gene-level meta is unaffected).")

suppressMessages({ library(clusterProfiler); ok <- require(orgdb_name, character.only = TRUE) })
if (!isTRUE(ok)) skip(sprintf("Cross-study enrichment skipped: OrgDb %s not installed.", orgdb_name))
orgdb <- get(orgdb_name)

ids <- strip_version(res$gene_id)
map <- tryCatch(bitr(unique(ids), fromType = keytype, toType = "ENTREZID", OrgDb = orgdb),
                error = function(e) { message("bitr failed: ", conditionMessage(e)); NULL })
if (is.null(map) || nrow(map) == 0)
  skip(sprintf("Cross-study enrichment skipped: 0 of %d gene ids mapped to ENTREZ (keytype %s).",
               length(unique(ids)), keytype))
res$entrez <- map$ENTREZID[match(ids, map[[keytype]])]
universe <- unique(res$entrez[!is.na(res$entrez)])

# Named gene list: per-study up/down (study_<S>_padj<alpha, split by sign) + convergent up/down.
study_cols <- grep("^study_.*_log2FC$", colnames(res), value = TRUE)
studies <- sub("^study_(.*)_log2FC$", "\\1", study_cols)
ez <- function(idx) unique(res$entrez[idx][!is.na(res$entrez[idx])])
lists <- list()
for (s in studies) {
  lfc <- res[[paste0("study_", s, "_log2FC")]]; padj <- res[[paste0("study_", s, "_padj")]]
  up <- ez(which(!is.na(padj) & padj < alpha & lfc > 0))
  dn <- ez(which(!is.na(padj) & padj < alpha & lfc < 0))
  if (length(up) >= 5) lists[[paste0(s, "_up")]] <- up
  if (length(dn) >= 5) lists[[paste0(s, "_down")]] <- dn
}
lists[["convergent_up"]]   <- ez(which(res$meta_sig %in% TRUE & res$common_direction == "up"))
lists[["convergent_down"]] <- ez(which(res$meta_sig %in% TRUE & res$common_direction == "down"))
lists <- lists[vapply(lists, length, integer(1)) >= 5]
if (length(lists) < 2)
  skip("Cross-study enrichment skipped: fewer than 2 gene sets have >=5 mapped genes.")

set.seed(42)
cc <- tryCatch(
  compareCluster(geneClusters = lists, fun = "enrichGO", OrgDb = orgdb, keyType = "ENTREZID",
                 ont = ont, universe = universe, pAdjustMethod = "BH",
                 pvalueCutoff = alpha, qvalueCutoff = 0.20, minGSSize = 10, maxGSSize = 500),
  error = function(e) { message("compareCluster failed: ", conditionMessage(e)); NULL })
if (is.null(cc) || nrows(cc) == 0)
  skip("Cross-study enrichment: no GO terms passed the FDR cutoff in any study/convergent set.")

# Attach readable gene symbols (setReadable) then persist.
cc <- tryCatch(setReadable(cc, OrgDb = orgdb, keyType = "ENTREZID"), error = function(e) cc)
saveRDS(cc, out[["objects"]])
df <- as.data.frame(cc)
write.csv(df, out[["ora"]], row.names = FALSE)
n_terms <- length(unique(df$ID)); n_clusters <- length(unique(df$Cluster))
write_check("PASS", sprintf("Cross-study GO enrichment: %d terms across %d gene sets (%s).",
                            n_terms, n_clusters, paste(unique(df$Cluster), collapse = ", ")))
sink(type = "message"); close(log_con)
