# Sanity-check phase gates. The aggregate depends on EVERY phase check so the
# summary is reproducible (not dependent on prior filesystem state).

if MICROARRAY_MODE:
    # Microarray: no reference/alignment/featureCounts; add normalization and
    # probe-mapping checks produced by ingest_geo.R instead.
    ALL_CHECKS = [
        "checks/00_project_setup.json",
        "checks/01_input_validation.json",
        "checks/11_normalization_qc.json",
        "checks/12_probe_mapping_qc.json",
        "checks/08_metadata_design_qc.json",
        "checks/09_deseq2_qc.json",
    ]
elif COUNT_MATRIX_MODE:
    # No reference/alignment in count-matrix mode, so those checks are not produced.
    ALL_CHECKS = [
        "checks/00_project_setup.json",
        "checks/01_input_validation.json",
        "checks/07_quantification_qc.json",
        "checks/08_metadata_design_qc.json",
        "checks/09_deseq2_qc.json",
        "checks/13_equivalence_qc.json",
    ]
elif DE_RESULTS_MODE:
    # DESeq2-results upload: only the ingest checks exist (no alignment, counts,
    # DESeq2 model or equivalence test).
    ALL_CHECKS = [
        "checks/00_project_setup.json",
        "checks/01_input_validation.json",
        "checks/08_metadata_design_qc.json",
        "checks/09_deseq2_qc.json",
    ]
else:
    ALL_CHECKS = [
        "checks/00_project_setup.json",
        "checks/01_input_validation.json",
        "checks/05_reference_validation.json",
        "checks/07_quantification_qc.json",
        "checks/08_metadata_design_qc.json",
        "checks/09_deseq2_qc.json",
        "checks/13_equivalence_qc.json",
    ]
    # 06 alignment QC parses STAR's Log.final.out; only STAR produces it. HISAT2 and
    # Salmon report their own mapping rate in their logs (results/aligned/*_hisat2_summary.txt,
    # results/salmon/<sample>/logs), so the formal 06 check is STAR-only.
    if not (USE_HISAT2 or USE_SALMON):
        ALL_CHECKS.insert(3, "checks/06_alignment_qc.json")
# limma-voom / edgeR do not run the DESeq2-specific equivalence (TOST) test.
if ALT_DE_MODE and "checks/13_equivalence_qc.json" in ALL_CHECKS:
    ALL_CHECKS.remove("checks/13_equivalence_qc.json")
if WF.get("enrichment", True):
    ALL_CHECKS.append("checks/10_enrichment_qc.json")
# Wilcoxon sensitivity diagnostic reads the normalized matrix, which the
# DESeq2-results upload mode does not have; it runs on every other mode.
if not DE_RESULTS_MODE:
    ALL_CHECKS.append("checks/14_wilcoxon_sensitivity.json")
# DE-vs-gene-set overlap (skips cleanly for organisms not covered by MSigDB).
ALL_CHECKS.append("checks/15_set_overlap.json")
# PPI network (STRING) when enabled; degrades to empty + PASS if unreachable.
if config.get("ppi", {}).get("enabled", True):
    ALL_CHECKS.append("checks/16_ppi_network.json")


rule validate_project:
    input:
        config="config/config.yaml",
        samples=config["input"]["samples"],
    output:
        "checks/00_project_setup.json",
    benchmark:
        "benchmarks/00_project_setup.tsv"
    shell:
        "python workflow/scripts/validate_project.py --config {input.config} --samples {input.samples} --out {output}"


rule input_check:
    input:
        samples=config["input"]["samples"],
        prev="checks/00_project_setup.json",
    output:
        "checks/01_input_validation.json",
    benchmark:
        "benchmarks/01_input_validation.tsv"
    shell:
        "python workflow/scripts/validate_metadata.py --samples {input.samples} --out {output}"


rule aggregate_sanity_checks:
    input:
        ALL_CHECKS,
    output:
        "checks/sanity_checks.txt",
    benchmark:
        "benchmarks/sanity_checks.tsv"
    shell:
        "python workflow/scripts/aggregate_sanity_checks.py --checks {input} --out {output}"
