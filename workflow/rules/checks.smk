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
# Multi-study meta-analysis QC (+ cross-study enrichment QC): only when META_MODE, so single-study
# and other modes never require them. Surfaces the meta status in sanity_checks.txt + the report.
if META_MODE:
    ALL_CHECKS.append("checks/17_meta_analysis_qc.json")
    if WF.get("enrichment", True):
        ALL_CHECKS.append("checks/18_meta_enrichment_qc.json")
# Contrast-orientation QC (config-only): flags an inverted case-vs-control contrast where positive
# log2FC would mean up in the control group. Applies to every mode that runs DESeq2.
ALL_CHECKS.append("checks/19_orientation_qc.json")
# Re-deposited (pseudo-replicated) study detection: META_MODE only (needs >1 study).
if META_MODE:
    ALL_CHECKS.append("checks/20_duplicate_study_qc.json")
    # Per-study strandedness divergence from the featureCounts summary: only when the pipeline
    # aligns + runs featureCounts (i.e. not count-matrix / microarray / DE-results uploads).
    if not (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE):
        ALL_CHECKS.append("checks/21_strandedness_qc.json")


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
    params:
        # Pass the contrast so the multi-study confounding gate is enforced for CLI/benchmark runs
        # too (not just GUI pre-launch). Empty for single-condition/no-contrast configs.
        num=((config.get("deseq2", {}).get("contrasts") or [{}])[0].get("numerator", "")),
        den=((config.get("deseq2", {}).get("contrasts") or [{}])[0].get("denominator", "")),
    benchmark:
        "benchmarks/01_input_validation.tsv"
    shell:
        "python workflow/scripts/validate_metadata.py --samples {input.samples} "
        "--numerator {params.num:q} --denominator {params.den:q} --out {output}"


# Contrast-orientation QC: config-only, so it depends on the config alone (plus an ordering
# handle on 01 to keep it late in the check sequence). Always defined.
rule orientation_check:
    input:
        config="config/config.yaml",
        prev="checks/01_input_validation.json",
    output:
        "checks/19_orientation_qc.json",
    benchmark:
        "benchmarks/19_orientation_qc.tsv"
    shell:
        "python workflow/scripts/check_orientation.py --config {input.config} --out {output}"


if META_MODE:

    # Re-deposited (pseudo-replicated) study detection. Depends on the meta result so the
    # per_study_*.csv DE tables already exist when the DE-correlation check runs.
    rule duplicate_study_check:
        input:
            samples=config["input"]["samples"],
            prev="checks/17_meta_analysis_qc.json",
        output:
            "checks/20_duplicate_study_qc.json",
        params:
            meta_dir="results/meta",
        benchmark:
            "benchmarks/20_duplicate_study_qc.tsv"
        shell:
            "python workflow/scripts/check_duplicate_studies.py --samples {input.samples} "
            "--meta-dir {params.meta_dir} --out {output}"

    if not (COUNT_MATRIX_MODE or MICROARRAY_MODE or DE_RESULTS_MODE):

        # Per-study strandedness divergence from the featureCounts summary. Depends on the summary
        # (produced by featureCounts) and the meta result (ordering handle late in the sequence).
        rule strandedness_check:
            input:
                # COUNTS_SUMMARY is the featureCounts .summary (counts.txt.summary, or
                # counts.raw.txt.summary when organellar filtering renames the counts file).
                summary=COUNTS_SUMMARY,
                samples=config["input"]["samples"],
                prev="checks/17_meta_analysis_qc.json",
            output:
                "checks/21_strandedness_qc.json",
            benchmark:
                "benchmarks/21_strandedness_qc.tsv"
            shell:
                "python workflow/scripts/check_strandedness.py --summary {input.summary} "
                "--samples {input.samples} --out {output}"


rule aggregate_sanity_checks:
    input:
        ALL_CHECKS,
    output:
        "checks/sanity_checks.txt",
    benchmark:
        "benchmarks/sanity_checks.tsv"
    shell:
        "python workflow/scripts/aggregate_sanity_checks.py --checks {input} --out {output}"
