# Transcriptomics figures (protocol section 9). Each figure is exported as both
# PNG (raster) and SVG (vector); titles are kept out of the figure.


rule figures:
    input:
        rds="results/deseq2/deseq2_objects.rds",
    output:
        pca_png="results/figures/pca.png",
        pca_svg="results/figures/pca.svg",
        dist_png="results/figures/sample_distance.png",
        dist_svg="results/figures/sample_distance.svg",
        ma_png="results/figures/ma_plot.png",
        ma_svg="results/figures/ma_plot.svg",
        volcano_png="results/figures/volcano.png",
        volcano_svg="results/figures/volcano.svg",
        heatmap_png="results/figures/top_deg_heatmap.png",
        heatmap_svg="results/figures/top_deg_heatmap.svg",
        up_heatmap_png="results/figures/top_upregulated_heatmap.png",
        up_heatmap_svg="results/figures/top_upregulated_heatmap.svg",
        down_heatmap_png="results/figures/top_downregulated_heatmap.png",
        down_heatmap_svg="results/figures/top_downregulated_heatmap.svg",
        pval_png="results/figures/pvalue_histogram.png",
        pval_svg="results/figures/pvalue_histogram.svg",
        disp_png="results/figures/dispersion.png",
        disp_svg="results/figures/dispersion.svg",
        cooks_png="results/figures/cooks_distance.png",
        cooks_svg="results/figures/cooks_distance.svg",
        libsize_png="results/figures/library_size.png",
        libsize_svg="results/figures/library_size.svg",
    params:
        # Declared so a figure-style change is a Snakemake rerun trigger (the
        # script reads style from params, not config). Without this, editing the
        # style in the GUI would not re-render the figures on the next run.
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/figures.tsv"
    log:
        "logs/figures.log",
    script:
        "../scripts/make_figures.R"


# Genes-of-interest: focused heatmap + per-gene expression across conditions.
# Only active when a custom gene list is configured (gene_sets.custom_gene_list).
# The rule is defined ONLY when a gene list exists, so it can never be invoked
# with an empty `genes` input (which would crash make_goi.R at readLines()).
_GOI = config.get("gene_sets", {}).get("custom_gene_list")


if _GOI:

    rule genes_of_interest:
        input:
            rds="results/deseq2/deseq2_objects.rds",
            genes=_GOI,
            de_results="results/deseq2/deseq2_results.csv",
        output:
            heatmap_png="results/figures/goi_heatmap.png",
            heatmap_svg="results/figures/goi_heatmap.svg",
            expr_png="results/figures/goi_expression.png",
            expr_svg="results/figures/goi_expression.svg",
            csv="results/genes_of_interest/goi_normalized_counts.csv",
            report="results/genes_of_interest/goi_report.txt",
            de_slice="results/genes_of_interest/goi_deseq2_results.csv",
            log2fc_png="results/figures/goi_log2fc.png",
            log2fc_svg="results/figures/goi_log2fc.svg",
        params:
            style=config.get("figures_style", {}),
        benchmark:
            "benchmarks/genes_of_interest.tsv"
        log:
            "logs/genes_of_interest.log",
        script:
            "../scripts/make_goi.R"


# Enrichment-term heatmap: reuses make_goi.R on the gene list of a chosen enrichment term
# (written by the app to config/enrichment_term.txt), producing a focused heatmap without
# re-running the pipeline. Always defined (never in `rule all`); reached only via
# --allowed-rules from the app's "term" mode. All nine make_goi.R outputs are provided so the
# script never KeyErrors; outputs land in results/figures/ so the existing gallery shows them.
rule enrichment_term_heatmap:
    input:
        rds="results/deseq2/deseq2_objects.rds",
        genes="config/enrichment_term.txt",
        de_results="results/deseq2/deseq2_results.csv",
    output:
        heatmap_png="results/figures/term_heatmap.png",
        heatmap_svg="results/figures/term_heatmap.svg",
        expr_png="results/figures/term_expression.png",
        expr_svg="results/figures/term_expression.svg",
        csv="results/enrichment/terms/term_normalized_counts.csv",
        report="results/enrichment/terms/term_report.txt",
        de_slice="results/enrichment/terms/term_deseq2_results.csv",
        log2fc_png="results/figures/term_log2fc.png",
        log2fc_svg="results/figures/term_log2fc.svg",
    params:
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/enrichment_term_heatmap.tsv"
    log:
        "logs/enrichment_term_heatmap.log",
    script:
        "../scripts/make_goi.R"
