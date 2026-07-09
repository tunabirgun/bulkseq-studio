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

# Ingest a GEO/GSE microarray dataset into a normalized, gene-level log2
# expression matrix (0.4.0). Two sources: a GEOquery series matrix (submitter-
# normalized; the common case) or raw Affymetrix CEL -> RMA. Probes are collapsed
# to unique gene symbols (MaxMean). Output feeds run_limma.R; figures/enrichment
# then reuse the shared DESeq2-shaped path.

suppressMessages({
  library(GEOquery)
  library(Biobase)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")
options(timeout = 600)

gse <- snakemake@params[["gse"]]
platform <- snakemake@params[["platform"]]
source_kind <- snakemake@params[["source"]]      # geo_series_matrix | affy_cel
norm_kind <- snakemake@params[["normalization"]] # auto | rma | none
log2_opt <- snakemake@params[["log2_transform"]] # auto | yes | no
samples_file <- snakemake@input[["samples"]]
out_expr <- snakemake@output[["expression"]]
out_map <- snakemake@output[["probe_map"]]
out_info <- snakemake@output[["norm_info"]]
out_norm_check <- snakemake@output[["norm_check"]]
out_map_check <- snakemake@output[["map_check"]]
workdir <- dirname(out_expr)
dir.create(workdir, showWarnings = FALSE, recursive = TRUE)

write_check <- function(path, name, status, messages) {
  esc <- function(s) gsub('"', '\\\\"', s)
  msg_json <- paste0(
    sprintf('    {"status": "%s", "message": "%s"}', vapply(messages, `[[`, "", "status"),
            vapply(lapply(messages, `[[`, "message"), esc, "")),
    collapse = ",\n")
  writeLines(sprintf('{\n  "check": "%s",\n  "status": "%s",\n  "messages": [\n%s\n  ]\n}',
                     name, status, msg_json), path)
}

# Progress markers go to stdout (NOT the message sink), so Snakemake's console shows how
# far the ingest got even when the sinked log is empty. `step()` timestamps each stage.
step <- function(msg) cat(sprintf("[ingest_geo] %s\n", msg))

# On ANY uncaught error: restore stderr from the log sink and re-emit the reason there, so
# Snakemake reports the actual cause inline instead of the cryptic "job completed
# successfully, but some output files are missing". Also write FAILED check files (so the
# sanity aggregation records it) and exit non-zero so the run stops with a clear message.
options(error = function() {
  err <- geterrmessage()
  try(sink(type = "message"), silent = TRUE)
  cat(sprintf("[ingest_geo] FAILED: %s\n", err))
  for (p in c(out_norm_check, out_map_check)) {
    try(write_check(p, if (identical(p, out_norm_check)) "11_normalization_qc" else "12_probe_mapping_qc",
                    "FAIL", list(list(status = "FAIL", message = paste("GEO ingest failed:", err)))),
        silent = TRUE)
  }
  quit(save = "no", status = 1)
})

step(sprintf("starting: gse=%s source=%s platform=%s", gse, source_kind, platform))

# ---- 1. Obtain a probe-level expression matrix + feature annotation ----------
fdata <- NULL
if (identical(source_kind, "local_matrix")) {
  # User-supplied gene x sample expression matrix (any platform, already processed): read it
  # directly with no GEO download. Rows are treated as gene-level (identity probe->gene below).
  matrix_path <- snakemake@params[["matrix"]]
  sep <- if (grepl("\\.csv$", matrix_path, ignore.case = TRUE)) "," else "\t"
  raw <- read.delim(matrix_path, sep = sep, header = TRUE, check.names = FALSE,
                    stringsAsFactors = FALSE, comment.char = "#")
  if (ncol(raw) < 2) stop("Microarray expression matrix needs a gene-id column plus at least one sample column.")
  exprs_mat <- suppressWarnings(data.matrix(raw[, -1, drop = FALSE]))
  rownames(exprs_mat) <- as.character(raw[[1]])
  norm_method <- "User-supplied expression matrix (local)"
  already_log2 <- FALSE
} else if (identical(source_kind, "affy_cel")) {
  suppressMessages(library(affy))
  supp <- getGEOSuppFiles(gse, makeDirectory = TRUE, baseDir = workdir)
  tarball <- rownames(supp)[grepl("_RAW\\.tar$", rownames(supp))][1]
  celdir <- file.path(workdir, gse, "cel")
  dir.create(celdir, showWarnings = FALSE, recursive = TRUE)
  utils::untar(tarball, exdir = celdir)
  cels <- list.files(celdir, pattern = "\\.CEL(\\.gz)?$", ignore.case = TRUE, full.names = TRUE)
  if (!length(cels)) stop("No CEL files found in the GEO supplementary archive.")
  ab <- affy::ReadAffy(filenames = cels)
  eset <- affy::rma(ab)              # RMA output is already background-corrected + log2
  exprs_mat <- exprs(eset)
  # GEO _RAW.tar CEL files are named "GSM..._descriptor.CEL"; reduce each column to the bare
  # GSM accession so it matches samples.tsv sample_id (else the reconciliation step below
  # aborts the run). Fall back to the suffix-stripped basename when there is no GSM prefix.
  cel_base <- sub("\\.CEL(\\.gz)?$", "", basename(colnames(exprs_mat)), ignore.case = TRUE)
  colnames(exprs_mat) <- ifelse(grepl("^GSM[0-9]+", cel_base),
                                sub("^(GSM[0-9]+).*", "\\1", cel_base), cel_base)
  norm_method <- "RMA (affy)"
  already_log2 <- TRUE
  # Annotation comes from the platform record (CEL exprs has no fData).
  gpl <- tryCatch(getGEO(platform, destdir = workdir), error = function(e) NULL)
  if (!is.null(gpl)) fdata <- Table(gpl)
} else {
  if (!nzchar(gse)) stop("no GEO accession set (microarray.gse_accession is empty).")
  step(sprintf("downloading GEO series matrix for %s (this can take a minute)...", gse))
  gseList <- getGEO(gse, GSEMatrix = TRUE, AnnotGPL = TRUE, destdir = workdir)
  # getGEO can return an empty list on a network/parse problem without erroring; catch it
  # here so the run fails with a clear reason instead of producing nothing.
  if (is.null(gseList) || length(gseList) == 0)
    stop(sprintf("getGEO returned no series for %s (network, wrong accession, or GEO parse failure).", gse))
  pick <- 1
  if (length(gseList) > 1 && nzchar(platform)) {
    hit <- which(vapply(gseList, function(es) identical(annotation(es), platform), logical(1)))
    if (length(hit)) pick <- hit[1]
  }
  eset <- gseList[[pick]]
  exprs_mat <- exprs(eset)
  fdata <- fData(eset)
  norm_method <- "GEO series matrix (submitter-normalized)"
  already_log2 <- FALSE
  step(sprintf("downloaded: %d probes x %d samples", nrow(exprs_mat), ncol(exprs_mat)))
}
# A GEO series with no expression values (empty exprs, or a supplementary-only record)
# would otherwise slip through and write empty outputs; fail loudly instead.
if (is.null(exprs_mat) || nrow(exprs_mat) < 1 || ncol(exprs_mat) < 1)
  stop("the GEO record has no usable expression matrix (0 probes or 0 samples).")

# ---- 2. log2 transform decision (GEO2R quantile heuristic) -------------------
applied_log2 <- FALSE
log2_reason <- ""
if (identical(log2_opt, "yes")) {
  exprs_mat[exprs_mat <= 0] <- NA
  exprs_mat <- log2(exprs_mat); applied_log2 <- TRUE
  log2_reason <- "forced (log2_transform=yes)"
} else if (identical(log2_opt, "no") || already_log2) {
  applied_log2 <- FALSE
  log2_reason <- if (already_log2) "skipped (RMA output already log2)" else "skipped (log2_transform=no)"
} else {  # auto: GEO2R quantile heuristic; record the decision so a misclassified
          # distribution (bimodal / zero-inflated) can be diagnosed afterwards.
  qx <- as.numeric(stats::quantile(exprs_mat, c(0, 0.25, 0.5, 0.75, 0.99, 1.0), na.rm = TRUE))
  log_c <- (qx[5] > 100) || (qx[6] - qx[1] > 50 && qx[2] > 0)
  log2_reason <- sprintf("auto: max=%.1f, 99%%=%.1f, Q1=%.1f, range=%.1f -> %s",
                         qx[6], qx[5], qx[2], qx[6] - qx[1], if (log_c) "log2 applied" else "no log2")
  if (log_c) {
    exprs_mat[exprs_mat <= 0] <- NA
    exprs_mat <- log2(exprs_mat); applied_log2 <- TRUE
  }
}

n_probes <- nrow(exprs_mat)
norm_messages <- list(list(status = "PASS",
  message = sprintf("%d probes x %d samples; %s; log2 %s [%s].",
                    n_probes, ncol(exprs_mat), norm_method,
                    if (applied_log2) "applied" else (if (already_log2) "already (RMA)" else "not needed"),
                    log2_reason)))
write_check(out_norm_check, "11_normalization_qc", "PASS", norm_messages)

# ---- 3. Probe -> gene symbol (synonym resolver) -----------------------------
find_symbol_col <- function(df) {
  if (is.null(df) || !ncol(df)) return(NULL)
  cands <- c("Gene Symbol", "Gene symbol", "GENE_SYMBOL", "Symbol", "SYMBOL",
             "gene_symbol", "ILMN_Gene", "GeneSymbol", "Gene_Symbol")
  hit <- intersect(cands, colnames(df))
  if (length(hit)) return(hit[1])
  # Affy ST gene_assignment: "NM_x // SYMBOL // desc // ...".
  if ("gene_assignment" %in% colnames(df)) return("gene_assignment")
  loose <- grep("symbol", colnames(df), ignore.case = TRUE, value = TRUE)
  if (length(loose)) loose[1] else NULL
}
id_col <- if (!is.null(fdata)) {
  ic <- intersect(c("ID", "id", "ProbeName", "probe"), colnames(fdata))
  if (length(ic)) ic[1] else colnames(fdata)[1]
} else NULL
sym_col <- find_symbol_col(fdata)

probe_ids <- rownames(exprs_mat)
symbols <- rep(NA_character_, length(probe_ids))
if (identical(source_kind, "local_matrix")) {
  symbols <- probe_ids   # rows are already gene-level; map each id to itself
} else if (!is.null(fdata) && !is.null(sym_col) && !is.null(id_col)) {
  key <- as.character(fdata[[id_col]])
  raw <- as.character(fdata[[sym_col]])
  if (identical(sym_col, "gene_assignment")) {
    raw <- vapply(strsplit(raw, "//", fixed = TRUE), function(p) if (length(p) >= 2) trimws(p[2]) else NA_character_, "")
  } else {
    raw <- trimws(sub("[ ]*//.*$", "", raw))            # "SYM // SYM2" -> first
    raw <- trimws(sub("[ ]*///.*$", "", raw))           # GPL "SYM /// SYM2" -> first
  }
  raw[!nzchar(raw) | raw %in% c("---", "NA")] <- NA
  symbols <- raw[match(probe_ids, key)]
}

probe_map <- data.frame(probe = probe_ids, gene_id = symbols, stringsAsFactors = FALSE)
write.table(probe_map, out_map, sep = "\t", quote = FALSE, row.names = FALSE)

mapped <- !is.na(symbols) & nzchar(symbols)
map_rate <- if (length(symbols)) mean(mapped) else 0
if (sum(mapped) < 1) {
  write_check(out_map_check, "12_probe_mapping_qc", "FAIL",
              list(list(status = "FAIL", message = "No probes mapped to a gene symbol; check the platform annotation.")))
  stop("Probe->gene mapping produced no symbols.")
}

# ---- 4. Collapse probes to unique genes by MaxMean --------------------------
em <- exprs_mat[mapped, , drop = FALSE]
sym <- symbols[mapped]
probe_mean <- rowMeans(em, na.rm = TRUE)
ord <- order(probe_mean, decreasing = TRUE)
em <- em[ord, , drop = FALSE]; sym_ord <- sym[ord]
keep <- !duplicated(sym_ord)
gene_mat <- em[keep, , drop = FALSE]
rownames(gene_mat) <- sym_ord[keep]
gene_mat <- gene_mat[order(rownames(gene_mat)), , drop = FALSE]

# Drop genes whose collapsed intensity is mostly missing (e.g. all probes were
# <= 0 and became NA at log2); limma would otherwise return NaN stats that are
# silently filtered downstream, hiding the data-quality loss.
na_frac <- rowMeans(is.na(gene_mat))
n_dropped_na <- sum(na_frac > 0.5)
gene_mat <- gene_mat[na_frac <= 0.5, , drop = FALSE]

map_status <- if (map_rate >= 0.5) "PASS" else "REVIEW_REQUIRED"
drop_note <- if (n_dropped_na > 0) sprintf(" Dropped %d gene(s) with >50%% missing intensity.", n_dropped_na) else ""
write_check(out_map_check, "12_probe_mapping_qc", map_status,
            list(list(status = map_status,
                      message = sprintf("%.1f%% of probes mapped to a symbol; %d unique genes.%s",
                                        100 * map_rate, nrow(gene_mat), drop_note))))

# ---- 5. Validate / order sample columns against samples.tsv -----------------
samples <- read.delim(samples_file, stringsAsFactors = FALSE)
ids <- as.character(samples$sample_id)
have <- colnames(gene_mat)
missing <- setdiff(ids, have)
if (length(missing)) {
  stop(sprintf("samples.tsv sample_id(s) not found among GEO sample columns: %s\nMatrix columns: %s",
               paste(missing, collapse = ", "), paste(head(have, 50), collapse = ", ")))
}
gene_mat <- gene_mat[, ids, drop = FALSE]

# ---- 6. Write the gene x sample matrix + normalization provenance -----------
out_df <- data.frame(gene_id = rownames(gene_mat), gene_mat, check.names = FALSE, stringsAsFactors = FALSE)
write.table(out_df, out_expr, sep = "\t", quote = FALSE, row.names = FALSE)

info <- sprintf(paste0('{\n  "gse": "%s",\n  "platform": "%s",\n  "source": "%s",\n',
                       '  "normalization": "%s",\n  "log2_applied": %s,\n',
                       '  "n_probes": %d,\n  "n_genes": %d,\n  "n_samples": %d,\n',
                       '  "probe_to_gene": "MaxMean collapse",\n  "symbol_map_rate": %.4f\n}'),
                gse, platform, source_kind, norm_method,
                if (applied_log2 || already_log2) "true" else "false",
                n_probes, nrow(gene_mat), ncol(gene_mat), map_rate)
writeLines(info, out_info)

# Confirm every declared output was actually written (catches a silent partial write:
# the exact "job finished but outputs are missing" symptom). Clear the error handler
# first so a failure here reports plainly rather than re-entering it.
options(error = NULL)
declared <- c(out_expr, out_map, out_info, out_norm_check, out_map_check)
absent <- declared[!file.exists(declared)]
if (length(absent)) {
  sink(type = "message")
  stop(sprintf("ingest finished but these outputs were not written: %s",
               paste(basename(absent), collapse = ", ")))
}
step(sprintf("done: %d genes x %d samples written.", nrow(gene_mat), ncol(gene_mat)))

sink(type = "message")
close(log_con)
