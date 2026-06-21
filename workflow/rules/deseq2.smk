# Differential expression with DESeq2 (protocol sections 7.1-7.4).
# The R script reads the snakemake object for inputs, params, and outputs.

_DE = config.get("deseq2", {})
_CONTRAST = (_DE.get("contrasts") or [{}])[0]
_REF = _DE.get("reference_level", {}) or {}
_REF_FACTOR = next(iter(_REF), "condition")


if MICROARRAY_MODE:

    # Microarray (continuous intensities): limma instead of DESeq2. Emits the
    # SAME output files as `rule deseq2` so figures/enrichment/GOI are unchanged.
    rule limma_de:
        input:
            expression="results/microarray/normalized_expression.tsv",
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            normalized="results/deseq2/normalized_counts.csv",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            design=_DE.get("design_formula", "~ condition"),
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
        benchmark:
            "benchmarks/limma_de.tsv"
        log:
            "logs/limma_de.log",
        script:
            "../scripts/run_limma.R"

else:

    rule deseq2:
        input:
            counts="results/counts/counts.txt",
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            normalized="results/deseq2/normalized_counts.csv",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            design=_DE.get("design_formula", "~ condition"),
            ref_factor=_REF_FACTOR,
            ref_level=_REF.get(_REF_FACTOR, ""),
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
            min_count=_DE.get("min_count", 10),
            shrink=_DE.get("shrinkage_method", "apeglm"),
            # Path (not input) so count-matrix mode, which has no reference, still
            # runs; run_deseq2.R reads symbol/biotype from it only when it exists.
            gtf=ANNOTATION_GTF,
        benchmark:
            "benchmarks/deseq2.tsv"
        log:
            "logs/deseq2.log",
        script:
            "../scripts/run_deseq2.R"
