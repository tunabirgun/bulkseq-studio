# Differential expression with DESeq2 (protocol sections 7.1-7.4).
# The R script reads the snakemake object for inputs, params, and outputs.

_DE = config.get("deseq2", {})
_CONTRAST = (_DE.get("contrasts") or [{}])[0]
_REF = _DE.get("reference_level", {}) or {}
_REF_FACTOR = next(iter(_REF), "condition")


def _design_full_rank(terms):
    # Treatment-contrast model matrix (intercept + drop-first dummies) rank check, so we never
    # inject a study covariate that makes the joint design rank-deficient (which DESeq2 rejects
    # outright and limma fits with non-estimable coefficients). samples_df is in scope at parse
    # time. If a term is absent we can't check it -> don't block.
    import numpy as np
    cols = [np.ones((len(samples_df), 1))]
    for t in terms:
        if t not in samples_df.columns:
            return True
        d = pd.get_dummies(samples_df[t].astype(str).str.strip(), drop_first=True).to_numpy(dtype=float)
        if d.shape[1]:
            cols.append(d)
    m = np.column_stack(cols)
    return int(np.linalg.matrix_rank(m)) == m.shape[1]


def _joint_design():
    # The JOINT DE fit pools all samples. On a multi-study run it MUST model study-of-origin, or
    # study effects confound the contrast. Auto-inject 'dataset' into the *default* single-factor
    # design (~ condition); respect an explicit multi-term design the user set (they may already
    # model batch). Runs beside the per-study meta-analysis, which handles studies independently.
    # Single-study runs are byte-identical (MULTI_DATASET is False). Returns (design, note).
    d = (_DE.get("design_formula") or "~ condition").strip()
    if not MULTI_DATASET:
        return d, None
    rhs = d.split("~", 1)[-1].strip()
    terms = [t.strip() for t in rhs.replace("+", " ").split() if t.strip()]
    if "dataset" in terms or not terms:
        return d, None
    # Any multi-study design that omits study-of-origin — the default ~ condition OR an explicit
    # multi-term ADDITIVE design like ~ batch + condition — is study-confounded, so inject 'dataset'.
    # Only inject when every term is a plain factor column: an interaction/operator design (a token
    # like '*' or ':' that is not a column) can't be rank-checked reliably at parse time, so we keep
    # the user's design unchanged and warn rather than risk a rank-deficient joint design. The rank
    # check makes the additive case safe: if covariates already span study (e.g. batch == dataset) the
    # injected design is rank-deficient and we fall back to their design unchanged with a warning.
    injected = f"~ dataset + {rhs}"
    if not all(t in samples_df.columns for t in terms) or not _design_full_rank(["dataset"] + terms):
        return d, ("fallback", injected)
    return injected, ("injected", injected)


_DESIGN, _design_note = _joint_design()
if _design_note:
    _kind, _inj = _design_note
    _engine = "limma" if MICROARRAY_MODE else "DESeq2"
    if _kind == "injected":
        _alongside = " The per-study meta-analysis runs alongside." if META_MODE else ""
        sys.stderr.write(
            f"NOTE: multi-study run detected; the joint {_engine} design was set to '{_inj}' so "
            f"study-of-origin is modelled as a covariate.{_alongside}\n")
    else:
        _alongside = (" (the per-study meta-analysis still handles studies separately)."
                      if META_MODE else ".")
        sys.stderr.write(
            f"WARNING: multi-study run detected, but study-of-origin could not be safely added to the "
            f"design (injecting '{_inj}' would be rank-deficient for this sample layout, or the design "
            f"uses interaction terms that cannot be auto-checked); keeping '{_DESIGN}'. Study-of-origin "
            f"is NOT modelled in the joint {_engine} fit{_alongside}\n")


if MICROARRAY_MODE:

    # Microarray (continuous intensities): limma instead of DESeq2. Emits the
    # SAME output files as `rule deseq2` so figures/enrichment/GOI are unchanged.
    rule limma_de:
        input:
            expression="results/microarray/normalized_expression.tsv",
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            normalized="results/deseq2/normalized_counts.csv",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            design=_DESIGN,
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
        benchmark:
            "benchmarks/limma_de.tsv"
        log:
            "logs/limma_de.log",
        script:
            "../scripts/run_limma.R"

elif DE_RESULTS_MODE:

    # DESeq2-results upload: the user supplies a ready results table. Ingest it into
    # the canonical deseq2 outputs (results + up/down + a synthetic objects RDS that
    # carries no dds/vsd) so enrichment/figures/PPI run; alignment, counts and DESeq2
    # are skipped. Count-only outputs (normalized, unchanged, equivalence) are not
    # produced and are gated out of final_targets().
    rule ingest_deseq2_results:
        input:
            table=DE_RESULTS_TABLE,
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
        benchmark:
            "benchmarks/ingest_deseq2_results.tsv"
        log:
            "logs/ingest_deseq2_results.log",
        script:
            "../scripts/ingest_deseq2_results.R"

elif VOOM_MODE:

    # limma-voom (optional DE engine for count-based routes). Emits the SAME core
    # outputs as `rule deseq2` MINUS the DESeq2-specific TOST equivalence test
    # (unchanged_genes / check 13), so figures/enrichment/GOI stay backend-agnostic.
    # DESeq2 remains the default engine.
    rule voom_de:
        input:
            counts="results/counts/counts.txt",
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            normalized="results/deseq2/normalized_counts.csv",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            design=_DESIGN,
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
            # Path (not input) so count-matrix mode, which has no reference, still
            # runs; run_voom.R reads symbol/biotype from it only when it exists.
            gtf=ANNOTATION_GTF,
        benchmark:
            "benchmarks/voom_de.tsv"
        log:
            "logs/voom_de.log",
        script:
            "../scripts/run_voom.R"

elif EDGER_MODE:

    # edgeR quasi-likelihood (optional DE engine, count-based routes). Same core outputs as
    # `rule deseq2` MINUS the DESeq2-specific TOST equivalence test. DESeq2 stays the default.
    rule edger_de:
        input:
            counts="results/counts/counts.txt",
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            normalized="results/deseq2/normalized_counts.csv",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
        params:
            design=_DESIGN,
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
            gtf=ANNOTATION_GTF,
        benchmark:
            "benchmarks/edger_de.tsv"
        log:
            "logs/edger_de.log",
        script:
            "../scripts/run_edger.R"

else:

    rule deseq2:
        input:
            counts="results/counts/counts.txt",
            samples=config["input"]["samples"],
        output:
            results="results/deseq2/deseq2_results.csv",
            up="results/deseq2/upregulated_genes.csv",
            down="results/deseq2/downregulated_genes.csv",
            rds="results/deseq2/deseq2_objects.rds",
            normalized="results/deseq2/normalized_counts.csv",
            session="results/reports/sessionInfo.txt",
            design_check="checks/08_metadata_design_qc.json",
            deseq_check="checks/09_deseq2_qc.json",
            unchanged="results/deseq2/unchanged_genes.csv",
            equivalence_check="checks/13_equivalence_qc.json",
        params:
            design=_DESIGN,
            ref_factor=_REF_FACTOR,
            ref_level=_REF.get(_REF_FACTOR, ""),
            contrast_factor=_CONTRAST.get("factor", "condition"),
            numerator=_CONTRAST.get("numerator", ""),
            denominator=_CONTRAST.get("denominator", ""),
            alpha=_DE.get("alpha", 0.05),
            lfc_threshold=_DE.get("lfc_threshold", 1.0),
            min_count=_DE.get("min_count", 10),
            shrink=_DE.get("shrinkage_method", "apeglm"),
            # Path (not input) so count-matrix mode, which has no reference, still
            # runs; run_deseq2.R reads symbol/biotype from it only when it exists.
            gtf=ANNOTATION_GTF,
        benchmark:
            "benchmarks/deseq2.tsv"
        log:
            "logs/deseq2.log",
        script:
            "../scripts/run_deseq2.R"
