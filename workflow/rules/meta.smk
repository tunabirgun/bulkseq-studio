# Multi-study DE meta-analysis (0.21.0). Runs ONLY when META_MODE (workflow.meta_analysis is set
# AND the sample sheet has a 'dataset' column with >1 study). Per-study DESeq2 -> HTSFilter ->
# metaRNASeq inverse-normal + metafor effect-size combination, on top of the joint '~ dataset +
# condition' DESeq2. Studies missing a contrast arm are dropped and reported.

_META_DE = config.get("deseq2", {})
_META_CONTRAST = (_META_DE.get("contrasts") or [{}])[0]


if META_MODE:

    rule meta_analysis:
        input:
            counts="results/counts/counts.txt",
            samples=config["input"]["samples"],
        output:
            results="results/meta/meta_analysis_results.csv",
            meta_check="checks/17_meta_analysis_qc.json",
        params:
            contrast_factor=_META_CONTRAST.get("factor", "condition"),
            numerator=_META_CONTRAST.get("numerator", ""),
            denominator=_META_CONTRAST.get("denominator", ""),
            alpha=_META_DE.get("alpha", 0.05),
            dataset_column="dataset",
        benchmark:
            "benchmarks/meta_analysis.tsv"
        log:
            "logs/meta_analysis.log",
        script:
            "../scripts/run_meta_analysis.R"

    # Comparative figures + tables from the meta result (all themed; each degrades to a
    # placeholder so every declared output always exists). style param triggers a re-render.
    rule meta_figures:
        input:
            results="results/meta/meta_analysis_results.csv",
        output:
            volcano_png="results/figures/meta_volcano.png",
            volcano_svg="results/figures/meta_volcano.svg",
            forest_png="results/figures/meta_forest.png",
            forest_svg="results/figures/meta_forest.svg",
            scatter_png="results/figures/meta_concordance_scatter.png",
            scatter_svg="results/figures/meta_concordance_scatter.svg",
            heatmap_png="results/figures/meta_convergent_heatmap.png",
            heatmap_svg="results/figures/meta_convergent_heatmap.svg",
            hetero_png="results/figures/meta_heterogeneity.png",
            hetero_svg="results/figures/meta_heterogeneity.svg",
            phist_png="results/figures/meta_combined_p_hist.png",
            phist_svg="results/figures/meta_combined_p_hist.svg",
            gain_png="results/figures/meta_integration_gain.png",
            gain_svg="results/figures/meta_integration_gain.svg",
            convergent="results/meta/meta_convergent_genes.csv",
            study_summary="results/meta/meta_study_summary.csv",
            summary_json="results/reports/meta_analysis_summary.json",
        params:
            style=config.get("figures_style", {}),
            alpha=_META_DE.get("alpha", 0.05),
            lfc_threshold=_META_DE.get("lfc_threshold", 1.0),
            n_forest=6,
        benchmark:
            "benchmarks/meta_figures.tsv"
        log:
            "logs/meta_figures.log",
        script:
            "../scripts/make_meta_figures.R"

    # Per-study figures + tables (everything tier). Aggregating rule: depends on the meta result
    # (so per_study_<S>.csv + per_study_vsd_<S>.rds already exist), discovers studies on-disk, and
    # emits ONE declared target -- the manifest -- so there is no parse-time expand()/drift (the
    # 0.21.1 lesson). Report + GUI read the manifest to enumerate studies.
    rule meta_per_study:
        input:
            results="results/meta/meta_analysis_results.csv",
            de_results="results/deseq2/deseq2_results.csv",
        output:
            manifest="results/meta/per_study/manifest.json",
        params:
            style=config.get("figures_style", {}),
            alpha=_META_DE.get("alpha", 0.05),
            lfc_threshold=_META_DE.get("lfc_threshold", 1.0),
        benchmark:
            "benchmarks/meta_per_study.tsv"
        log:
            "logs/meta_per_study.log",
        script:
            "../scripts/make_meta_per_study_figures.R"

    if WF.get("enrichment", True):

        # Cross-study functional enrichment: compareCluster over per-study + convergent gene sets on
        # one shared universe. Reuses the organism -> OrgDb/keytype/KEGG mapping from enrichment.smk
        # (_MAPPED / _ENR); org-db-gated so unmapped organisms skip cleanly.
        rule meta_enrichment:
            input:
                results="results/meta/meta_analysis_results.csv",
            output:
                ora="results/meta/meta_enrichment_ora.csv",
                objects="results/meta/meta_enrichment_objects.rds",
                check="checks/18_meta_enrichment_qc.json",
            params:
                orgdb=_ENR.get("orgdb") or _MAPPED[0],
                keytype=_ENR.get("keytype") or ("SYMBOL" if MICROARRAY_MODE else _MAPPED[1]),
                kegg=_ENR.get("kegg_organism") or _MAPPED[2],
                ont=_ENR.get("go_ontology", "BP"),
                alpha=_META_DE.get("alpha", 0.05),
            benchmark:
                "benchmarks/meta_enrichment.tsv"
            log:
                "logs/meta_enrichment.log",
            script:
                "../scripts/run_meta_enrichment.R"

        rule meta_enrichment_figures:
            input:
                objects="results/meta/meta_enrichment_objects.rds",
            output:
                dotplot_png="results/figures/meta_enrichment_dotplot.png",
                dotplot_svg="results/figures/meta_enrichment_dotplot.svg",
            params:
                style=config.get("figures_style", {}),
            benchmark:
                "benchmarks/meta_enrichment_figures.tsv"
            log:
                "logs/meta_enrichment_figures.log",
            script:
                "../scripts/make_meta_enrichment_figures.R"

        # Optional per-study enrichment (opt-in: workflow.per_study_enrichment; default OFF). Heavy:
        # clusterProfiler x N studies. Runs ORA on each study's up/down lists over a per-study
        # universe, writing results/meta/per_study/<S>/enrichment/ + an enrichment_manifest.json.
        if WF.get("per_study_enrichment", False):

            rule meta_per_study_enrichment:
                input:
                    manifest="results/meta/per_study/manifest.json",
                output:
                    manifest="results/meta/per_study/enrichment_manifest.json",
                params:
                    orgdb=_ENR.get("orgdb") or _MAPPED[0],
                    keytype=_ENR.get("keytype") or ("SYMBOL" if MICROARRAY_MODE else _MAPPED[1]),
                    kegg=_ENR.get("kegg_organism") or _MAPPED[2],
                    ont=_ENR.get("go_ontology", "BP"),
                    alpha=_META_DE.get("alpha", 0.05),
                    style=config.get("figures_style", {}),
                benchmark:
                    "benchmarks/meta_per_study_enrichment.tsv"
                log:
                    "logs/meta_per_study_enrichment.log",
                script:
                    "../scripts/run_meta_per_study_enrichment.R"
