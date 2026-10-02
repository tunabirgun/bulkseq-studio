Sys.setenv(OMP_NUM_THREADS = "1", OPENBLAS_NUM_THREADS = "1", MKL_NUM_THREADS = "1")
library(DESeq2)
library(edgeR)
library(limma)
library(apeglm)
library(jsonlite)
BiocParallel::register(BiocParallel::SerialParam())

destination <- file.path(getwd(), "docs_src")
stopifnot(dir.exists(destination))
genes <- sprintf("G%03d", seq_len(480))
samples <- c(paste0("A", 1:4), paste0("B", 1:4))
condition <- factor(rep(c("A", "B"), each = 4), levels = c("A", "B"))
depth <- c(.7, 1.2, .9, 1.5, 1, .8, 1.3, 1)
set.seed(20261001)
baseline <- exp(runif(length(genes), log(8), log(800)))
set.seed(20261002)
truth <- c(runif(60, 1.25, 2.5), -runif(60, 1.25, 2.5), rep(0, length(genes) - 120))
mu <- outer(baseline, depth) * 2^outer(truth, as.integer(condition == "B"))
set.seed(20261003)
cts <- matrix(rnbinom(length(mu), mu = as.vector(mu), size = 1 / .15),
              nrow = length(genes), dimnames = list(genes, samples))
coldata <- data.frame(condition, row.names = samples)
smallest_group <- min(table(condition))
keep <- rowSums(cts >= 10) >= smallest_group
dds <- DESeq2::DESeqDataSetFromMatrix(cts[keep, ], coldata, ~ condition)
dds <- DESeq2::DESeq(dds, parallel = FALSE, quiet = TRUE)
res <- DESeq2::results(dds, contrast = c("condition", "B", "A"), alpha = .05)
shrunk <- DESeq2::lfcShrink(dds, coef = "condition_B_vs_A", type = "apeglm", quiet = TRUE)
transformation <- "vst"
transformed <- tryCatch(DESeq2::vst(dds, blind = FALSE), error = function(e) {
  transformation <<- "rlog"
  DESeq2::rlog(dds, blind = FALSE)
})

design <- model.matrix(~ 0 + condition)
colnames(design) <- levels(condition)
contrast <- limma::makeContrasts(B - A, levels = design)
dge <- edgeR::DGEList(cts, group = condition)
eligible <- edgeR::filterByExpr(dge, design)
dge <- edgeR::calcNormFactors(dge[eligible, , keep.lib.sizes = FALSE])
dispersion <- edgeR::estimateDisp(dge, design)
fit <- edgeR::glmQLFit(dispersion, design)
edge <- edgeR::topTags(edgeR::glmQLFTest(fit, contrast = contrast), n = Inf, sort.by = "none")$table
v <- limma::voom(dge, design, plot = FALSE)
lf <- limma::contrasts.fit(limma::lmFit(v, design), contrast)
voom <- limma::topTable(limma::eBayes(lf, robust = TRUE), number = Inf, sort.by = "none")

records <- function(table, effect, p, adjusted) {
  lapply(seq_len(nrow(table)), function(i) list(id = rownames(table)[i],
    log2fc = unname(table[i, effect]), pvalue = unname(table[i, p]), padj = unname(table[i, adjusted])))
}
matrix_rows <- function(matrix) unname(lapply(seq_len(nrow(matrix)), function(i) unname(matrix[i, ])))
models <- list(
  deseq2 = records(as.data.frame(res), "log2FoldChange", "pvalue", "padj"),
  edger = records(edge, "logFC", "PValue", "FDR"),
  voom = records(voom, "logFC", "P.Value", "adj.P.Val"))
expression <- SummarizedExperiment::assay(transformed)
normalization_counts <- matrix(c(
  100, 200, 100, 1000,
  50, 100, 50, 50,
  80, 160, 80, 80,
  120, 240, 120, 120,
  200, 400, 200, 200,
  40, 80, 40, 40), nrow = 6, byrow = TRUE,
  dimnames = list(paste0("N", 1:6), c("Base", "2x depth", "Same depth", "Composition")))
sf <- DESeq2::estimateSizeFactorsForMatrix(normalization_counts)
totals <- colSums(normalization_counts)
total_factors <- totals / exp(mean(log(totals)))
normalization <- list(samples = colnames(normalization_counts), genes = rownames(normalization_counts),
  counts = matrix_rows(normalization_counts), size_factors = unname(sf), total_factors = unname(total_factors))
packages <- c("DESeq2", "edgeR", "limma", "apeglm", "jsonlite")
result <- list(
  provenance = list(R = as.character(getRversion()), packages = setNames(lapply(packages, function(p) as.character(packageVersion(p))), packages),
    seeds = c(20261001, 20261002, 20261003), dispersion = .15, alpha = .05,
    transformation = transformation, note = "Simulated counts; real method fits. Not a biological study or a full BulkSeq run."),
  samples = samples, genes = genes, counts = matrix_rows(cts), models = models,
  shrunk = lapply(seq_len(nrow(shrunk)), function(i) list(id = rownames(shrunk)[i], log2fc = unname(shrunk$log2FoldChange[i]))),
  expression_genes = rownames(expression), expression = matrix_rows(expression),
  normalization = normalization)
stopifnot(all(is.finite(as.matrix(cts))), all(cts >= 0), ncol(expression) == length(samples))
jsonlite::write_json(result, file.path(destination, "model-examples.json"), auto_unbox = TRUE, digits = NA, na = "null")
cat("Saved method fits for", nrow(cts), "synthetic genes and", ncol(cts), "samples; transformation:", transformation, "\n")
print(sessionInfo())
