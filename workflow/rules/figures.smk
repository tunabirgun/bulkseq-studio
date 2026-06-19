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
    benchmark:
        "benchmarks/figures.tsv"
    log:
        "logs/figures.log",
    script:
        "../scripts/make_figures.R"
