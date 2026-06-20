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
    ]
else:
    ALL_CHECKS = [
        "checks/00_project_setup.json",
        "checks/01_input_validation.json",
        "checks/05_reference_validation.json",
        "checks/06_alignment_qc.json",
        "checks/07_quantification_qc.json",
        "checks/08_metadata_design_qc.json",
        "checks/09_deseq2_qc.json",
    ]
if WF.get("enrichment", True):
    ALL_CHECKS.append("checks/10_enrichment_qc.json")


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
