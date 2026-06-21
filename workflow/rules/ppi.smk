# Protein-protein interaction network (STRING) built from the DE / GOI set.
# Opt-outable via ppi.enabled. STRINGdb contacts string-db.org (no offline mode);
# build_string_network.R degrades to empty outputs + a PASS check when the network
# or organism is unavailable, so a run never fails because STRING is unreachable.
_PPI = config.get("ppi", {})


rule network_string:
    input:
        results="results/deseq2/deseq2_results.csv",
        up="results/deseq2/upregulated_genes.csv",
        down="results/deseq2/downregulated_genes.csv",
    output:
        graphml="results/networks/string_ppi.graphml",
        sif="results/networks/string_ppi.sif",
        cyjs="results/networks/string_ppi.cyjs",
        nodes="results/networks/string_ppi_nodes.csv",
        edges="results/networks/string_ppi_edges.csv",
        hubs="results/networks/ppi_hub_genes.csv",
        png="results/figures/ppi_network.png",
        svg="results/figures/ppi_network.svg",
        check="checks/16_ppi_network.json",
    params:
        organism=config.get("reference", {}).get("organism_name", ""),
        score_threshold=_PPI.get("score_threshold", 400),
        taxon=(_PPI.get("taxon") or ""),
        seed_source=_PPI.get("seed_source", "de"),
        string_version=_PPI.get("string_version", "12.0"),
        max_seed=_PPI.get("max_seed_genes", 400),
        hub_labels=_PPI.get("hub_label_count", 15),
        goi=(config.get("gene_sets", {}).get("custom_gene_list") or ""),
        style=config.get("figures_style", {}),
    benchmark:
        "benchmarks/network_string.tsv"
    log:
        "logs/network_string.log",
    script:
        "../scripts/build_string_network.R"
