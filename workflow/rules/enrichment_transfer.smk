# Annotation-transfer enrichment: ORA and GSEA against STRING's orthology-transferred
# per-organism annotation (GO, Reactome, InterPro) and, optionally, eggNOG-mapper or
# KofamScan/KofamKOALA output the user supplies. It fills the gap for organisms without a
# Bioconductor OrgDb, so enrichment.transfer "auto" runs it only there; "on" also runs it
# for OrgDb organisms (a cross-check against curated GO) and "off" never runs it. Its
# outputs are separate files, so the curated routes' results are unchanged.
# YAML 1.1 reads an unquoted on/off as a boolean, so a hand-edited `transfer: off` arrives as False.
_TRANSFER_RAW = _ENR.get("transfer")
_TRANSFER_MODE = ({True: "on", False: "off"}[_TRANSFER_RAW] if isinstance(_TRANSFER_RAW, bool)
                  else str(_TRANSFER_RAW or "auto").lower())
_TRANSFER_TAXON = str(config.get("ppi", {}).get("taxon") or "")
_TRANSFER_IMPORTS = {k: (_ENR.get(f"transfer_{k}") or "") for k in ("emapper", "ko_table")}
_HAS_ORGDB = bool(_ENR.get("orgdb") or _MAPPED[0])
TRANSFER_ON = (
    bool(WF.get("enrichment", True))
    and not META_MODE
    and _TRANSFER_MODE != "off"
    and (_TRANSFER_MODE == "on" or not _HAS_ORGDB or any(_TRANSFER_IMPORTS.values()))
    and bool(_TRANSFER_TAXON or any(_TRANSFER_IMPORTS.values()))
)

if TRANSFER_ON:

    rule transfer_enrichment:
        input:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            eligibility_helper="workflow/scripts/enrichment_eligibility.R",
            **{k: v for k, v in _TRANSFER_IMPORTS.items() if v},
        output:
            ora="results/enrichment/transfer/transfer_ora.csv",
            gsea="results/enrichment/transfer/transfer_gsea.csv",
            id_map="results/enrichment/transfer/transfer_id_map.csv",
            summary="results/enrichment/transfer/transfer_summary.txt",
            provenance="results/enrichment/transfer/transfer_provenance.json",
            objects="results/enrichment/transfer/transfer_objects.rds",
            check="checks/25_transfer_enrichment_qc.json",
        params:
            taxon=_TRANSFER_TAXON,
            # One STRING snapshot for the network and the annotation.
            string_version=str(config.get("ppi", {}).get("string_version") or "12.0"),
            keytype=_ENR.get("keytype") or ("SYMBOL" if MICROARRAY_MODE else _MAPPED[1]),
            alpha=config.get("deseq2", {}).get("alpha", 0.05),
            cache_dir="results/enrichment/transfer/string_cache",
            emapper=_TRANSFER_IMPORTS["emapper"],
            ko_table=_TRANSFER_IMPORTS["ko_table"],
            # Only read to bridge imported protein ids to genes; a reclaimed reference
            # leaves direct gene-id imports working.
            annotation=ANNOTATION_GTF,
        benchmark:
            "benchmarks/transfer_enrichment.tsv"
        log:
            "logs/transfer_enrichment.log",
        script:
            "../scripts/run_transfer_enrichment.R"

    rule transfer_enrichment_figure:
        input:
            ora="results/enrichment/transfer/transfer_ora.csv",
        output:
            dotplot_png="results/figures/transfer_enrichment_dotplot.png",
            dotplot_svg="results/figures/transfer_enrichment_dotplot.svg",
        params:
            style=config.get("figures_style", {}),
        benchmark:
            "benchmarks/transfer_enrichment_figure.tsv"
        log:
            "logs/transfer_enrichment_figure.log",
        script:
            "../scripts/make_transfer_enrichment_figure.R"
