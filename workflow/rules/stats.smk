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
