# Provenance and timing reports (run_summary, timing_summary, software_versions).
# Depends on the pipeline sinks so the reports reflect a completed run.


rule final_reports:
    input:
        sanity="checks/sanity_checks.txt",
        counts="results/counts/counts.txt",
        deseq2="results/deseq2/deseq2_results.csv",
        # MultiQC only exists on the alignment route, not in count-matrix mode.
        **({} if COUNT_MATRIX_MODE else {"multiqc": "results/qc/multiqc/multiqc_report.html"}),
    output:
        run_txt="results/reports/run_summary.txt",
        run_json="results/reports/run_summary.json",
        timing_txt="results/reports/timing_summary.txt",
        timing_json="results/reports/timing_summary.json",
        versions="results/reports/software_versions.txt",
    benchmark:
        "benchmarks/final_reports.tsv"
    shell:
        "python workflow/scripts/make_run_summary.py --project . && "
        "python workflow/scripts/make_timing_summary.py --project ."
