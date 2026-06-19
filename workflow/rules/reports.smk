rule final_reports:
    input:
        "checks/sanity_checks.txt",
        "results/enrichment/enrichment_results.tsv",
        "results/figures/pca_placeholder.txt"
    output:
        "results/reports/run_summary.txt",
        "results/reports/timing_summary.txt",
        "results/reports/run_summary.json",
        "results/reports/timing_summary.json",
        "results/reports/software_versions.txt"
    benchmark:
        "benchmarks/final_reports.tsv"
    shell:
        "python workflow/scripts/make_run_summary.py --project . && python workflow/scripts/make_timing_summary.py --project ."
