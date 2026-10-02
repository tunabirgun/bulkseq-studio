source("workflow/scripts/run_meta_analysis.R")

samples <- data.frame(
  sample_id = paste0("s", seq_len(8)),
  dataset = rep(c("S1", "S2"), each = 4),
  condition = rep(c("A", "A", "B", "B"), 2),
  treatment = rep(c("A", "A", "B", "B"), 2)
)
counts <- matrix(10L, nrow = 2, ncol = 8,
                 dimnames = list(c("g1", "g2"), samples$sample_id))

selected <- function(factor_name, values) {
  sheet <- samples
  sheet[[factor_name]] <- values
  captured <- character()
  DESeqDataSetFromMatrix <- function(countData, colData, design) {
    captured <<- colnames(countData)
    stop("subset captured")
  }
  fn <- per_study_deseq
  environment(fn) <- environment()
  tryCatch(fn(counts, sheet, "dataset", factor_name, "A", "B",
              design = paste("~", factor_name)),
           error = function(e) if (conditionMessage(e) != "subset captured") stop(e))
  captured
}

first_study <- paste0("s", seq_len(4))
stopifnot(identical(selected("condition", samples$condition), first_study))
stopifnot(identical(selected("treatment", samples$treatment), first_study))
stopifnot(length(selected("treatment", paste0(" ", samples$treatment, " "))) == 0L)
cat("META_FACTOR_SUBSET_PASS\n")
