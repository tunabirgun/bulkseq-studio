# GEO/GSE microarray ingest (0.4.0). Produces a normalized, gene-level log2
# expression matrix from a GEOquery series matrix or affy RMA, which run_limma.R
# (rules/deseq2.smk under MICROARRAY_MODE) consumes. Only defined in that mode.

_MICRO = config.get("microarray", {})


if MICROARRAY_MODE:

    rule ingest_geo:
        input:
            samples=config["input"]["samples"],
            # A user-supplied local expression matrix (source == local_matrix) is a real input
            # so Snakemake tracks it; GEO/affy sources download inside the R script instead.
            **({"matrix": _MICRO["expression_matrix"]}
               if _MICRO.get("source") == "local_matrix" and _MICRO.get("expression_matrix") else {}),
        output:
            expression="results/microarray/normalized_expression.tsv",
            probe_map="results/microarray/probe_gene_map.tsv",
            norm_info="results/microarray/normalization_info.json",
            norm_check="checks/11_normalization_qc.json",
            map_check="checks/12_probe_mapping_qc.json",
        params:
            gse=_MICRO.get("gse_accession", "") or "",
            platform=_MICRO.get("platform", "") or "",
            source=_MICRO.get("source", "geo_series_matrix"),
            matrix=_MICRO.get("expression_matrix", "") or "",
            normalization=_MICRO.get("normalization", "auto"),
            log2_transform=_MICRO.get("log2_transform", "auto"),
        benchmark:
            "benchmarks/ingest_geo.tsv"
        log:
            "logs/ingest_geo.log",
        script:
            "../scripts/ingest_geo.R"
