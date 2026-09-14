# Fusarium graminearum Z-3639 Heat-Shock Benchmark

This benchmark uses all six paired-end runs of the *Fusarium graminearum* heat-stress study deposited as BioProject `PRJNA314297` (SRA study `SRP071140`, GEO series `GSE78885`): 24 h-old mycelia of wild-type strain Z-3639 incubated for 15 min at 25 °C (three replicates) or 37 °C (three replicates), 151 bp mates on an Illumina HiSeq 2000.

It is the catalogue's strandedness case. The library is reverse-stranded, so counting it as unstranded changes the differential result rather than merely slowing the run down: a strandedness-detection regression shows up here as a different DE gene set.

## Sources

- BioProject: https://www.ebi.ac.uk/ena/browser/view/PRJNA314297
- GEO series GSE78885: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE78885
- Publication linked from the ENA record: Bui D-C, Lee Y, Lim JY, Fu M, Kim J-C, Choi GJ, Son H, Lee Y-W. Heat shock protein 90 is required for sexual and asexual development, virulence, and heat shock response in *Fusarium graminearum*. *Scientific Reports* 6:28154 (2016). DOI 10.1038/srep28154, PubMed 27306495.
- Run metadata (FASTQ URLs, MD5 checksums, byte, read and base counts) comes from the ENA Portal API `filereport` for `SRP071140`.
- Reference: NCBI RefSeq `GCF_000240135.3` (ASM24013v3), the bundled *Fusarium graminearum* PH-1 preset.

## Strain and Reference

The sequenced strain is the wild type Z-3639 (ENA `scientific_name` *Fusarium graminearum*, taxon 5518). Reads are mapped to the PH-1 reference preset, and the PH-1 taxon 229533 is used as the STRING species proxy. Strain-specific loci must be interpreted with that mismatch in mind.

## Files

- `sra_accessions.txt`: the six SRR runs
- `samples.tsv`: metadata, expected local FASTQ paths, ENA URLs and checksums
- `benchmark_manifest.yaml`: source metadata, selection policy and the recorded expectation

## Expected Contrast

`temp_37_vs_temp_25`, with `temp_25` as the reference level.

DESeq2 design:

```r
~ condition
```

## Recorded Expectation

The 0.11.0 changelog records 5,836 differentially expressed genes for this dataset on the STAR + featureCounts + DESeq2 route counted at `-s 2`, and 5,812 on the HISAT2 route in the same validation. It states no significance threshold alongside those counts.

The 0.31.0 reproduction supplies that threshold. Running the study from FASTQ under workflow version 0.31.0 on 2026-09-13 returned the same 5,836 genes at `padj < 0.05` with no log2 fold-change filter, 1,996 of them up and 1,255 down at `|log2FC| > 1`, from a count matrix identical to a v0.29.1 run of the same reads. Strandedness detection returned 2 on every one of the six samples, at reverse-strand ratios between 0.9671 and 0.9823.
