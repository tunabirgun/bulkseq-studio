rule figures:
    input:
        "results/deseq2/deseq2_results.tsv"
    output:
        "results/figures/pca_placeholder.txt",
        "results/figures/volcano_placeholder.txt",
        "results/figures/top_deg_heatmap_placeholder.txt"
    benchmark:
        "benchmarks/figures.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output[0]} --message 'PCA placeholder.' && python workflow/scripts/touch_report.py --out {output[1]} --message 'Volcano placeholder.' && python workflow/scripts/touch_report.py --out {output[2]} --message 'Top DEG heatmap placeholder.'"
