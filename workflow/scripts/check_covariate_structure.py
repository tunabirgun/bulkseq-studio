from __future__ import annotations

import argparse
import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path


PRIORITY = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
P_REVIEW_THRESHOLD = 0.05

# Descriptive columns that are never candidate covariates: the sample identifier, read-file
# paths, ingest provenance, and the free-text labels (library_name, sample_title, title) that describe
# a sample rather than group it. A free-text label whose values happen to partition the samples
# the way the contrast does reaches the perfectly-aliased branch below and reports a confounder
# that does not exist. Literal copy of app.constants.DESCRIPTIVE_METADATA_COLUMNS — this script
# runs in the pipeline environment, without `app` importable; the two are pinned together by
# tests/test_metadata_schema.py.
EXCLUDED_COLUMNS = {
    "sample_id",
    "library_name",
    "sample_title",
    "title",
    "fastq_1",
    "fastq_2",
    "gsm_accession",
    "original_accession",
    "original_filename",
    "detected_pair_id",
}


def _design_terms(design_formula: str) -> set[str]:
    rhs = design_formula.split("~", 1)[-1]
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", rhs))


def _read_samples(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise ValueError("samples sheet has no rows")
    sample_ids = [row["sample_id"].strip() for row in rows]
    columns = {col: [row.get(col, "").strip() for row in rows] for col in rows[0] if col != "sample_id"}
    return sample_ids, columns


def _read_pca(path: Path) -> dict[str, tuple[float, float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or "sample_id" not in rows[0] or "PC1" not in rows[0] or "PC2" not in rows[0]:
        raise ValueError("PCA coordinate table must have sample_id, PC1, PC2 columns")
    return {row["sample_id"]: (float(row["PC1"]), float(row["PC2"])) for row in rows}


def _betacf(a: float, b: float, x: float, itmax: int = 200, eps: float = 3e-11) -> float:
    # Numerical Recipes continued-fraction expansion for the regularized incomplete beta.
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / d if abs(d) >= 1e-30 else 1e30
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / d if abs(d) >= 1e-30 else 1e30
        c = 1.0 + aa / c
        c = c if abs(c) >= 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / d if abs(d) >= 1e-30 else 1e30
        c = 1.0 + aa / c
        c = c if abs(c) >= 1e-30 else 1e-30
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b), 0 <= x <= 1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _f_test_pvalue(f_stat: float, df1: int, df2: int) -> float:
    """Upper-tail p-value of the F(df1, df2) distribution at f_stat."""
    if df1 <= 0 or df2 <= 0:
        return 1.0
    if f_stat <= 0:
        return 1.0
    x = df2 / (df2 + df1 * f_stat)
    return _betai(df2 / 2.0, df1 / 2.0, x)


def _f_quantile(alpha: float, df1: int, df2: int) -> float:
    """The f with P(F(df1, df2) > f) = alpha, by bisection on _f_test_pvalue."""
    lo, hi = 0.0, 1.0
    while _f_test_pvalue(hi, df1, df2) > alpha:
        lo, hi = hi, hi * 2.0
    while hi - lo > 1e-9 * hi:
        mid = 0.5 * (lo + hi)
        if _f_test_pvalue(mid, df1, df2) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


@lru_cache(maxsize=None)
def chance_adj_r2(n: int, df1: int, df2: int, alpha: float = P_REVIEW_THRESHOLD) -> float:
    # The adjusted R^2 that only alpha of random label assignments reach at this (n, k).
    # adj_r2 = 1 - (n-1)/(F*df1 + df2) is monotone in F, so this is the null F quantile
    # mapped through that identity -- an effect-size floor that scales with n instead of a
    # fixed constant that only bound at small n.
    if df1 <= 0 or df2 <= 0:
        return 1.0
    return 1.0 - (n - 1) / (_f_quantile(alpha, df1, df2) * df1 + df2)


def screen_column(a: dict, n: int) -> tuple[bool, float]:
    # The floor and the p-value are one test: adjusted R^2 is monotone in F, so adj R^2 clears
    # the null 95th percentile exactly when p < 0.05. Both are kept so the message can state
    # how large an effect had to be at this n.
    floor = chance_adj_r2(n, a["df1"], a["df2"])
    return a["adj_r2"] > floor and a["p"] < P_REVIEW_THRESHOLD, floor


def _anova(values: list[float], levels: list[str]) -> dict:
    # One-way ANOVA (equivalent to OLS on dummy-coded levels + intercept): raw R^2 has a
    # null expectation of (k-1)/(n-1), so it is reported alongside the adjusted R^2 and the
    # F-test p-value rather than judged on its own.
    n = len(values)
    k = len(set(levels))
    grand_mean = sum(values) / n
    total_ss = sum((v - grand_mean) ** 2 for v in values)
    groups: dict[str, list[float]] = {}
    for v, lv in zip(values, levels):
        groups.setdefault(lv, []).append(v)
    between_ss = sum(len(g) * (sum(g) / len(g) - grand_mean) ** 2 for g in groups.values())
    r2 = (between_ss / total_ss) if total_ss > 0 else 0.0
    df1, df2 = k - 1, n - k
    within_ss = total_ss - between_ss
    if df1 <= 0 or df2 <= 0:
        return {"r2": r2, "adj_r2": r2, "p": 1.0, "f": 0.0, "df1": df1, "df2": df2}
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / df2
    if within_ss <= 0:
        f_stat, p = math.inf, 0.0
    else:
        f_stat = (between_ss / df1) / (within_ss / df2)
        p = _f_test_pvalue(f_stat, df1, df2)
    return {"r2": r2, "adj_r2": adj_r2, "p": p, "f": f_stat, "df1": df1, "df2": df2}


def _looks_numeric(values: list[str]) -> bool:
    try:
        return all(v != "" and not math.isnan(float(v)) for v in values)
    except ValueError:
        return False


def evaluate(sample_ids: list[str], columns: dict[str, list[str]],
             pca: dict[str, tuple[float, float]], design_formula: str, contrast_factor: str) -> dict:
    missing = [s for s in sample_ids if s not in pca]
    if missing:
        return {
            "check": "23_covariate_structure_qc",
            "status": "WARNING",
            "messages": [{"status": "WARNING", "message": (
                "Covariate-structure screen was not assessed, so an unmodelled covariate "
                "could go unnoticed; re-run it once the PCA table covers every sample. "
                f"PCA coordinates missing for {len(missing)} sample(s).")}],
        }

    pc1 = [pca[s][0] for s in sample_ids]
    pc2 = [pca[s][1] for s in sample_ids]
    design_terms = _design_terms(design_formula)
    contrast_levels = columns.get(contrast_factor)

    messages: list[dict[str, str]] = []
    tested = 0
    for col, values in sorted(columns.items()):
        if col in EXCLUDED_COLUMNS or col in design_terms:
            continue
        n_levels = len(set(values))
        if n_levels <= 1 or n_levels >= len(sample_ids):
            continue
        tested += 1
        if contrast_levels is not None and col != contrast_factor:
            partition = {v: frozenset(i for i, cv in enumerate(values) if cv == v) for v in set(values)}
            contrast_partition = {v: frozenset(i for i, cv in enumerate(contrast_levels) if cv == v) for v in set(contrast_levels)}
            if set(partition.values()) == set(contrast_partition.values()):
                messages.append({"status": "REVIEW_REQUIRED", "message": (
                    "A sample-sheet column is perfectly aliased with the contrast factor: do "
                    "not add it to the design formula, and check whether the grouping is "
                    "genuinely confounded, because its contribution cannot be distinguished "
                    f"from the contrast effect. Column '{col}', contrast factor "
                    f"'{contrast_factor}'.")})
                continue
        anova_pc1 = _anova(pc1, values)
        anova_pc2 = _anova(pc2, values)
        pc, a = ("PC1", anova_pc1) if anova_pc1["adj_r2"] >= anova_pc2["adj_r2"] else ("PC2", anova_pc2)
        numeric_note = " (numeric column treated as categorical)" if _looks_numeric(values) else ""
        n, k = len(sample_ids), a["df1"] + 1
        flagged, floor = screen_column(a, n)
        detail = (
            f"Column '{col}'{numeric_note}, best of PC1/PC2 is {pc}: raw R^2={a['r2']:.2f}, "
            f"adjusted R^2={a['adj_r2']:.2f} against a chance ceiling of {floor:.2f} at n={n}, "
            f"k={k}, F({a['df1']},{a['df2']})={a['f']:.2f}, p={a['p']:.3g}"
        )
        if flagged:
            messages.append({"status": "REVIEW_REQUIRED", "message": (
                f"{pc} separates samples by an unmodelled sample-sheet column beyond chance: "
                "consider adding it to the design formula as a covariate, or confirm it is not "
                f"confounded with the contrast. The screen is advisory. {detail}.")})
        else:
            messages.append({"status": "PASS", "message": (
                "No unmodelled structure to act on: this column's share of PC1/PC2 variance "
                f"is within chance, so the design formula needs no change for it. {detail}.")})

    if tested == 0:
        messages.append({"status": "PASS", "message": (
            "No design change indicated: no sample-sheet column outside the design formula "
            "was eligible for the covariate-structure screen.")})

    status = max((m["status"] for m in messages), key=lambda s: PRIORITY.get(s, 0))
    return {"check": "23_covariate_structure_qc", "status": status, "messages": messages}


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen leading PCs for unmodelled sample-sheet covariates.")
    parser.add_argument("--pca", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--contrast-factor", default="condition")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    pca_path = Path(args.pca)
    samples_path = Path(args.samples)
    try:
        if not pca_path.exists():
            raise FileNotFoundError(f"PCA coordinate table not found: {pca_path}")
        sample_ids, columns = _read_samples(samples_path)
        pca = _read_pca(pca_path)
        payload = evaluate(sample_ids, columns, pca, args.design, args.contrast_factor)
    except (OSError, UnicodeError, ValueError) as exc:
        payload = {
            "check": "23_covariate_structure_qc",
            "status": "WARNING",
            "messages": [{"status": "WARNING", "message": (
                "Covariate-structure screen was not assessed, so an unmodelled covariate "
                f"could go unnoticed; fix the input it could not read and re-run. Reason: {exc}.")}],
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
