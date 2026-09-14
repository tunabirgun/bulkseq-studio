# Fusarium graminearum PH-1 Spores vs Mycelium Benchmark

This benchmark uses all six paired-end runs of the *Fusarium graminearum* PH-1 developmental study deposited as BioProject `PRJNA239711` (SRA study `SRP039087`, GEO series `GSE55477`): three spore and three mycelium biological replicates, 90 bp mates on an Illumina HiSeq 2000.

It is the primary fungal guardrail benchmark. The genome is compact (~36 Mb), the spore-versus-mycelium contrast produces a large and stable differential signal, and the library is unstranded, so one run exercises the STAR + featureCounts + DESeq2 route together with the KEGG (`fgr`) and STRING (taxon 229533) fungal enrichment path.

## Sources

- BioProject: https://www.ebi.ac.uk/ena/browser/view/PRJNA239711
- GEO series GSE55477: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE55477
- Publication linked from the ENA record: Zhao C, Waalwijk C, de Wit PJGM, Tang D, van der Lee T. Relocation of genes generates non-conserved chromosomal segments in *Fusarium graminearum* that show distinct and co-regulated gene expression patterns. *BMC Genomics* 15:191 (2014). DOI 10.1186/1471-2164-15-191, PubMed 24625133.
- Run metadata (FASTQ URLs, MD5 checksums, byte, read and base counts) comes from the ENA Portal API `filereport` for `SRP039087`.
- Reference: NCBI RefSeq `GCF_000240135.3` (ASM24013v3), the bundled *Fusarium graminearum* PH-1 preset.

## Files

- `sra_accessions.txt`: the six SRR runs
- `samples.tsv`: metadata, expected local FASTQ paths, ENA URLs and checksums
- `benchmark_manifest.yaml`: source metadata, selection policy and the recorded expectation

## Expected Contrast

`spores_vs_mycelium`, with `mycelium` as the reference level.

DESeq2 design:

```r
~ condition
```

## Recorded Expectation

Earlier 0.2x-era runs of this dataset (STAR + featureCounts + DESeq2) reported 5,734 differentially expressed genes at `padj < 0.05` with no log2 fold-change filter; of those, 2,723 were up and 2,478 down at `|log2FC| > 1`. The library is unstranded, and the pipeline's strandedness detection is expected to report 0.

Those counts were reproduced from FASTQ under workflow version 0.31.0 on 2026-09-13. The count matrix and the DESeq2 table are identical to a v0.29.1 run of the same reads: 9,028 tested genes, 5,734 at `padj < 0.05`, and 2,723 up against 2,478 down at `|log2FC| > 1`. Strandedness detection returned 0 on every one of the six samples, at reverse-strand ratios between 0.4911 and 0.5041.
