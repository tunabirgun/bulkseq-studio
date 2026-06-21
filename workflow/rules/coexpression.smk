# Correlation / co-expression outputs (0.6.0). Sample-sample correlation reads
# the normalized matrix from deseq2_objects.rds, so it works on every backend
# (RNA-seq, count-matrix, microarray) with no new dependency.


rule sample_correlation:
    input:
        rds="results/deseq2/deseq2_objects.rds",
    output:
        pearson_png="results/figures/sample_correlation_pearson.png",
        pearson_svg="results/figures/sample_correlation_pearson.svg",
        spearman_png="results/figures/sample_correlation_spearman.png",
        spearman_svg="results/figures/sample_correlation_spearman.svg",
        pearson_csv="results/export/sample_correlation_pearson.csv",
        spearman_csv="results/export/sample_correlation_spearman.csv",
    params:
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/sample_correlation.tsv"
    log:
        "logs/sample_correlation.log",
    script:
        "../scripts/sample_correlation.R"
