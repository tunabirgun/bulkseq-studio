# H5: salmon_tximport.R must fail loudly, naming the likely prebuilt-index cause, when a
# quant.sf transcript is absent from tx2gene -- rather than let tximport silently drop it.
# Run in the bulkseq env: micromamba run -n bulkseq Rscript tests/test_salmon_tximport_mismatch.R

setClass("FakeSnakemake", representation(input = "list", output = "list", params = "list", log = "list"))

write_quant <- function(path, names) {
  df <- data.frame(Name = names, Length = 1000, EffectiveLength = 950, TPM = 10, NumReads = 100)
  write.table(df, path, sep = "\t", quote = FALSE, row.names = FALSE)
}

run_script <- function(tx2gene_names, quant_names, workdir) {
  quant_dir <- file.path(workdir, "results", "salmon", "sample1")
  dir.create(quant_dir, recursive = TRUE)
  quant_path <- file.path(quant_dir, "quant.sf")
  write_quant(quant_path, quant_names)

  tx2gene_path <- file.path(workdir, "tx2gene.tsv")
  writeLines(paste(tx2gene_names, paste0("gene", seq_along(tx2gene_names)), sep = "\t"), tx2gene_path)

  snakemake <- new("FakeSnakemake",
    input = list(quants = quant_path, tx2gene = tx2gene_path),
    output = list(counts = file.path(workdir, "counts.txt"), summary = file.path(workdir, "counts.txt.summary")),
    params = list(), log = list(file.path(workdir, "log.txt")))
  assign("snakemake", snakemake, envir = globalenv())
  source("workflow/scripts/salmon_tximport.R", local = new.env(parent = globalenv()))
}

ok <- TRUE
check <- function(cond, msg) {
  if (!isTRUE(cond)) { cat("FAIL:", msg, "\n"); ok <<- FALSE } else cat("ok:", msg, "\n")
}

## ---- negative: a quant.sf transcript absent from tx2gene must stop with a named cause ----
workdir <- tempfile("mismatch_")
dir.create(workdir)
err <- tryCatch({
  run_script(c("tx1", "tx2"), c("tx1", "tx2", "tx3_not_in_tx2gene"), workdir)
  NULL
}, error = function(e) conditionMessage(e))
check(!is.null(err), "mismatched transcript name raises an error")
check(!is.null(err) && grepl("tx3_not_in_tx2gene", err), "error names the first unmatched transcript")
check(!is.null(err) && grepl("salmon_index|transcriptome_fasta", err),
      "error names the prebuilt-index settings as the likely cause")
unlink(workdir, recursive = TRUE)

## ---- positive: every quant.sf transcript present in tx2gene must not raise ----
workdir2 <- tempfile("match_")
dir.create(workdir2)
err2 <- tryCatch({
  run_script(c("tx1", "tx2"), c("tx1", "tx2"), workdir2)
  NULL
}, error = function(e) conditionMessage(e))
check(is.null(err2), paste("fully matched transcripts run without error (got:", err2, ")"))
check(file.exists(file.path(workdir2, "counts.txt")), "counts.txt is written on the matched path")
unlink(workdir2, recursive = TRUE)

if (!ok) { cat("SOME TESTS FAILED\n"); quit(status = 1) } else cat("ALL TESTS PASSED\n")
