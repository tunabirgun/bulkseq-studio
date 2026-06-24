# Ingest a user-supplied DESeq2 results table (bring-your-own results mode). No
# alignment, counts or DESeq2 are run: the table is normalized into the canonical
# results/deseq2/deseq2_results.csv + up/down sets + a synthetic objects RDS that
# carries no dds/vsd, so enrichment / figures / PPI run downstream unchanged.
# Driven by the Snakemake `script:` directive via the `snakemake` S4 object.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

table_file <- snakemake@input[["table"]]
alpha <- suppressWarnings(as.numeric(snakemake@params[["alpha"]]))
lfc_thr <- suppressWarnings(as.numeric(snakemake@params[["lfc_threshold"]]))
con_factor <- tryCatch(as.character(snakemake@params[["contrast_factor"]]), error = function(e) "condition")
numerator <- tryCatch(as.character(snakemake@params[["numerator"]]), error = function(e) "")
denominator <- tryCatch(as.character(snakemake@params[["denominator"]]), error = function(e) "")
if (length(alpha) != 1 || is.na(alpha) || alpha <= 0 || alpha >= 1) alpha <- 0.05
if (length(lfc_thr) != 1 || is.na(lfc_thr) || lfc_thr < 0) lfc_thr <- 1.0

write_check <- function(path, name, status, messages) {
  esc <- function(s) gsub('"', '\\\\"', s)
  msg_json <- paste0(
    sprintf('    {"status": "%s", "message": "%s"}', vapply(messages, `[[`, "", "status"),
            vapply(lapply(messages, `[[`, "message"), esc, "")),
    collapse = ",\n")
  json <- sprintf('{\n  "check": "%s",\n  "status": "%s",\n  "messages": [\n%s\n  ]\n}',
                  name, status, msg_json)
  writeLines(json, path)
}

# Read CSV or TSV (by extension), falling back to the other delimiter when the
# first parse yields a single column (a mis-detected separator).
read_table_any <- function(path) {
  sep <- if (grepl("\\.tsv$|\\.txt$|\\.tab$", path, ignore.case = TRUE)) "\t" else ","
  df <- tryCatch(read.delim(path, sep = sep, header = TRUE, check.names = FALSE,
                            stringsAsFactors = FALSE, comment.char = ""),
                 error = function(e) NULL)
  if (is.null(df) || ncol(df) < 2) {
    alt <- if (sep == ",") "\t" else ","
    df2 <- tryCatch(read.delim(path, sep = alt, header = TRUE, check.names = FALSE,
                               stringsAsFactors = FALSE, comment.char = ""),
                    error = function(e) NULL)
    if (!is.null(df2) && ncol(df2) >= 2) df <- df2
  }
  df
}

# First column whose name matches one of the synonyms (case-insensitive).
pick <- function(df, cands) {
  lc <- tolower(colnames(df))
  for (n in cands) {
    i <- match(tolower(n), lc)
    if (!is.na(i)) return(colnames(df)[i])
  }
  NA_character_
}

df <- read_table_any(table_file)
if (is.null(df) || nrow(df) == 0) stop("DESeq2 results table is empty or unreadable.")

col_gene <- pick(df, c("gene_id", "gene", "geneid", "id", "ensembl", "ensembl_id"))
col_lfc <- pick(df, c("log2FoldChange", "log2fc", "logFC", "log2_fold_change"))
col_padj <- pick(df, c("padj", "adj.P.Val", "FDR", "qvalue", "q_value", "adjp", "p_adj", "padj_BH"))
if (is.na(col_lfc)) stop("Required column not found: log2FoldChange (accepted: log2FoldChange / log2FC / logFC).")
if (is.na(col_padj)) stop("Required column not found: padj (accepted: padj / adj.P.Val / FDR / qvalue).")

col_pval <- pick(df, c("pvalue", "P.Value", "pval", "p_value", "p"))
col_stat <- pick(df, c("stat", "statistic", "t", "z"))
col_base <- pick(df, c("baseMean", "AveExpr", "basemean", "mean_expr"))
col_sym <- pick(df, c("symbol", "gene_name", "genename", "gene_symbol"))
col_se <- pick(df, c("lfcSE", "lfcse", "se"))
col_bt <- pick(df, c("biotype", "gene_biotype", "gene_type"))

# gene_id resolution. A named gene column wins. Otherwise, the common
# write.csv(as.data.frame(res)) export has gene ids as ROW NAMES (its header is one
# field short, so read.* assigns them to row.names and the first data column is
# baseMean) — detect non-default row names and use them. Only then fall back to the
# first column.
rn <- rownames(df)
default_rn <- is.null(rn) || identical(rn, as.character(seq_len(nrow(df))))
if (!is.na(col_gene)) {
  gene_id <- trimws(as.character(df[[col_gene]]))
} else if (!default_rn) {
  gene_id <- trimws(rn)
} else {
  gene_id <- trimws(as.character(df[[1]]))
}
log2FoldChange <- suppressWarnings(as.numeric(df[[col_lfc]]))
padj <- suppressWarnings(as.numeric(df[[col_padj]]))

# Validate the required columns.
if (any(is.na(gene_id) | !nzchar(gene_id))) stop("gene_id column has empty / NA values.")
if (anyDuplicated(gene_id)) {
  dups <- unique(gene_id[duplicated(gene_id)])
  stop(sprintf("gene_id values must be unique; %d duplicated (e.g. %s).",
               length(dups), paste(head(dups, 5), collapse = ", ")))
}
if (all(is.na(log2FoldChange))) stop("log2FoldChange column is non-numeric throughout.")
if (all(is.na(padj))) stop("padj column is non-numeric throughout.")

num_or_na <- function(col) if (is.na(col)) rep(NA_real_, length(gene_id)) else suppressWarnings(as.numeric(df[[col]]))
chr_or_na <- function(col) if (is.na(col)) rep(NA_character_, length(gene_id)) else as.character(df[[col]])
baseMean <- num_or_na(col_base)
lfcSE <- num_or_na(col_se)
pvalue <- num_or_na(col_pval)
stat <- num_or_na(col_stat)
symbol <- chr_or_na(col_sym)
biotype <- chr_or_na(col_bt)

# Synthesize a ranking statistic when absent (used by the .rnk export / GSEA):
# signed -log10(padj), matching the fold-change direction.
if (all(is.na(stat))) {
  stat <- sign(log2FoldChange) * -log10(pmax(padj, .Machine$double.xmin))
}

# Canonical column order, identical to run_deseq2.R's results CSV so enrichment,
# PPI and the figures consume it unchanged.
res_out <- data.frame(baseMean = baseMean, log2FoldChange = log2FoldChange,
                      lfcSE = lfcSE, stat = stat, pvalue = pvalue, padj = padj,
                      gene_id = gene_id, symbol = symbol, biotype = biotype,
                      stringsAsFactors = FALSE)
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)

# Up / down sets: padj < alpha AND |log2FoldChange| >= threshold (mirrors run_deseq2.R).
sig <- !is.na(res_out$padj) & res_out$padj < alpha
up <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange >= lfc_thr, ]
down <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange <= -lfc_thr, ]
up <- up[order(-up$log2FoldChange), ]
down <- down[order(down$log2FoldChange), ]
write.csv(up, snakemake@output[["up"]], row.names = FALSE)
write.csv(down, snakemake@output[["down"]], row.names = FALSE)

# Synthetic objects RDS: res / resLFC as a results-like data.frame (rownames =
# gene_id), no dds / vsd (the figures gate count/VST plots on those), plus a
# gene_id -> symbol map for labels.
res_rds <- res_out[, c("baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj")]
rownames(res_rds) <- res_out$gene_id
sm <- setNames(res_out$symbol, res_out$gene_id)
saveRDS(list(dds = NULL, vsd = NULL, res = res_rds, resLFC = res_rds,
             symbol_map = sm, assay_kind = "results_only"),
        snakemake@output[["rds"]])

# Checks. 08 (design) is informational: no model is fit from a results table.
coef_name <- if (nzchar(numerator) && nzchar(denominator))
  paste0(con_factor, "_", numerator, "_vs_", denominator) else con_factor
write_check(snakemake@output[["design_check"]], "08_metadata_design_qc", "PASS",
            list(list(status = "PASS",
              message = "Design taken as given: differential expression was computed externally and uploaded as a results table.")))
n_sig <- sum(sig)
write_check(snakemake@output[["deseq_check"]], "09_deseq2_qc",
            if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
            list(list(status = if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
              message = sprintf("Ingested %d genes from the uploaded results table; %d padj < %.3g (%s); %d up / %d down at |log2FC| >= %.2g.",
                                nrow(res_out), n_sig, alpha, coef_name, nrow(up), nrow(down), lfc_thr))))

writeLines(capture.output(sessionInfo()), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
