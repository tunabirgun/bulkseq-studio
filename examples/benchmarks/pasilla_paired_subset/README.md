# Pasilla Paired-End Subset Benchmark

This benchmark uses a small paired-end subset of the Brooks et al. pasilla RNAi RNA-seq experiment in Drosophila S2-DRSC cells.

The full experiment is described by the Bioconductor `pasilla` package and GEO series `GSE18508`. This subset keeps one ENA/SRA run from each of four GEO biological samples:

- two untreated samples
- two CG8144 RNAi samples

The subset is intended for validating BulkSeq Studio project generation, metadata validation, SRA/FASTQ configuration, runtime estimation, Snakemake dry-runs, sanity checks, and report generation. For full biological reanalysis, include all technical runs per GEO sample and merge/count them by biological sample.

## Sources

- Bioconductor pasilla package: https://bioconductor.org/packages/release/data/experiment/html/pasilla.html
- GEO series GSE18508: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE18508
- ENA run metadata can be queried from the ENA Portal API by `experiment_accession` for `SRX014459`, `SRX014460`, `SRX014462`, and `SRX014463`.

## Files

- `sra_accessions.txt`: selected SRR runs
- `samples.tsv`: metadata and expected local FASTQ paths
- `benchmark_manifest.yaml`: source metadata and ENA FASTQ URLs

## Expected Contrast

`cg8144_rnai_vs_untreated`

DESeq2 design:

```r
~ condition
```
