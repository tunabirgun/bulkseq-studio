# Aggregate per-sample Salmon quant.sf into the canonical gene-level counts.txt
# (featureCounts layout) via tximport. countsFromAbundance="lengthScaledTPM" makes the
# rounded counts usable directly in standard DESeq2 (no offset needed), so run_deseq2.R
# and the whole downstream are unchanged. Driven by the snakemake S4 object.

suppressMessages({
  library(tximport)
  library(jsonlite)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

quants <- as.character(snakemake@input[["quants"]])
samples <- basename(dirname(quants))           # results/salmon/<sample>/quant.sf -> <sample>
names(quants) <- samples

# tx2gene from gffread's own table (transcript_id <TAB> gene_id), so the transcript
# names match the FASTA / Salmon index exactly -- robust to RefSeq dual XM_/gnl|WGS
# transcript records that a raw-GTF parse would mismatch.
t2g <- read.delim(as.character(snakemake@input[["tx2gene"]]), header = FALSE,
                  stringsAsFactors = FALSE, col.names = c("tx", "gene"))
t2g <- t2g[nzchar(t2g$tx) & nzchar(t2g$gene), ]
message(sprintf("tx2gene: %d transcripts, %d genes", nrow(t2g), length(unique(t2g$gene))))

# ignoreTxVersion = FALSE: tx2gene and the Salmon index FASTA are both produced by the
# same gffread run, so the transcript ids (including any ".N" version, e.g. XM_015766610.2)
# are byte-identical. Stripping the version from only the quant side would unmatch every
# versioned RefSeq transcript and silently drop those genes.
txi <- tximport(quants, type = "salmon", tx2gene = t2g,
                countsFromAbundance = "lengthScaledTPM", ignoreTxVersion = FALSE)
counts <- round(txi$counts)
storage.mode(counts) <- "integer"
genes <- rownames(counts)
len <- round(as.numeric(txi$length[, 1]))

# featureCounts-layout counts.txt: comment line + header (Geneid + 5 meta cols + samples).
out_counts <- snakemake@output[["counts"]]
df <- data.frame(Geneid = genes, Chr = "NA", Start = "NA", End = "NA", Strand = "NA",
                 Length = len, check.names = FALSE, stringsAsFactors = FALSE)
df <- cbind(df, as.data.frame(counts, check.names = FALSE))
writeLines("# salmon + tximport (lengthScaledTPM), featureCounts-compatible layout", out_counts)
suppressWarnings(write.table(df, out_counts, sep = "\t", quote = FALSE,
                             row.names = FALSE, col.names = TRUE, append = TRUE))

# featureCounts-style summary so the 07 quantification check works; the unmapped count
# comes from Salmon's per-sample meta_info.json (num_processed) when available.
assigned <- as.integer(colSums(counts))
unmapped <- integer(length(samples))
for (j in seq_along(samples)) {
  mj <- file.path(dirname(quants[j]), "aux_info", "meta_info.json")
  np <- tryCatch(as.integer(jsonlite::fromJSON(mj)$num_processed), error = function(e) NA_integer_)
  unmapped[j] <- if (is.na(np)) 0L else max(0L, np - assigned[j])
}
out_sum <- snakemake@output[["summary"]]
writeLines(paste(c("Status", samples), collapse = "\t"), out_sum)
cat(paste(c("Assigned", assigned), collapse = "\t"), "\n", sep = "", file = out_sum, append = TRUE)
cat(paste(c("Unassigned_Unmapped", unmapped), collapse = "\t"), "\n", sep = "", file = out_sum, append = TRUE)

sink(type = "message")
close(log_con)
