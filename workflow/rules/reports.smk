# Provenance and timing reports (run_summary, timing_summary, software_versions).
# Depends on the pipeline sinks so the reports reflect a completed run.


# Downstream-interoperability exports (normalized matrix + preranked .rnk) from
# the existing DE artifacts. Backend-agnostic: assay(vsd) and the `stat` column
# exist for both DESeq2 and limma.
rule export_matrices:
    input:
        rds="results/deseq2/deseq2_objects.rds",
        results="results/deseq2/deseq2_results.csv",
    output:
        vst="results/export/normalized_expression_matrix.csv",
        rnk="results/export/ranked_genes.rnk",
    log:
        "logs/export_matrices.log",
    script:
        "../scripts/export_downstream.R"


# The timing/report rule must run after every enabled scientific sink, not merely after
# the aggregate check. Otherwise opportunistic figures can still be rendering while the
# timing JSON and HTML are written, so their benchmark records and panels are omitted.
# One representative output is sufficient for a multi-output rule because Snakemake
# completes the whole rule atomically before releasing any dependent job.
_REPORT_SINKS = {
    "ranked": "results/export/ranked_genes.rnk",
    "set_overlap": "results/figures/set_overlap_dotplot.png",
}
if not DE_RESULTS_MODE:
    _REPORT_SINKS.update({
        "normalized": "results/export/normalized_expression_matrix.csv",
        "correlation": "results/figures/sample_correlation_pearson.png",
        "wilcoxon": "results/figures/wilcoxon_concordance.png",
    })
if WF.get("figures", True):
    _REPORT_SINKS["de_figures"] = "results/figures/volcano.png"
if WF.get("enrichment", True):
    _REPORT_SINKS.update({
        "enrichment_figures": "results/figures/enrichment_emapplot.png",
        "enrichment_network": "results/networks/enrichment_emap.cyjs",
    })
_CUSTOM_SETS = config.get("gene_sets", {})
if _CUSTOM_SETS.get("custom_gene_sets") or _CUSTOM_SETS.get("functional_annotation_table"):
    _REPORT_SINKS["custom_enrichment"] = "results/figures/custom_enrichment_dotplot.png"
if GSVA_ON:
    _REPORT_SINKS["gsva"] = "results/figures/gsva_heatmap.png"
if _CUSTOM_SETS.get("custom_gene_list") and not DE_RESULTS_MODE:
    _REPORT_SINKS["genes_of_interest"] = "results/figures/goi_heatmap.png"
if config.get("ppi", {}).get("enabled", True):
    _REPORT_SINKS["ppi"] = "results/figures/ppi_network.png"
if META_MODE:
    _REPORT_SINKS["meta"] = "results/figures/meta_volcano.png"
    if WF.get("enrichment", True):
        _REPORT_SINKS["meta_enrichment"] = "results/figures/meta_enrichment_dotplot.png"


# A realized strandedness value exists only on local/public read routes that align to a
# reference. Salmon infers library type internally and the matrix, microarray, and imported-
# results routes do not align reads, so none of those routes should acquire this dependency.
_REPORT_REALIZED_STRANDEDNESS = (
    str(INPUT.get("type") or "fastq").lower() in {"fastq", "sra", "mixed"}
    and not USE_SALMON
)


rule final_reports:
    input:
        sanity="checks/sanity_checks.txt",
        deseq2="results/deseq2/deseq2_results.csv",
        # No counts.txt in microarray mode (intensities) or deseq2-results mode (no counts).
        **({} if (MICROARRAY_MODE or DE_RESULTS_MODE) else {"counts": "results/counts/counts.txt"}),
        **({"strandedness": "results/aligned/strandedness.txt"} if _REPORT_REALIZED_STRANDEDNESS else {}),
        # MultiQC only exists on the alignment route.
        **({} if (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE) else {"multiqc": "results/qc/multiqc/multiqc_report.html"}),
        **_REPORT_SINKS,
    output:
        run_txt="results/reports/run_summary.txt",
        run_json="results/reports/run_summary.json",
        timing_txt="results/reports/timing_summary.txt",
        timing_json="results/reports/timing_summary.json",
        versions="results/reports/software_versions.txt",
        tools_refs="results/reports/tools_references.txt",
        study_design="results/reports/study_design.txt",
    benchmark:
        "benchmarks/final_reports.tsv"
    shell:
        "python workflow/scripts/make_run_summary.py --project . && "
        "python workflow/scripts/make_timing_summary.py --project ."


# Self-contained HTML results report: inlines the figures + top DE results + enrichment +
# provenance into one shareable file. Depends on the run summary (so provenance is written)
# and the DE table; reads figures/enrichment opportunistically. Runs in every input mode.
rule html_report:
    input:
        run_txt="results/reports/run_summary.txt",
        results="results/deseq2/deseq2_results.csv",
        # Order the report AFTER the figures rule: the report embeds results/figures/*.png read off
        # disk at build time (not as declared inputs), so without a DAG edge it can win the race and
        # embed pre-restyle / not-yet-written figures (stale panels on Regenerate; blank panels on a
        # fresh multi-core run). volcano.png is produced by `rule figures` in EVERY input mode (a real
        # plot or a placeholder), so requiring it forces the whole single-rule figure render first with
        # no MissingInputException. Gated on the figures flag so a figures-off run is not forced to render
        # them; enrichment/PPI figures stay opportunistic (they are conditionally absent).
        **({"figures": "results/figures/volcano.png"} if WF.get("figures", True) else {}),
        # On a multi-study run, wait for the meta summary so the main report's meta card block
        # renders deterministically (it reads meta_analysis_summary.json opportunistically).
        **({"meta_summary": "results/reports/meta_analysis_summary.json"} if META_MODE else {}),
    output:
        html="results/reports/results_report.html",
    log:
        "logs/html_report.log",
    shell:
        "python workflow/scripts/make_html_report.py --project . > {log} 2>&1"


if META_MODE:

    # Dedicated cross-study report (imports make_html_report.py's CSS/helpers). Depends on the
    # meta figures + tables + summary; the enrichment inputs are optional (org-db-gated), so they
    # are only required when enrichment is on.
    _meta_report_inputs = {
        "summary": "results/reports/meta_analysis_summary.json",
        "convergent": "results/meta/meta_convergent_genes.csv",
        "volcano": "results/figures/meta_volcano.png",
    }
    if WF.get("enrichment", True):
        _meta_report_inputs["dotplot"] = "results/figures/meta_enrichment_dotplot.png"

    rule meta_report:
        input:
            **_meta_report_inputs,
        output:
            html="results/reports/meta_analysis_report.html",
        log:
            "logs/meta_report.log",
        shell:
            "python workflow/scripts/make_meta_report.py --project . > {log} 2>&1"
