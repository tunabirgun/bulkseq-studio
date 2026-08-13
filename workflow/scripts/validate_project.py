from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml


# Path-valued config settings that are used as a rule INPUT, with the feature each enables.
#
# Snakemake resolves rule inputs while BUILDING THE DAG, before the first job runs and
# therefore before the 00_project_setup check this script writes. A path that does not exist
# aborts the run with a MissingInputException that names the rule and the file but never the
# setting that introduced it, nor that clearing that setting is all it takes to proceed. That
# is reachable without doing anything unusual: copying a colleague's config.yaml into a fresh
# project, or restoring a config without the auxiliary files that sat beside it.
#
# The Snakefile calls check_gating_paths() at parse time, which is the only point early enough
# to beat DAG construction; main() calls it as well, so the same message reaches the sanity-check
# panel the application already shows on runs that do build.
#
# Explicit rather than derived: which settings gate a rule is a property of the rule files, not
# of anything in the config, and statically parsing Snakemake includes to recover it would be
# markedly more fragile than this table. tests/test_config_paths.py re-derives the list from the
# rule files and fails if the two disagree, so a new gated input cannot be added silently.
GATING_PATHS: tuple[tuple[str, str, str], ...] = (
    ("gene_sets", "custom_gene_list",
     "the genes-of-interest heatmap and per-gene expression figures"),
    ("gene_sets", "custom_gene_sets",
     "custom gene-set over-representation and gene-set enrichment"),
    ("gene_sets", "functional_annotation_table",
     "custom gene-set over-representation from an identifier-to-term table"),
    ("gene_sets", "background_gene_list",
     "the custom-enrichment background gene list"),
    ("input", "count_matrix", "count-matrix input mode"),
    ("input", "deseq2_results", "DESeq2-results input mode"),
    ("microarray", "expression_matrix", "the local-matrix microarray route"),
)

_RESULTS_ONLY_SAMPLE_COLUMNS = ("sample_id", "condition", "layout", "fastq_1")
_RESULTS_PROVENANCE_REQUIRED = (
    "original_basename", "imported_at", "project_copy", "sha256", "byte_size",
    "row_count", "column_names", "gene_id_column", "log2fc_column", "adjusted_p_column",
    "upstream_method", "lfc_shrinkage", "p_adjustment_method",
)


def _timezone_aware_iso(value: object) -> bool:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        stamp = value.strip()
        if stamp.endswith(("Z", "z")):
            stamp = stamp[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(stamp)
        except ValueError:
            parsed = None
    return parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_external_table(path: Path) -> pd.DataFrame:
    """Mirror the GUI import reader's delimiter, encoding, and row-shift safeguards."""
    last_decode_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with warnings.catch_warnings():
                # A data row with one extra field can otherwise make pandas silently use its
                # first value as an index and shift/truncate scientific columns.
                warnings.simplefilter("error", pd.errors.ParserWarning)
                return pd.read_csv(
                    path, sep=None, engine="python", dtype=str, keep_default_na=False,
                    index_col=False, encoding=encoding,
                )
        except UnicodeDecodeError as exc:
            last_decode_error = exc
    if last_decode_error is not None:
        raise last_decode_error
    raise ValueError("Could not decode the external-results project copy.")


def check_gating_paths(config: dict, base: Path | None = None) -> list[dict[str, str]]:
    """One FAIL per path-valued setting that names a file the project does not have.

    `base` resolves relative paths against the project root; Snakemake runs with the project
    root as the working directory, so the default of None (resolve against the cwd) is correct
    in the workflow and the parameter exists for tests.
    """
    msgs: list[dict[str, str]] = []
    micro_source = str((config.get("microarray") or {}).get("source", "") or "")
    for section, key, effect in GATING_PATHS:
        # The local-matrix path is only wired into a rule on that source; on a GEO series the
        # setting is inert and a stale value must not block the run.
        if (section, key) == ("microarray", "expression_matrix") and micro_source != "local_matrix":
            continue
        value = str(((config.get(section) or {}).get(key) or "")).strip()
        if not value:
            continue
        path = Path(value)
        if base is not None and not path.is_absolute():
            path = Path(base) / path
        if path.exists():
            continue
        msgs.append({"status": "FAIL", "message": (
            f"{section}.{key} points at a file that does not exist: {value}. "
            f"That setting enables {effect}; clear it to run without that feature, or put the "
            f"file at that path. Left as it is, the run stops while building the job graph with "
            f"a MissingInputException that names only the file."
        )})
    return msgs


def check_design(config: dict, samples_path: Path) -> list[dict[str, str]]:
    """Fail fast when the DE design references a factor level that does not exist in the
    sample sheet. Without this the run only crashes at the DESeq2 step ("'ref' must be an
    existing level") after alignment and counting have already run for many minutes."""
    msgs: list[dict[str, str]] = []
    if (config.get("input") or {}).get("type") == "deseq2_results":
        return msgs  # external results are supplied; no DE model is fit, so design is unused
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


def check_deseq2_results_direction(config: dict) -> list[dict[str, str]]:
    """Require an explicit, non-ambiguous log2FC direction for imported DE results.

    An imported table carries signed effects but not necessarily the comparison labels that
    define the sign.  Do not reuse ``deseq2.contrasts`` here: it may be stale from a prior
    local run and describes no model on the external-results route. Defaults remain lenient in
    the Pydantic model so legacy projects open, while every *new run* of this route fails until
    the analyst confirms the source table's direction.
    """
    inp = config.get("input") or {}
    if inp.get("type") != "deseq2_results":
        return []
    direction = inp.get("deseq2_results_direction")
    if not isinstance(direction, dict):
        return [{"status": "FAIL", "message": (
            "Imported DE-results mode requires direction provenance. Specify the numerator and "
            "denominator that define a positive log2 fold change, then confirm the source table.")}]

    numerator = str(direction.get("numerator") or "").strip()
    denominator = str(direction.get("denominator") or "").strip()
    messages: list[dict[str, str]] = []
    if not numerator or not denominator:
        messages.append({"status": "FAIL", "message": (
            "Imported DE-results direction is incomplete. Set both numerator and denominator so "
            "positive log2 fold change has an unambiguous meaning.")})
    elif numerator.casefold() == denominator.casefold():
        messages.append({"status": "FAIL", "message": (
            f"Imported DE-results direction uses '{numerator}' as both numerator and denominator. "
            "Use two labels that are distinct even when capitalization is ignored; otherwise the "
            "sign of log2 fold change is not meaningful.")})
    if direction.get("confirmed") is not True:
        messages.append({"status": "FAIL", "message": (
            "Imported DE-results direction has not been confirmed. Confirm that positive log2 fold "
            "change means higher expression in the recorded numerator than denominator before running.")})
    if not _timezone_aware_iso(direction.get("confirmed_at")):
        messages.append({"status": "FAIL", "message": (
            "Imported DE-results direction needs confirmed_at as a timezone-aware ISO 8601 "
            "timestamp (for example, 2026-08-10T12:00:00+03:00). Reconfirm the source direction "
            "so the provenance records when and in which timezone it was checked.")})
    return messages


def check_deseq2_results_provenance(
    config: dict, base: Path | None = None
) -> list[dict[str, str]]:
    """Require a complete, unchanged import-time record for the project copy.

    The GUI validates all rows before copying the file, then records its SHA-256 and schema facts.
    The workflow may also be launched directly, so it independently refuses a missing/legacy
    record or a project copy whose bytes changed after that validation. The R ingest repeats the
    row-level scientific checks; this function establishes that it is reading those same bytes.
    """
    inp = config.get("input") or {}
    if inp.get("type") != "deseq2_results":
        return []
    record = inp.get("deseq2_results_provenance")
    if not isinstance(record, dict):
        record = {}
    missing = [key for key in _RESULTS_PROVENANCE_REQUIRED if record.get(key) in (None, "", [])]
    if missing:
        return [{"status": "FAIL", "message": (
            "The external-results project copy has no complete import-time provenance record "
            f"(missing: {', '.join(missing)}). Re-import the original table before running.")}]

    messages: list[dict[str, str]] = []
    configured = str(inp.get("deseq2_results") or "").strip()
    if str(record.get("project_copy") or "").strip() != configured:
        messages.append({"status": "FAIL", "message": (
            "The configured external-results project-copy path differs from the path recorded at "
            "import. Re-import the original table instead of redirecting the verified record.")})
    original = str(record.get("original_basename") or "")
    if original.replace("\\", "/").rsplit("/", 1)[-1] != original:
        messages.append({"status": "FAIL", "message": (
            "External-results original_basename must contain only the source file name, not its "
            "original directory. Re-import the table to create a privacy-safe record.")})
    if not _timezone_aware_iso(record.get("imported_at")):
        messages.append({"status": "FAIL", "message": (
            "External-results imported_at must be a timezone-aware ISO 8601 timestamp. Re-import "
            "the original table so the project copy has an auditable import time.")})
    if record.get("lfc_shrinkage") not in {"unknown", "applied", "not_applied"}:
        messages.append({"status": "FAIL", "message": (
            "External-results lfc_shrinkage must be unknown, applied, or not_applied. Reconfirm "
            "the import metadata before running.")})
    for key in ("byte_size", "row_count"):
        value = record.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            messages.append({"status": "FAIL", "message": (
                f"External-results {key} must be a positive recorded integer. Re-import the table.")})

    columns = record.get("column_names")
    if isinstance(columns, list):
        missing_columns = [
            str(record.get(key)) for key in
            ("gene_id_column", "log2fc_column", "adjusted_p_column")
            if str(record.get(key)) not in columns
        ]
        if missing_columns:
            messages.append({"status": "FAIL", "message": (
                "The recorded selected column(s) are absent from the import-time schema: "
                f"{', '.join(missing_columns)}. Re-import the original table.")})
    else:
        messages.append({"status": "FAIL", "message": (
            "External-results column_names must be a recorded list. Re-import the original table.")})

    path = Path(configured) if configured else None
    if path is not None and base is not None and not path.is_absolute():
        path = Path(base) / path
    if path is None or not path.is_file():
        # check_gating_paths reports the missing configured file with the setting name.
        return messages
    try:
        actual_size = path.stat().st_size
        actual_sha = _sha256(path)
        dataframe = _read_external_table(path)
    except OSError as exc:
        messages.append({"status": "FAIL", "message": (
            f"Could not verify the external-results project copy: {exc}")})
        return messages
    except Exception as exc:
        messages.append({"status": "FAIL", "message": (
            f"Could not read the external-results project copy while verifying provenance: {exc}")})
        return messages
    if record.get("byte_size") != actual_size:
        messages.append({"status": "FAIL", "message": (
            "The external-results project-copy byte size changed after import.")})
    if str(record.get("sha256") or "").casefold() != actual_sha:
        messages.append({"status": "FAIL", "message": (
            "The external-results project copy changed after import (SHA-256 mismatch). Re-import "
            "the original table; do not run against unvalidated bytes.")})
    if record.get("row_count") != len(dataframe.index):
        messages.append({"status": "FAIL", "message": (
            "The external-results project-copy row count differs from the import-time record.")})
    actual_columns = [str(column) for column in dataframe.columns]
    if record.get("column_names") != actual_columns:
        messages.append({"status": "FAIL", "message": (
            "The external-results project-copy column schema differs from the import-time record.")})
    return messages


def check_samples(config: dict, samples_path: Path) -> list[dict[str, str]]:
    """Validate the empty-sheet exception used by the results-only route.

    The Snakefile indexes ``sample_id`` while parsing, even when there are no rows.  Accepting an
    arbitrary empty TSV would therefore let validation pass and then fail before the DAG exists.
    The application emits this exact minimal schema for results-only projects.  Ordinary routes
    still require at least one biological sample.
    """
    if not samples_path.exists():
        return []
    try:
        with samples_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            rows = list(reader)
            columns = tuple(reader.fieldnames or ())
    except (OSError, csv.Error) as exc:
        return [{"status": "FAIL", "message": f"Could not read samples table {samples_path}: {exc}"}]
    if rows:
        return []
    if (config.get("input") or {}).get("type") == "deseq2_results":
        if columns == _RESULTS_ONLY_SAMPLE_COLUMNS:
            return []
        expected = "\\t".join(_RESULTS_ONLY_SAMPLE_COLUMNS)
        observed = "\\t".join(columns) if columns else "(no header)"
        return [{"status": "FAIL", "message": (
            "A results-only project may use a header-only samples table only with the exact "
            f"minimal schema '{expected}'. Found '{observed}'. Recreate the results-only sample "
            "sheet before running so the workflow can parse it safely.")}]
    return [{"status": "FAIL", "message": (
        f"Sample sheet '{samples_path}' has no sample rows. Add at least one sample before running.")}]


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
        # Path-valued settings that gate a rule input. The Snakefile checks these at parse time
        # too; repeating it here puts the same message in the sanity-check panel.
        messages.extend(check_gating_paths(payload))
    if not samples_path.exists():
        messages.append({"status": "FAIL", "message": f"Missing samples table: {samples_path}"})
    messages.extend(check_samples(payload, samples_path))
    messages.extend(check_design(payload, samples_path))
    messages.extend(check_deseq2_results_direction(payload))
    project_root = config_path.resolve().parent.parent if config_path.exists() else Path.cwd()
    messages.extend(check_deseq2_results_provenance(payload, base=project_root))
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
