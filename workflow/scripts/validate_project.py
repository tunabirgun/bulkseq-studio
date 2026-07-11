from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def check_design(config: dict, samples_path: Path) -> list[dict[str, str]]:
    """Fail fast when the DE design references a factor level that does not exist in the
    sample sheet. Without this the run only crashes at the DESeq2 step ("'ref' must be an
    existing level") after alignment and counting have already run for many minutes."""
    msgs: list[dict[str, str]] = []
    if (config.get("input") or {}).get("type") == "deseq2_results":
        return msgs  # results are uploaded; no DE model is fit, so the design is unused
    de = config.get("deseq2") or {}
    # Required (factor -> {levels}) from the reference level and every contrast.
    required: dict[str, set[str]] = {}
    ref = de.get("reference_level")
    if isinstance(ref, dict):
        for factor, level in ref.items():
            if level:
                required.setdefault(str(factor), set()).add(str(level))
    for con in de.get("contrasts") or []:
        if not isinstance(con, dict) or not con.get("factor"):
            continue
        for key in ("numerator", "denominator"):
            if con.get(key):
                required.setdefault(str(con["factor"]), set()).add(str(con[key]))
    if not required or not samples_path.exists():
        return msgs
    with samples_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if not rows:
        return msgs
    cols = list(rows[0].keys())
    for factor, levels in required.items():
        if factor not in cols:
            msgs.append({"status": "FAIL", "message": (
                f"The differential-expression design references the column '{factor}', which is "
                f"not in the sample sheet (columns: {', '.join(cols)}). Set the contrast factor "
                f"to a column that exists.")})
            continue
        present = sorted({str(r.get(factor, "")).strip() for r in rows if str(r.get(factor, "")).strip()})
        for level in sorted(levels):
            if level not in present:
                msgs.append({"status": "FAIL", "message": (
                    f"The design uses '{level}' for '{factor}', but the sample sheet has no such "
                    f"value. Available {factor} values: {', '.join(present) or '(none)'}. Fix the "
                    f"reference level / contrast on Workflow Settings to match your sample "
                    f"conditions, then re-run.")})
    return msgs


# R / Bioconductor packages a standard run loads regardless of organism or DE engine. Load-
# testing (not just presence) also catches a package left binary-incompatible by an r-base
# drift (the env pins r-base=4.5.2 for that reason). Fail fast here with a clear message
# instead of dying minutes later in enrichment/figures/networks.
_CORE_R_PACKAGES = [
    "DESeq2", "limma", "clusterProfiler", "GO.db", "DOSE", "enrichplot", "fgsea",
    "AnnotationDbi", "ggplot2", "ggrepel", "pheatmap", "igraph", "STRINGdb",
    # CRAN figure/plotting packages every route hard-loads in the mandatory figures +
    # sample-correlation rules (scales especially is only a transitive dep in the fallback
    # env spec, so a solve can drop it and pass the presence check), and msigdbr backs the
    # set-overlap rule that runs on every DE route.
    "scales", "svglite", "RColorBrewer", "msigdbr",
]


def required_r_packages(config: dict | None = None) -> list[str]:
    """The R/Bioconductor packages a run with this config actually loads. Core figure/DE/enrichment
    packages plus the route-/engine-conditional ones (microarray, meta-analysis, edgeR/limma-voom,
    GSVA, Salmon->tximport, g:Profiler), so Check Environment fails fast on exactly what the run needs."""
    packages = list(_CORE_R_PACKAGES)
    cfg = config or {}
    # A microarray run loads GEOquery (and affy for the raw-CEL source) in ingest_geo.R.
    if (cfg.get("input") or {}).get("type") == "microarray":
        packages.append("GEOquery")
        if ((cfg.get("microarray") or {}).get("source")) == "affy_cel":
            packages.append("affy")
    # Route-/engine-conditional packages the run only loads on some settings — added the same way
    # GEOquery/affy are, so a run fails fast in Check Environment with a clear message instead of
    # dying minutes in (e.g. run_meta_analysis.R has no metaRNASeq).
    wf = cfg.get("workflow") or {}
    if wf.get("meta_analysis"):
        packages += ["metaRNASeq", "metafor", "HTSFilter"]
    if wf.get("de_engine") in ("edgeR", "limma-voom"):
        packages.append("edgeR")  # limma-voom's voom() uses edgeR's DGEList/normalisation
    if wf.get("gsva"):
        packages.append("GSVA")
    # aligner/quantifier live in the workflow section (WorkflowConfig), not a separate 'alignment'
    # section — read them from wf so the Salmon route actually load-tests tximport.
    if wf.get("aligner") == "Salmon" or wf.get("quantifier") == "Salmon_tximport":
        packages.append("tximport")
    enr = cfg.get("enrichment") or {}
    if enr.get("backend") == "gprofiler" or enr.get("gprofiler_organism"):
        packages.append("gprofiler2")
    # DESeq2 lfcShrink estimator — only a count-based DESeq2 run calls lfcShrink; the method selects
    # apeglm (default) or ashr, each a separate package ('normal' needs none). Load-test the one this
    # run uses so a dropped estimator fails fast here instead of minutes in at the shrinkage step.
    itype = (cfg.get("input") or {}).get("type")
    de = cfg.get("deseq2") or {}
    if (wf.get("de_engine", "DESeq2") == "DESeq2" and itype not in ("microarray", "deseq2_results")
            and de.get("lfc_shrinkage", True)):
        shrink = de.get("shrinkage_method") or "apeglm"
        if shrink in ("apeglm", "ashr"):
            packages.append(shrink)
    return list(dict.fromkeys(packages))  # de-dup, preserve order


def check_r_packages(config: dict | None = None) -> list[dict[str, str]]:
    """Fail fast if the bulkseq R environment cannot load the packages the pipeline needs."""
    rscript = shutil.which("Rscript")
    if not rscript:
        return [{"status": "FAIL", "message": (
            "Rscript is not on PATH, so the R/Bioconductor environment (bulkseq) is not active. "
            "Activate it, or recreate it from workflow/envs/bulkseq.lock.yaml, then re-run.")}]
    packages = required_r_packages(config)
    pkgs = ", ".join(f'"{p}"' for p in packages)
    r_code = (
        f"pkgs <- c({pkgs}); "
        "ok <- function(p) tryCatch(suppressWarnings(suppressMessages("
        "requireNamespace(p, quietly = TRUE))), error = function(e) FALSE); "
        "bad <- pkgs[!vapply(pkgs, ok, logical(1))]; "
        "if (length(bad)) { cat(paste(bad, collapse = ',')); quit(status = 1) }"
    )
    try:
        proc = subprocess.run([rscript, "--vanilla", "-e", r_code],
                              capture_output=True, text=True, timeout=600)
    except Exception as exc:
        return [{"status": "FAIL", "message": f"Could not run the R environment check: {exc}"}]
    if proc.returncode != 0:
        bad = (proc.stdout or "").strip() or (proc.stderr or "").strip() or "one or more packages"
        return [{"status": "FAIL", "message": (
            f"These required R/Bioconductor packages will not load in the bulkseq env: {bad}. "
            "This is usually a missing package (e.g. GO.db) or an env drift that bumped r-base and "
            "left compiled packages binary-incompatible. Recreate the env from "
            "workflow/envs/bulkseq.lock.yaml, or install the missing one "
            "(e.g. micromamba install -n bulkseq -c bioconda -c conda-forge bioconductor-go.db), "
            "then re-run.")}]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    messages: list[dict[str, str]] = []
    config_path = Path(args.config)
    samples_path = Path(args.samples)
    payload: dict = {}
    if not config_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing config: {config_path}"})
    else:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        for section in ("project", "input", "reference", "workflow", "resources"):
            if section not in payload:
                messages.append({"status": "FAIL", "message": f"Missing config section: {section}"})
        # Contamination screening needs a user-provided FastQ Screen config; warn if it is
        # enabled without one (the screen is skipped) or points at a missing file (it will fail).
        wf = payload.get("workflow") or {}
        if wf.get("contamination_screen"):
            conf = ((payload.get("contamination") or {}).get("conf") or "").strip()
            if not conf:
                messages.append({"status": "WARNING", "message": "Contamination screening is enabled but no FastQ Screen config (contamination.conf) is set; the screen will be skipped. Set a fastq_screen.conf under Advanced parameters to run it."})
            elif not Path(conf).exists():
                messages.append({"status": "WARNING", "message": f"FastQ Screen config not found: {conf}; the contamination screen will fail until the path is fixed or the screen is disabled."})
    if not samples_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing samples table: {samples_path}"})
    messages.extend(check_design(payload, samples_path))
    messages.extend(check_r_packages(payload))
    if not messages:
        messages.append({"status": "PASS", "message": "Project setup files are present."})
    write_payload(Path(args.out), "00_project_setup", messages)
    # Stop the run now on a fatal setup error (bad design, missing config/samples) with a clear
    # message, instead of letting it fail minutes later at alignment or DESeq2.
    fails = [m["message"] for m in messages if m["status"] == "FAIL"]
    if fails:
        for msg in fails:
            print(f"PROJECT SETUP ERROR: {msg}", file=sys.stderr)
        return 1
    return 0


def write_payload(path: Path, name: str, messages: list[dict[str, str]]) -> None:
    priority = {"FAIL": 4, "REVIEW_REQUIRED": 3, "WARNING": 2, "PASS": 1}
    status = max((m["status"] for m in messages), key=lambda s: priority.get(s, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"check": name, "status": status, "messages": messages}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
