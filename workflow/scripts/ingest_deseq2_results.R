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

# Ingest the verified project copy of a user-supplied differential-expression table. No
# alignment, counts or DESeq2 are run: the table is normalized into the canonical
# results/deseq2/deseq2_results.csv + up/down sets + a synthetic objects RDS that
# carries no dds/vsd, so enrichment / figures / PPI run downstream unchanged.
# Driven by the Snakemake `script:` directive via the `snakemake` S4 object.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

table_file <- snakemake@input[["table"]]
alpha <- suppressWarnings(as.numeric(snakemake@params[["alpha"]]))
lfc_thr <- suppressWarnings(as.numeric(snakemake@params[["lfc_threshold"]]))
numerator <- tryCatch(as.character(snakemake@params[["numerator"]]), error = function(e) "")
denominator <- tryCatch(as.character(snakemake@params[["denominator"]]), error = function(e) "")
upstream_method <- tryCatch(as.character(snakemake@params[["upstream_method"]]),
                            error = function(e) "unknown")
lfc_shrinkage <- tryCatch(as.character(snakemake@params[["lfc_shrinkage"]]),
                          error = function(e) "unknown")
p_adjustment_method <- tryCatch(as.character(snakemake@params[["p_adjustment_method"]]),
                                error = function(e) "unknown")
if (length(upstream_method) != 1 || !nzchar(trimws(upstream_method))) upstream_method <- "unknown"
if (length(lfc_shrinkage) != 1 || !nzchar(trimws(lfc_shrinkage))) lfc_shrinkage <- "unknown"
if (length(p_adjustment_method) != 1 || !nzchar(trimws(p_adjustment_method))) {
  p_adjustment_method <- "unknown"
}
if (length(alpha) != 1 || is.na(alpha) || alpha <= 0 || alpha >= 1) alpha <- 0.05
if (length(lfc_thr) != 1 || is.na(lfc_thr) || lfc_thr < 0) lfc_thr <- 1.0
if (!nzchar(trimws(numerator)) || !nzchar(trimws(denominator)) ||
    identical(tolower(trimws(numerator)), tolower(trimws(denominator)))) {
  stop("Imported results require two distinct, non-blank source direction labels before ingest.")
}
if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("The required R package 'jsonlite' is not available; cannot write safe validation JSON.")
}

write_check <- function(path, name, status, messages) {
  payload <- list(check = name, status = status, messages = messages)
  jsonlite::write_json(payload, path, auto_unbox = TRUE, pretty = TRUE,
                       null = "null", na = "null")
}

# Read CSV or TSV (by extension), falling back to the other delimiter when the
# first parse yields a single column (a mis-detected separator).
validate_field_counts <- function(counts) {
  # A count of one usually means this is the wrong candidate delimiter; the caller will try the
  # other one. Once the header establishes >=2 fields, every data record must match it. Without
  # this guard read.table can reinterpret a first value as row names and shift scientific columns.
  if (length(counts) > 1 && counts[[1]] >= 2) {
    bad <- which(counts[-1] != counts[[1]]) + 1
    if (length(bad)) {
      stop(sprintf(paste(
        "Data row field count does not match the %d-field header at record(s) %s.",
        "Check for an extra delimiter (for example a decimal comma) or a missing value delimiter."
      ), counts[[1]], paste(head(bad, 5), collapse = ", ")))
    }
  }
}

count_fields_encoded <- function(path, sep, encoding) {
  con <- file(path, open = "rt", encoding = encoding)
  on.exit(close(con))
  count.fields(con, sep = sep, quote = "\"", comment.char = "", blank.lines.skip = TRUE)
}

read_delimited_encoded <- function(path, sep) {
  for (encoding in c("UTF-8", "CP1252", "latin1")) {
    counts <- tryCatch(
      count_fields_encoded(path, sep, encoding),
      warning = function(w) NULL, error = function(e) NULL
    )
    if (is.null(counts)) next
    validate_field_counts(counts)
    df <- tryCatch(
      withCallingHandlers(
        read.delim(path, sep = sep, header = TRUE, check.names = FALSE,
                   stringsAsFactors = FALSE, colClasses = "character", comment.char = "",
                   fileEncoding = encoding),
        warning = function(w) {
          if (grepl("incomplete final line", conditionMessage(w), ignore.case = TRUE)) {
            invokeRestart("muffleWarning")
          }
          stop(w)
        }
      ),
      error = function(e) NULL
    )
    if (!is.null(df)) return(df)
  }
  NULL
}

read_table_any <- function(path) {
  sep <- if (grepl("\\.tsv$|\\.txt$|\\.tab$", path, ignore.case = TRUE)) "\t" else ","
  df <- read_delimited_encoded(path, sep)
  if (is.null(df) || ncol(df) < 2) {
    alt <- if (sep == ",") "\t" else ","
    df2 <- read_delimited_encoded(path, alt)
    if (!is.null(df2) && ncol(df2) >= 2) df <- df2
  }
  if (!is.null(df) && ncol(df)) {
    # UTF-8 BOM is metadata, not part of the first schema name.
    colnames(df)[[1]] <- sub("^\ufeff", "", colnames(df)[[1]])
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
if (is.null(df) || nrow(df) == 0) {
  stop("The external differential-expression project copy is empty or unreadable.")
}

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
# write.csv(as.data.frame(res)) export has gene ids as ROW NAMES. Depending on the
# reader/version, that field is represented as non-default row names, an explicitly unnamed
# column, or pandas' generated "Unnamed: ..." index header. An arbitrary named first column is
# never guessed as an ID because it may be baseMean or another measurement.
rn <- rownames(df)
default_rn <- is.null(rn) || identical(rn, as.character(seq_len(nrow(df))))
unnamed_cols <- which(!nzchar(trimws(colnames(df))))
if (!is.na(col_gene)) {
  gene_id <- trimws(as.character(df[[col_gene]]))
} else if (!default_rn) {
  gene_id <- trimws(rn)
} else if (length(unnamed_cols) == 1) {
  gene_id <- trimws(as.character(df[[unnamed_cols]]))
} else if (ncol(df) && grepl("^unnamed:", colnames(df)[[1]], ignore.case = TRUE)) {
  gene_id <- trimws(as.character(df[[1]]))
} else {
  stop(paste(
    "Required gene identifier column not found.",
    "Use gene_id/gene/id/ensembl, or an R row-name CSV with an unnamed first field."
  ))
}

# Validate identifiers before producing any output. Identifiers become row names and keys in
# several downstream files, so embedded whitespace/control characters are not safe aliases.
if (any(is.na(gene_id) | !nzchar(gene_id))) stop("gene_id column has empty / NA values.")
bad_gene <- grepl("[[:space:][:cntrl:]]", gene_id)
if (any(bad_gene)) {
  examples <- paste(head(gene_id[bad_gene], 5), collapse = ", ")
  stop(sprintf("gene_id values must not contain whitespace/control characters; examples: %s.",
               examples))
}
if (anyDuplicated(gene_id)) {
  dups <- unique(gene_id[duplicated(gene_id)])
  stop(sprintf("gene_id values must be unique; %d duplicated (e.g. %s).",
               length(dups), paste(head(dups, 5), collapse = ", ")))
}

# Convert a supplied numeric column without silently turning malformed tokens into NA. Genuine
# missing values remain NA; every non-missing token must be numeric and finite. These checks all
# run before the first scientific output is written.
strict_numeric <- function(col, label, required = FALSE, lower = NULL, upper = NULL) {
  if (is.na(col)) {
    if (required) stop(sprintf("Required column not found: %s.", label))
    return(rep(NA_real_, length(gene_id)))
  }
  raw_value <- df[[col]]
  token <- trimws(as.character(raw_value))
  missing <- is.na(raw_value) | token == "" | toupper(token) == "NA"
  explicit_nan <- !is.na(raw_value) & toupper(token) %in% c("NAN", "+NAN", "-NAN")
  value <- suppressWarnings(as.numeric(token))
  malformed <- !missing & !explicit_nan & is.na(value)
  if (any(malformed)) {
    rows <- paste(head(which(malformed), 5), collapse = ", ")
    examples <- paste(head(unique(token[malformed]), 5), collapse = ", ")
    stop(sprintf("%s contains non-numeric token(s) at row(s) %s (examples: %s).",
                 label, rows, examples))
  }
  value[missing] <- NA_real_
  nonfinite <- explicit_nan | (!is.na(value) & !is.finite(value))
  if (any(nonfinite)) {
    stop(sprintf("%s contains non-finite value(s) at row(s) %s.", label,
                 paste(head(which(nonfinite), 5), collapse = ", ")))
  }
  if (required && all(is.na(value))) {
    stop(sprintf("%s column has no numeric values.", label))
  }
  outside <- rep(FALSE, length(value))
  if (!is.null(lower)) outside <- outside | (!is.na(value) & value < lower)
  if (!is.null(upper)) outside <- outside | (!is.na(value) & value > upper)
  if (any(outside)) {
    interval <- sprintf("%s%s, %s%s",
                        if (is.null(lower)) "(" else "[", if (is.null(lower)) "-Inf" else lower,
                        if (is.null(upper)) "Inf" else upper, if (is.null(upper)) ")" else "]")
    stop(sprintf("%s contains value(s) outside %s at row(s) %s.", label, interval,
                 paste(head(which(outside), 5), collapse = ", ")))
  }
  value
}

log2FoldChange <- strict_numeric(col_lfc, "log2FoldChange", required = TRUE)
padj <- strict_numeric(col_padj, "adjusted p-value", required = TRUE, lower = 0, upper = 1)
baseMean <- strict_numeric(col_base, "baseMean", lower = 0)
lfcSE <- strict_numeric(col_se, "lfcSE", lower = 0)
pvalue <- strict_numeric(col_pval, "pvalue", lower = 0, upper = 1)
stat <- strict_numeric(col_stat, "stat")

# A supplied test statistic may have a different scale, but its sign must agree with the source
# log2FC direction. Reject contradictions rather than silently flipping either value. Missing
# statistic entries use log2FC solely as a direction-safe placeholder; the external .rnk export
# deliberately ranks all results-only rows by log2FC, not by a source statistic of unknown type.
comparable <- !is.na(stat) & !is.na(log2FoldChange) & stat != 0 & log2FoldChange != 0
contradiction <- comparable & sign(stat) != sign(log2FoldChange)
if (any(contradiction)) {
  stop(sprintf(paste(
    "stat contradicts the confirmed log2FoldChange direction at row(s) %s.",
    "Correct the source table; supplied signs are never changed during ingest."
  ), paste(head(which(contradiction), 5), collapse = ", ")))
}
stat[is.na(stat)] <- log2FoldChange[is.na(stat)]

chr_or_na <- function(col) if (is.na(col)) rep(NA_character_, length(gene_id)) else as.character(df[[col]])
symbol <- chr_or_na(col_sym)
biotype <- chr_or_na(col_bt)

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
             symbol_map = sm, assay_kind = "results_only",
             external_rank_basis = "log2FoldChange"),
        snakemake@output[["rds"]])

# Checks. 08 (design) is informational: no model is fit from a results table.
direction_label <- sprintf("positive log2FC = higher in '%s' than '%s'", numerator, denominator)
write_check(snakemake@output[["design_check"]], "08_metadata_design_qc", "PASS",
            list(list(status = "PASS",
              message = sprintf(paste(
                "Design taken as given from the verified external-results project copy; no local",
                "differential-expression model was fitted (upstream method: %s; LFC shrinkage: %s)."
              ), upstream_method, lfc_shrinkage))))
n_sig <- sum(sig)
adjustment_label <- if (nzchar(trimws(p_adjustment_method)) &&
                         tolower(trimws(p_adjustment_method)) != "unknown") {
  sprintf("adjusted p-value (%s)", trimws(p_adjustment_method))
} else {
  "adjusted p-value (adjustment method not recorded)"
}
write_check(snakemake@output[["deseq_check"]], "09_deseq2_qc",
            if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
            list(list(status = if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
              message = sprintf(paste(
                "Ingested %d genes from the verified external-results project copy; %d genes with %s < %.3g",
                "(%s); %d up / %d down",
                "at |log2FC| >= %.2g."
              ), nrow(res_out), n_sig, adjustment_label, alpha, direction_label,
              nrow(up), nrow(down), lfc_thr))))

writeLines(capture.output(sessionInfo()), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
