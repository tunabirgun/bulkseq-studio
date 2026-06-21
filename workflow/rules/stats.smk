# Additional statistical tests (0.6.0): non-parametric sensitivity, gene-set
# overlap significance. Backend-agnostic where possible (read the DE artifacts).

_STAT_DE = config.get("deseq2", {})
_STAT_CON = (_STAT_DE.get("contrasts") or [{}])[0]


# Wilcoxon rank-sum per-gene sensitivity / concordance diagnostic.
rule wilcoxon_sensitivity:
    input:
        rds="results/deseq2/deseq2_objects.rds",
        results="results/deseq2/deseq2_results.csv",
    output:
        csv="results/stats/wilcoxon_results.csv",
        png="results/figures/wilcoxon_concordance.png",
        svg="results/figures/wilcoxon_concordance.svg",
        check="checks/14_wilcoxon_sensitivity.json",
    params:
        factor=_STAT_CON.get("factor", "condition"),
        numerator=_STAT_CON.get("numerator", ""),
        denominator=_STAT_CON.get("denominator", ""),
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/wilcoxon_sensitivity.tsv"
    log:
        "logs/wilcoxon_sensitivity.log",
    script:
        "../scripts/run_wilcoxon.R"


# DE-list vs MSigDB Hallmark overlap significance (hypergeometric ORA).
rule set_overlap:
    input:
        results="results/deseq2/deseq2_results.csv",
        up="results/deseq2/upregulated_genes.csv",
        down="results/deseq2/downregulated_genes.csv",
    output:
        csv="results/stats/set_overlap.csv",
        png="results/figures/set_overlap_dotplot.png",
        svg="results/figures/set_overlap_dotplot.svg",
        check="checks/15_set_overlap.json",
    params:
        organism=config.get("reference", {}).get("organism_name", ""),
        alpha=config.get("deseq2", {}).get("alpha", 0.05),
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/set_overlap.tsv"
    log:
        "logs/set_overlap.log",
    script:
        "../scripts/run_set_overlap.R"
