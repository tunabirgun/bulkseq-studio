from __future__ import annotations

import re

import pandas as pd


def assess_meta_readiness(samples, *, enabled, input_type, contrast_factor, numerator,
                          denominator, design_formula, min_reps=2) -> dict:
    """Assess sample and model eligibility for the supported per-study meta fit."""
    df = samples if isinstance(samples, pd.DataFrame) else pd.DataFrame(samples)
    raw_factor = str(contrast_factor or "")
    raw_num, raw_den = str(numerator or ""), str(denominator or "")
    factor = raw_factor.strip()
    num, den = raw_num.strip(), raw_den.strip()
    design = str(design_formula or "").strip()
    result = {
        "enabled": bool(enabled), "applicable": input_type not in {"microarray", "deseq2_results"},
        "runnable": False, "status": "PASS", "contrast": {"factor": factor,
        "numerator": num, "denominator": den}, "dataset_column": "dataset" in df.columns,
        "study_counts": {}, "eligible_studies": [], "excluded_studies": [],
        "unsupported_design_terms": [], "messages": [],
    }
    if not enabled:
        return result

    def fail(message):
        result["messages"].append({"status": "FAIL", "message": message})

    if not result["applicable"]:
        fail(f"Meta-analysis is unavailable for {input_type} input.")
        result["status"] = "FAIL"
        return result
    if raw_factor != factor:
        fail("Selected comparison factor name has leading or trailing whitespace; correct the selection.")
    if raw_num != num or raw_den != den:
        fail("Compared arm labels have leading or trailing whitespace; correct the contrast labels without changing sample values.")
    if not factor or factor not in df.columns:
        fail(f"Selected comparison factor '{factor}' is missing from samples.")
    if not num or not den or num == den:
        fail("Select two distinct comparison arms for meta-analysis.")
    if "dataset" not in df.columns:
        fail("Assign each sample to a study in the dataset column.")
    if not isinstance(min_reps, int) or min_reps < 1:
        fail("Minimum replicate count must be a positive integer.")

    # R model grammar: intercept plus selected factor, optionally additive dataset.
    body = design[1:].strip() if design.startswith("~") else ""
    terms = [term.strip() for term in body.split("+")] if body else []
    if terms.count("1") == 1:
        terms.remove("1")
    supported = ({factor}, {factor, "dataset"})
    if (not terms or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", t) for t in terms)
            or len(terms) != len(set(terms)) or set(terms) not in supported):
        result["unsupported_design_terms"] = [t for t in terms if t not in {factor, "dataset"}]
        if not result["unsupported_design_terms"]:
            result["unsupported_design_terms"] = [design or "(missing design)"]
        fail("Meta-analysis supports an intercept, the selected factor, and optionally additive dataset; remove unsupported terms only if scientifically appropriate.")

    if factor in df.columns and "dataset" in df.columns and num and den and num != den:
        datasets = df["dataset"].fillna("").astype(str).str.strip()
        arms = df[factor].fillna("").astype(str)
        padded_arms = arms.ne(arms.str.strip())
        if padded_arms.any():
            fail(f"{int(padded_arms.sum())} sample(s) have leading or trailing whitespace in '{factor}'; correct the metadata values before meta-analysis.")
        missing_study = int(datasets.eq("").sum())
        missing_arm = int(arms.isin(("", "unknown")).sum())
        if missing_study:
            fail(f"{missing_study} sample(s) lack a study assignment.")
        if missing_arm:
            fail(f"{missing_arm} sample(s) lack a known '{factor}' value.")
        for study in sorted(v for v in datasets.unique() if v):
            selected = arms[datasets == study]
            n_num, n_den = int(selected.eq(num).sum()), int(selected.eq(den).sum())
            eligible = n_num >= min_reps and n_den >= min_reps
            reason = "" if eligible else (f"Needs at least {min_reps} samples in each compared arm "
                                         f"({num}: {n_num}; {den}: {n_den}).")
            result["study_counts"][study] = {"total": int(selected.size), "numerator": n_num,
                                             "denominator": n_den, "eligible": eligible,
                                             "reason": reason}
            (result["eligible_studies"] if eligible else result["excluded_studies"]).append(study)
        if len(result["eligible_studies"]) < 2:
            fail(f"Only {len(result['eligible_studies'])} study/studies have at least {min_reps} samples in each compared arm; meta-analysis needs two.")
    result["runnable"] = not result["messages"]
    result["status"] = "PASS" if result["runnable"] else "FAIL"
    if result["runnable"]:
        result["messages"].append({"status": "PASS", "message":
                                   f"{len(result['eligible_studies'])} studies meet metadata and model eligibility; study independence still needs review."})
    return result
