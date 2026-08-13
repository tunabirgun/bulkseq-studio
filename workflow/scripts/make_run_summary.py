from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

import yaml


def env_lock_md5() -> str | None:
    # md5 of the pinned conda lock that defines the analysis environment.
    lock = Path(__file__).resolve().parent.parent / "envs" / "bulkseq.lock.yaml"
    if not lock.exists():
        return None
    return hashlib.md5(lock.read_bytes()).hexdigest()


def workflow_git_commit() -> str | None:
    # Source commit when run from a checkout; None in a packaged build (no .git).
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent),
                              "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, check=False)
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


TOOLS = {
    "snakemake": ["snakemake", "--version"],
    "python": ["python", "--version"],
    "fastqc": ["fastqc", "--version"],
    "multiqc": ["multiqc", "--version"],
    "STAR": ["STAR", "--version"],
    "HISAT2": ["hisat2", "--version"],
    "salmon": ["salmon", "--version"],
    "gffread": ["gffread", "--version"],
    "samtools": ["samtools", "--version"],
    "featureCounts": ["featureCounts", "-v"],
    "Rscript": ["Rscript", "--version"],
}

# Trimmer / rRNA-filter / contamination-screen probe commands, keyed by the config value that
# selects them (workflow.trimmer, workflow.rrna_tool, workflow.contamination_screen -- see
# app/core/config_models.py). TOOLS above is the command catalog; these three are
# config-selectable alternatives, so probing them unconditionally would either miss the tool
# that actually ran (e.g. trim_galore, ribodetector) or report a version for a tool that never
# ran. select_tools() below gates each entry on what the run's config actually enabled.
TRIMMER_TOOL_CMD = {
    "fastp": ("fastp", ["fastp", "--version"]),
    "trim-galore": ("trim_galore", ["trim_galore", "--version"]),
    "trimmomatic": ("trimmomatic", ["trimmomatic", "-version"]),
}
RRNA_TOOL_CMD = {
    "sortmerna": ("sortmerna", ["sortmerna", "--version"]),
    "ribodetector": ("ribodetector", ["ribodetector_cpu", "--version"]),
}


_DE_ENGINE_LABELS = {"deseq2": "DESeq2", "limma-voom": "limma-voom", "limma_voom": "limma-voom",
                     "voom": "limma-voom", "edger": "edgeR"}


def uploaded_results_direction(payload: dict) -> tuple[str | None, str | None, bool, str | None]:
    """Return the independently recorded log2FC direction for a supplied results table."""
    direction = (payload.get("input") or {}).get("deseq2_results_direction") or {}
    if not isinstance(direction, dict):
        return None, None, False, None
    numerator = str(direction.get("numerator") or "").strip() or None
    denominator = str(direction.get("denominator") or "").strip() or None
    confirmed_at = str(direction.get("confirmed_at") or "").strip() or None
    return numerator, denominator, direction.get("confirmed") is True, confirmed_at


def uploaded_results_provenance(payload: dict) -> dict:
    """Return the normalized provenance mapping for an externally supplied DE table."""
    provenance = (payload.get("input") or {}).get("deseq2_results_provenance") or {}
    return provenance if isinstance(provenance, dict) else {}


def uploaded_results_adjusted_p_name(payload: dict) -> str:
    """Name the supplied adjusted-p field without guessing its correction method."""
    method = str(uploaded_results_provenance(payload).get("p_adjustment_method") or "").strip()
    if not method or method.casefold() == "unknown":
        return "adjusted p-value"
    return f"adjusted p-value ({method})"


def uploaded_results_provenance_lines(payload: dict) -> list[str]:
    """Render the shared, privacy-local external-results provenance record."""
    provenance = uploaded_results_provenance(payload)
    numerator, denominator, confirmed, confirmed_at = uploaded_results_direction(payload)
    if confirmed and numerator and denominator:
        direction = (f"positive log2FC = higher in {numerator} (numerator) than "
                     f"{denominator} (denominator)")
        confirmation = "confirmed"
    else:
        direction = "not confirmed"
        confirmation = "not confirmed"
    if confirmed_at:
        confirmation += f" at {confirmed_at}"

    columns = provenance.get("column_names")
    column_text = ", ".join(str(value) for value in columns) if isinstance(columns, list) else ""
    selected = (
        f"gene ID={provenance.get('gene_id_column') or 'not recorded'}; "
        f"log2 fold change={provenance.get('log2fc_column') or 'not recorded'}; "
        f"adjusted p-value={provenance.get('adjusted_p_column') or 'not recorded'}"
    )
    lines = [
        f"Original basename: {provenance.get('original_basename') or 'not recorded'}",
        f"Import timestamp: {provenance.get('imported_at') or 'not recorded'}",
        f"Project copy: {provenance.get('project_copy') or (payload.get('input') or {}).get('deseq2_results') or 'not recorded'}",
        f"Project-copy SHA-256: {provenance.get('sha256') or 'not recorded'}",
        f"Project-copy byte size: {provenance.get('byte_size') if provenance.get('byte_size') is not None else 'not recorded'}",
        f"Imported rows: {provenance.get('row_count') if provenance.get('row_count') is not None else 'not recorded'}",
        f"Imported columns: {column_text or 'not recorded'}",
        f"Selected columns: {selected}",
        f"Upstream differential-expression method: {provenance.get('upstream_method') or 'unknown'}",
        f"Upstream LFC shrinkage: {provenance.get('lfc_shrinkage') or 'unknown'}",
        f"Upstream p-adjustment method: {provenance.get('p_adjustment_method') or 'unknown'}",
        f"Confirmed numerator/denominator semantics: {direction} ({confirmation}).",
    ]
    return lines


def configured_samples_path(root: Path, payload: dict) -> tuple[Path, str]:
    """Resolve input.samples relative to the project while retaining its report label."""
    configured = str((payload.get("input") or {}).get("samples") or "config/samples.tsv").strip()
    path = Path(configured)
    if not path.is_absolute():
        path = root / path
    return path, configured


def external_active_workflow(payload: dict) -> dict:
    """Report only downstream switches that can run on an imported DE table."""
    workflow = payload.get("workflow") or {}
    return {key: workflow[key] for key in ("enrichment", "figures") if key in workflow}


def microarray_active_workflow(payload: dict) -> dict:
    """Return only switches that can change a microarray execution route."""
    workflow = payload.get("workflow") or {}
    active = ("enrichment", "figures", "gsva")
    selected = {key: workflow[key] for key in active if key in workflow}
    ppi = payload.get("ppi") or {}
    if "enabled" in ppi:
        selected["protein_interaction_network"] = bool(ppi["enabled"])
    return selected


def route_active_workflow(payload: dict) -> dict:
    input_type = (payload.get("input") or {}).get("type")
    if input_type == "deseq2_results":
        return external_active_workflow(payload)
    if input_type == "microarray":
        return microarray_active_workflow(payload)
    return payload.get("workflow") or {}


_STRANDEDNESS_LABELS = {0: "unstranded", 1: "forward", 2: "reverse"}
_STRANDEDNESS_PATH = "results/aligned/strandedness.txt"
_COUNTS_PATH = "results/counts/counts.txt"
_LOCAL_READ_INPUT_TYPES = {"fastq", "sra", "mixed"}


def realized_strandedness_route(config: dict) -> bool:
    """Return whether this run must carry a realized alignment strandedness record."""
    input_type = str((config.get("input") or {}).get("type") or "fastq").lower()
    aligner = str((config.get("workflow") or {}).get("aligner") or "STAR").upper()
    return input_type in _LOCAL_READ_INPUT_TYPES and aligner != "SALMON"


def _single_strandedness_token(path: Path, source_name: str) -> int:
    """Read exactly one ASCII strandedness token and reject every other shape/value."""
    try:
        tokens = path.read_text(encoding="utf-8").split()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{source_name} is missing or unreadable: {path}") from exc
    if len(tokens) != 1:
        raise ValueError(
            f"{source_name} must contain exactly one token (0, 1, or 2); found {len(tokens)}"
        )
    token = tokens[0]
    if token not in {"0", "1", "2"}:
        raise ValueError(
            f"{source_name} must be exactly 0, 1, or 2; found {token!r}"
        )
    return int(token)


def _featurecounts_header_strandedness(path: Path) -> int:
    """Extract the one realized ``-s`` argument from a featureCounts output header."""
    try:
        with path.open(encoding="utf-8") as handle:
            comments: list[str] = []
            for line in handle:
                if not line.startswith("#"):
                    break
                comments.append(line.rstrip("\n"))
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"featureCounts output is missing or unreadable: {path}") from exc
    header = " ".join(comments)
    if "Program:featureCounts" not in header:
        raise ValueError(f"featureCounts output lacks its program header: {path}")
    # featureCounts writes command tokens either quoted ("-s" "2") or unquoted
    # (-s 2). Match the exact option, then require one value so a duplicated or damaged
    # command line cannot be accepted as provenance.
    values = re.findall(r'(?:^|\s)"?-s"?(?=\s)\s+"?([^"\s]+)"?', header)
    if len(values) != 1:
        raise ValueError(
            "featureCounts header must contain exactly one -s value; "
            f"found {len(values)} in {path}"
        )
    value = values[0]
    if value not in {"0", "1", "2"}:
        raise ValueError(
            f"featureCounts header -s must be 0, 1, or 2; found {value!r} in {path}"
        )
    return int(value)


def load_realized_strandedness(root: Path, config: dict) -> dict | None:
    """Load and independently verify realized strandedness for active alignment routes."""
    if not realized_strandedness_route(config):
        return None
    strand_path = root / _STRANDEDNESS_PATH
    code = _single_strandedness_token(strand_path, "realized strandedness file")
    configured = (config.get("featurecounts") or {}).get("strandedness")
    provenance: dict = {
        "configured": {"code": configured},
        "realized": {
            "code": code,
            "label": _STRANDEDNESS_LABELS[code],
            "path": _STRANDEDNESS_PATH,
        },
    }
    workflow = config.get("workflow") or {}
    quantifier = str(workflow.get("quantifier") or "featureCounts")
    if quantifier.casefold() == "featurecounts":
        header_code = _featurecounts_header_strandedness(root / _COUNTS_PATH)
        if header_code != code:
            raise ValueError(
                "Realized strandedness mismatch: "
                f"{_STRANDEDNESS_PATH} records {code}, but {_COUNTS_PATH} records "
                f"featureCounts -s {header_code}"
            )
        provenance["featurecounts_header"] = {
            "code": header_code,
            "path": _COUNTS_PATH,
        }
    return provenance


def realized_strandedness_text(payload: dict) -> str | None:
    """Render only a complete realized record; never substitute the configured value."""
    provenance = payload.get("strandedness")
    realized = provenance.get("realized") if isinstance(provenance, dict) else None
    if not isinstance(realized, dict):
        return None
    code = realized.get("code")
    label = realized.get("label")
    path = realized.get("path")
    if (isinstance(code, bool) or code not in _STRANDEDNESS_LABELS
            or label != _STRANDEDNESS_LABELS[code] or not isinstance(path, str)
            or not path.strip()):
        return None
    return f"{label} ({code}; realized from {path})"


def deseq2_effect_size_semantics(payload: dict) -> dict | None:
    """Describe the raw-MLE cutoff separately from realized LFC shrinkage."""
    input_type = str((payload.get("input") or {}).get("type") or "fastq").lower()
    if input_type in {"microarray", "deseq2_results"}:
        return None
    engine = str((payload.get("workflow") or {}).get("de_engine") or "DESeq2")
    if engine.casefold() != "deseq2":
        return None
    de = payload.get("deseq2") or {}
    return {
        "configured_absolute_log2fc_cutoff": de.get("lfc_threshold"),
        "threshold_estimate": "raw, unshrunken DESeq2 maximum-likelihood log2FoldChange",
        "thresholded_outputs": [
            "up/down differential-expression gene sets",
            "enrichment input sets derived from those up/down sets",
        ],
        "shrinkage": {
            "realized_method": (payload.get("session_info") or {}).get("shrinkage_used"),
            "role": (
                "stabilized effect display/ranking where used; not up/down or enrichment-set "
                "cutoff classification"
            ),
        },
    }


def effect_size_semantics_lines(payload: dict) -> list[str]:
    """Render the recorded DESeq2 effect-size contract without inferring missing facts."""
    semantics = payload.get("effect_size_semantics")
    if not isinstance(semantics, dict):
        return []
    cutoff = semantics.get("configured_absolute_log2fc_cutoff")
    estimate = semantics.get("threshold_estimate")
    shrinkage = semantics.get("shrinkage")
    if not estimate or not isinstance(shrinkage, dict):
        return []
    realized_method = shrinkage.get("realized_method") or "not recorded"
    role = shrinkage.get("role") or "not recorded"
    return [
        f"Configured effect cutoff: absolute {estimate} >= {cutoff}",
        "Cutoff scope: up/down differential-expression gene sets and enrichment input "
        "sets derived from them.",
        f"Realized LFC shrinkage: {realized_method}",
        f"Shrinkage role: {role}.",
    ]


def external_active_customizations(payload: dict) -> dict:
    """Omit stale read-processing and local-model defaults from external-route reports."""
    customized = payload.get("customized_parameters") or {}
    active_exact = {
        "input.type", "deseq2.alpha", "deseq2.lfc_threshold",
        "workflow.enrichment", "workflow.figures",
    }
    active_prefixes = ("enrichment.", "ppi.", "gene_sets.", "figures.")
    return {
        key: value for key, value in customized.items()
        if key in active_exact or key.startswith(active_prefixes)
    }


def microarray_active_customizations(payload: dict) -> dict:
    customized = payload.get("customized_parameters") or {}
    active_exact = {
        "input.type", "workflow.enrichment", "workflow.figures", "workflow.gsva",
        "deseq2.design_formula", "deseq2.contrasts", "deseq2.alpha",
        "deseq2.lfc_threshold",
    }
    active_prefixes = (
        "microarray.", "reference.organism", "deseq2.reference_level.",
        "enrichment.", "ppi.",
        "gene_sets.", "figures.", "resources.", "rule_threads.",
    )
    return {
        key: value for key, value in customized.items()
        if key in active_exact or key.startswith(active_prefixes)
    }


def _display_customization_key(key: str, input_type: str | None) -> str:
    if input_type == "microarray":
        microarray_labels = {
            "deseq2.design_formula": "analysis.design_formula",
            "deseq2.contrasts": "analysis.contrasts",
            "deseq2.alpha": "differential_expression.alpha",
            "deseq2.lfc_threshold": "differential_expression.lfc_threshold",
        }
        if key in microarray_labels:
            return microarray_labels[key]
        prefix = "deseq2.reference_level."
        if key.startswith(prefix):
            return "analysis.reference_level." + key[len(prefix):]
    return key


def _format_reference_level(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in value.items()) or "not configured"
    return str(value) if value not in (None, "") else "not configured"


def _format_contrasts(value) -> str:
    if not isinstance(value, list):
        return str(value or "none configured")
    rendered = []
    for item in value:
        if not isinstance(item, dict):
            rendered.append(str(item))
            continue
        numerator = item.get("numerator")
        denominator = item.get("denominator")
        factor = item.get("factor")
        name = item.get("name")
        if numerator and denominator:
            label = f"{numerator} vs {denominator}"
            if factor:
                label += f" (factor: {factor})"
            if name:
                label += f" [{name}]"
            rendered.append(label)
        else:
            rendered.append(json.dumps(item, sort_keys=True))
    return "; ".join(rendered) or "none configured"


def report_software_versions(payload: dict) -> dict:
    versions = payload.get("software_versions") or {}
    if (payload.get("input") or {}).get("type") != "deseq2_results":
        return versions
    return {key: value for key, value in versions.items() if key in {"snakemake", "python", "Rscript"}}


def report_r_packages(payload: dict) -> dict:
    packages = payload.get("r_packages") or {}
    input_type = (payload.get("input") or {}).get("type")
    if input_type == "microarray":
        workflow = payload.get("workflow") or {}
        active = {"limma"}
        if workflow.get("figures", True):
            active.add("ggplot2")
        if workflow.get("enrichment", True):
            active.update({"clusterProfiler", "enrichplot", "gprofiler2", "DOSE"})
            orgdb = str((payload.get("enrichment") or {}).get("orgdb") or "").strip()
            if orgdb:
                active.add(orgdb)
        if (payload.get("ppi") or {}).get("enabled", True):
            active.add("STRINGdb")
        return {key: value for key, value in packages.items() if key in active}
    if input_type != "deseq2_results":
        return packages
    local_model_packages = {"DESeq2", "limma", "apeglm", "ashr", "edgeR", "tximport"}
    return {key: value for key, value in packages.items() if key not in local_model_packages}


def normalize_external_report_payload(payload: dict) -> dict:
    """Remove inactive local-route settings from the imported-results report record."""
    if (payload.get("input") or {}).get("type") != "deseq2_results":
        return payload
    normalized = dict(payload)
    normalized["workflow"] = external_active_workflow(payload)
    de = payload.get("deseq2") or {}
    normalized["deseq2"] = {
        key: de.get(key) for key in ("alpha", "lfc_threshold") if key in de
    }
    normalized["software_versions"] = report_software_versions(payload)
    normalized["r_packages"] = report_r_packages(payload)
    normalized["customized_parameters"] = external_active_customizations(payload)
    normalized["download_integrity"] = {}
    normalized["reference_integrity"] = {}
    normalized["output_paths"] = [
        value for value in payload.get("output_paths", [])
        if value in {
            "results/deseq2/deseq2_results.csv",
            "results/figures/volcano.png",
            "results/enrichment/enrichment_summary.txt",
            "results/networks/string_ppi_provenance.json",
        }
    ]
    session = dict(payload.get("session_info") or {})
    session.pop("shrinkage_used", None)
    normalized["session_info"] = session
    for inactive in (
            "fastp", "sortmerna", "star", "featurecounts", "strandedness",
            "effect_size_semantics"):
        normalized.pop(inactive, None)
    return normalized


def normalize_microarray_report_payload(payload: dict) -> dict:
    """Remove inactive RNA read-processing settings from a microarray run record."""
    if (payload.get("input") or {}).get("type") != "microarray":
        return payload
    normalized = dict(payload)
    normalized["workflow"] = microarray_active_workflow(payload)
    analysis = payload.get("deseq2") or {}
    normalized["deseq2"] = {
        key: analysis[key]
        for key in ("design_formula", "reference_level", "contrasts", "alpha", "lfc_threshold")
        if key in analysis
    }
    normalized["analysis_method"] = {
        "engine": "limma",
        "empirical_bayes": {"trend": True, "robust": True},
        "p_adjustment_method": "Benjamini-Hochberg",
        "lfc_shrinkage": False,
        "effect_size_uncertainty": "moderated log2-fold-change standard error",
    }
    reference = payload.get("reference") or {}
    normalized["reference"] = {
        key: reference[key] for key in ("organism_name", "organism", "taxon_id")
        if key in reference
    }
    versions = payload.get("software_versions") or {}
    normalized["software_versions"] = {
        key: value for key, value in versions.items()
        if key in {"snakemake", "python", "Rscript"}
    }
    active_r = report_r_packages(payload)
    all_r = payload.get("r_packages") or {}
    normalized["r_packages"] = active_r
    normalized["environment_r_packages"] = {
        key: value for key, value in all_r.items() if key not in active_r
    }
    normalized["customized_parameters"] = microarray_active_customizations(payload)
    for inactive in (
            "fastp", "sortmerna", "star", "featurecounts", "strandedness",
            "effect_size_semantics"):
        normalized.pop(inactive, None)
    return normalized


def de_engine_label(payload: dict, is_microarray: bool, shrink_realized, shrink_configured) -> str:
    """Human-readable "<engine>, shrinkage: <method>" for the run summary.

    Only DESeq2 applies LFC shrinkage; run_voom.R and run_edger.R set resLFC <- res,
    and the microarray route uses limma. Naming DESeq2/apeglm for those runs reports an
    engine and a method that never ran.
    """
    if is_microarray:
        return "limma (no LFC shrinkage)"
    if (payload.get("input") or {}).get("type") == "deseq2_results":
        return "externally supplied DE results (no local DE model or LFC shrinkage)"
    engine_key = str((payload.get("workflow") or {}).get("de_engine", "deseq2")).lower()
    engine = _DE_ENGINE_LABELS.get(engine_key, engine_key or "DESeq2")
    if engine != "DESeq2":
        return f"{engine} (no LFC shrinkage)"
    return f"DESeq2, shrinkage: {shrink_realized or shrink_configured}"


def select_tools(config: dict) -> dict:
    # Tool version probes for this run: route-active entries from TOOLS plus whichever
    # trimmer / rRNA filter / contamination screen the config actually enabled, so a run that used
    # trim_galore, ribodetector, or fastq_screen is reproducible from what gets recorded.
    wf = config.get("workflow", {})
    input_type = str((config.get("input") or {}).get("type", "fastq"))
    reads_processed = input_type not in ("count_matrix", "microarray", "deseq2_results")
    # Every route is orchestrated by Snakemake, uses Python for reports/checks and
    # runs an R analysis step. Read-processing executables are added only when the
    # DAG can actually invoke them.
    tools = {key: TOOLS[key] for key in ("snakemake", "python", "Rscript")}
    if reads_processed:
        tools["multiqc"] = TOOLS["multiqc"]
        if wf.get("fastqc_pre_trim", True) or (
                wf.get("fastqc_post_trim", True) and wf.get("trimming", True)):
            tools["fastqc"] = TOOLS["fastqc"]
        aligner = str(wf.get("aligner") or "STAR").upper()
        if aligner == "SALMON":
            tools["salmon"] = TOOLS["salmon"]
            tools["gffread"] = TOOLS["gffread"]
        elif aligner == "HISAT2":
            tools["HISAT2"] = TOOLS["HISAT2"]
            tools["samtools"] = TOOLS["samtools"]
            tools["featureCounts"] = TOOLS["featureCounts"]
        else:
            tools["STAR"] = TOOLS["STAR"]
            tools["samtools"] = TOOLS["samtools"]
            if str(wf.get("quantifier") or "featureCounts") == "featureCounts":
                tools["featureCounts"] = TOOLS["featureCounts"]
    # Mirror the Snakefile's gates exactly (workflow/Snakefile: TRIMMING, RRNA_FILTER,
    # CONTAM_SCREEN). They are strictly stronger than the workflow switches alone:
    #   - the read-processing steps do not exist at all when the input is a count matrix,
    #     a microarray series, or an uploaded DESeq2 table, regardless of the switches,
    #     which keep their defaults in a project converted from a FASTQ run;
    #   - the contamination screen additionally needs a FastQ Screen config path.
    # Recording a version for a tool the run never executed is the misreporting this
    # function exists to prevent, so the two sets of conditions must not drift.
    if reads_processed and wf.get("trimming", True):
        trimmer_name, trimmer_cmd = TRIMMER_TOOL_CMD.get(wf.get("trimmer", "fastp"),
                                                        TRIMMER_TOOL_CMD["fastp"])
        tools[trimmer_name] = trimmer_cmd
    if reads_processed and wf.get("rrna_filtering"):
        rrna_name, rrna_cmd = RRNA_TOOL_CMD.get(wf.get("rrna_tool", "sortmerna"), RRNA_TOOL_CMD["sortmerna"])
        tools[rrna_name] = rrna_cmd
    if (reads_processed and wf.get("contamination_screen")
            and (config.get("contamination") or {}).get("conf")):
        tools["fastq_screen"] = ["fastq_screen", "--version"]
    return tools


# Key R analysis packages (DE, enrichment, network, figures). Their versions are the
# effective "database versions" for the annotation/enrichment back-ends (OrgDb, MSigDB).
R_PACKAGES = [
    "DESeq2", "limma", "apeglm", "tximport", "clusterProfiler", "enrichplot",
    "gprofiler2", "STRINGdb", "msigdbr", "DOSE", "ggplot2",
]


def r_package_versions(extra=None):
    pkgs = list(R_PACKAGES) + [p for p in (extra or []) if p]
    code = ('for (p in commandArgs(TRUE)) cat(sprintf("%s\\t%s\\n", p, '
            'tryCatch(as.character(packageVersion(p)), error = function(e) "not installed")))')
    try:
        result = subprocess.run(["Rscript", "-e", code, *pkgs],
                                capture_output=True, text=True, timeout=60, check=False)
        out = {}
        for line in result.stdout.splitlines():
            if "\t" in line:
                name, ver = line.split("\t", 1)
                out[name.strip()] = ver.strip()
        return out
    except Exception:
        return {}


def run_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        # Skip leading log banners (SortMeRNA prints "[process:...] === Options ... ==="
        # before its version) and pick the first meaningful line that carries a version number.
        lines = [ln.strip() for ln in (result.stdout or result.stderr).splitlines()
                 if ln.strip() and not ln.strip().startswith("[")]
        if not lines:
            return "unknown"
        for ln in lines:
            if any(ch.isdigit() for ch in ln):
                # Extract just the version token, dropping tool names, paths and banners
                # (e.g. "/home/.../hisat2-align-s version 2.2.2" -> "2.2.2").
                m = re.search(r"v?(\d+(?:\.\d+)+[A-Za-z0-9.\-+]*)", ln)
                return m.group(1) if m else ln
        return lines[0]
    except Exception as exc:
        return f"unavailable ({exc.__class__.__name__})"


def diff_configs(defaults: dict, used: dict, prefix: str = "") -> dict:
    changed: dict = {}
    for key, value in used.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in defaults:
            continue
        default_value = defaults[key]
        if isinstance(value, dict) and isinstance(default_value, dict):
            changed.update(diff_configs(default_value, value, path))
        elif value != default_value:
            changed[path] = {"default": default_value, "used": value}
    return changed


def drop_project(config: dict) -> dict:
    return {k: v for k, v in config.items() if k != "project"}


def collect_warnings(sanity_text: str) -> list[str]:
    return [line.strip() for line in sanity_text.splitlines() if "WARNING" in line or "REVIEW_REQUIRED" in line]


def parse_session_info(text: str) -> dict:
    # run_deseq2.R / run_edger.R / run_limma.R / run_voom.R / ingest_deseq2_results.R all write
    # results/reports/sessionInfo.txt via capture.output(sessionInfo()); run_deseq2.R additionally
    # prefixes a "Shrinkage method used: ..." provenance line (see run_deseq2.R, near sessionInfo()).
    # Absent entirely on the microarray/limma backends (no shrinkage) or when the R step never ran.
    info: dict = {}
    for line in text.splitlines():
        if line.startswith("Shrinkage method used:"):
            info["shrinkage_used"] = line.split(":", 1)[1].strip()
        elif line.startswith("Platform:"):
            info["r_platform"] = line.split(":", 1)[1].strip()
        elif line.startswith("BLAS:"):
            info["blas"] = line.split(":", 1)[1].strip()
        elif line.startswith("LAPACK:"):
            info["lapack"] = line.split(":", 1)[1].strip()
    return info


def platform_provenance(session_info: dict) -> dict:
    # OS/arch of the machine that ran this Python process, plus the R-side platform and
    # BLAS/LAPACK line when reachable (sessionInfo.txt exists) -- distinguishes a WSL2 run
    # from a native Linux one, and flags an unexpected non-reference BLAS backend, which is
    # the usual explanation when two runs of the same data disagree in the last digits.
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "python_platform": platform.platform(),
        "r_platform": session_info.get("r_platform"),
        "r_blas": session_info.get("blas"),
        "r_lapack": session_info.get("lapack"),
    }


def download_integrity(root: Path) -> dict:
    # Aggregate the per-file checksum sidecars written by the download rule: how many FASTQ
    # downloads were verified against ENA's published MD5 (a data-integrity guarantee).
    cdir = root / "results" / "qc" / "checksums"
    if not cdir.is_dir():
        return {}
    verified = no_checksum = 0
    files: list[dict] = []
    for path in sorted(cdir.glob("*.txt")):
        parts = path.read_text(encoding="utf-8", errors="replace").strip().split("\t")
        status = parts[0] if parts else ""
        name = parts[1] if len(parts) > 1 else path.stem
        md5 = parts[2] if len(parts) > 2 else ""
        if status == "PASS":
            verified += 1
        elif status == "NO_CHECKSUM":
            no_checksum += 1
        files.append({"file": name, "status": status, "md5": md5})
    return {"verified": verified, "no_checksum": no_checksum,
            "total": verified + no_checksum, "files": files}


def reference_integrity(root: Path) -> dict:
    """Load the realized reference lock written by the fail-closed reference gate."""
    path = root / "references" / "reference.lock.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "lock_path": "references/reference.lock.json",
                "error": f"Reference lock could not be read: {exc}"}
    if not isinstance(payload, dict):
        return {"status": "FAIL", "lock_path": "references/reference.lock.json",
                "error": "Reference lock root is not a JSON object."}
    realized = dict(payload)
    realized["lock_path"] = "references/reference.lock.json"
    return realized


def reference_integrity_lines(payload: dict) -> list[str]:
    """Render source-checksum and canonical-hash evidence from the realized lock."""
    realized = payload.get("reference_integrity") or {}
    if not realized:
        return ["Realized reference lock: not recorded"]
    if realized.get("error"):
        return [f"Realized reference lock: {realized.get('status') or 'FAIL'}",
                f"Reference-lock error: {realized['error']}"]

    lines = [
        f"Realized reference lock: {realized.get('status') or 'not recorded'} "
        f"({realized.get('lock_path') or 'references/reference.lock.json'})"
    ]
    for key, label in (("genome", "Genome"), ("annotation", "Annotation")):
        integrity = ((realized.get(key) or {}).get("integrity") or {})
        content = ((realized.get(key) or {}).get("content") or {})
        lines += [
            f"{label} source MD5: {integrity.get('source_md5') or 'not recorded'} "
            f"({integrity.get('md5_status') or 'not recorded'}; "
            f"configured={integrity.get('configured_md5') or 'not configured'})",
            f"{label} canonical SHA-256: {integrity.get('canonical_sha256') or 'not recorded'}",
            f"{label} source/canonical bytes: {integrity.get('source_bytes', 'not recorded')} / "
            f"{integrity.get('canonical_bytes', 'not recorded')}",
        ]
        if key == "genome":
            lines.append(
                f"{label} records/bases: {content.get('record_count', 'not recorded')} / "
                f"{content.get('total_bases', 'not recorded')}"
            )
        else:
            evidence = content.get("evidence_counts") or {}
            lines.append(
                f"{label} features (gene/exon/CDS): "
                f"{evidence.get('gene', 'not recorded')}/"
                f"{evidence.get('exon', 'not recorded')}/"
                f"{evidence.get('CDS', 'not recorded')}"
            )
    compatibility = realized.get("contig_compatibility") or {}
    counting = realized.get("counting_contract") or {}
    lines.append(
        "Configured counting contract: feature_type="
        f"{','.join(str(value) for value in (counting.get('feature_types') or [])) or 'not recorded'}; "
        f"attribute_type={counting.get('attribute_type') or 'not recorded'}; "
        f"eligible rows={counting.get('feature_rows', 'not recorded')}; "
        f"rows missing attribute={counting.get('feature_rows_missing_attribute', 'not recorded')}"
    )
    lines.append(
        "Compatible contigs (overlap/annotation): "
        f"{compatibility.get('overlap_contigs', 'not recorded')}/"
        f"{compatibility.get('annotation_contigs', 'not recorded')}"
    )
    fraction = compatibility.get("feature_row_overlap_fraction")
    threshold = compatibility.get("minimum_feature_row_overlap_fraction")
    lines.append(
        "Compatible annotation feature rows: "
        f"{compatibility.get('compatible_feature_rows', 'not recorded')}/"
        f"{compatibility.get('annotation_feature_rows', 'not recorded')} "
        f"({fraction:.2%}; required >= {threshold:.0%})"
        if isinstance(fraction, (int, float)) and isinstance(threshold, (int, float))
        else "Compatible annotation feature rows: not recorded"
    )
    return lines


def existing_outputs(root: Path) -> list[str]:
    candidates = [
        "results/counts/counts.txt",
        "results/deseq2/deseq2_results.csv",
        "results/qc/multiqc/multiqc_report.html",
        "results/figures/pca.png",
        "results/figures/volcano.png",
        "results/enrichment/enrichment_summary.txt",
        "results/networks/string_ppi_provenance.json",
    ]
    return [c for c in candidates if (root / c).exists()]


_ENRICHMENT_MAPPING_PREFIXES = (
    "Eligible ID mapping keytypes:",
    "Identifier routing policy:",
    "Accepted ID mapping routes:",
    "Tested input IDs retained after mapping/exclusion:",
    "Significant input IDs retained after mapping/exclusion:",
    "Up-regulated input IDs retained after mapping/exclusion:",
    "Down-regulated input IDs retained after mapping/exclusion:",
    "Mapped tested-gene universe",
    "GO effective annotated ORA universes:",
    "DO effective annotated ORA universe:",
    "OrgDb annotation identity:",
    "KEGG identity verification:",
    "KEGG retrieval:",
    "KEGG effective resource universe:",
    "KEGG supported foreground:",
    "KEGG eligible hypotheses/gene sets:",
    "KEGG adjusted results:",
    "KEGG resource status:",
    "Unmapped input IDs excluded:",
    "Ambiguous input IDs excluded:",
    "One-to-many mappings observed:",
    "Cross-keytype discordance observed:",
    "Many-to-one Entrez groups collapsed",
    "Direction-conflict Entrez IDs excluded:",
    "Source IDs present in both up/down inputs:",
    "Foreground intersection (up/down Entrez)",
    "Mapping interpretation gate:",
    "Direction-conflict gate:",
    "GO/DO annotation-resource status:",
    "Universe policy:",
    "ORA parameters:",
    "ORA multiple-testing families:",
    "GSEA parameters:",
    "Mapping limitation:",
)


def enrichment_mapping_evidence(root: Path) -> dict:
    """Load the exact identifier-mapping evidence emitted by enrichment."""
    path = root / "results" / "enrichment" / "enrichment_summary.txt"
    if not path.exists():
        return {}
    lines = [line.strip() for line in path.read_text(
        encoding="utf-8", errors="replace").splitlines()]
    evidence = [line for line in lines if line.startswith(_ENRICHMENT_MAPPING_PREFIXES)]
    if not evidence:
        return {}
    return {"summary_path": "results/enrichment/enrichment_summary.txt", "evidence": evidence}


_PPI_REQUIRED_FIELDS = {
    "database": (
        "name", "configured_version", "realized_version", "realized_build",
        "taxon", "query_date_utc",
    ),
    "software": ("R", "STRINGdb", "igraph", "ggplot2"),
    "configuration": (
        "seed_source", "max_seed_genes", "score_threshold_combined",
        "string_combined_score_scale", "stored_edge_weight", "hub_label_count", "layout",
    ),
    "realized": (
        "seed_source", "seed_input_count", "seed_after_limit_count", "mapped_seed_count",
        "mapped_string_id_count", "interactions_returned_count",
        "interactions_passing_threshold_count", "score_threshold_combined",
        "minimum_combined_score", "maximum_combined_score", "node_count", "edge_count",
        "module_count", "hub_label_count", "layout_method", "layout_fallback_reason",
        "figure_width_in", "figure_height_in",
    ),
    "methods": ("edge_source", "community_detection", "betweenness", "layout", "figure_labels"),
}
_PPI_METHOD_FIELDS = {
    "edge_source": ("method", "evidence", "threshold", "stored_weight"),
    "community_detection": ("algorithm", "weights", "seed"),
    "betweenness": ("algorithm", "directed", "edge_distance"),
    "layout": ("requested", "realized", "seed"),
    "figure_labels": ("algorithm", "selection", "seed"),
}


def ppi_provenance(root: Path) -> dict:
    """Load the realized STRING sidecar without substituting configured values."""
    relpath = "results/networks/string_ppi_provenance.json"
    path = root / relpath
    if not path.exists():
        return {"status": "NOT_RECORDED", "reason": "not recorded", "sidecar_path": relpath}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "INVALID", "reason": f"malformed sidecar ({exc.__class__.__name__})",
            "sidecar_path": relpath,
        }
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return {
            "status": "INVALID", "reason": "unsupported or missing schema_version",
            "sidecar_path": relpath,
        }
    missing: list[str] = []
    for group, fields in _PPI_REQUIRED_FIELDS.items():
        value = payload.get(group)
        if not isinstance(value, dict):
            missing.append(group)
            continue
        missing.extend(f"{group}.{field}" for field in fields if field not in value)
    methods = payload.get("methods") if isinstance(payload.get("methods"), dict) else {}
    for method, fields in _PPI_METHOD_FIELDS.items():
        value = methods.get(method)
        if not isinstance(value, dict):
            missing.append(f"methods.{method}")
            continue
        missing.extend(
            f"methods.{method}.{field}" for field in fields if field not in value)
    for field in ("status", "reason", "generated_at_utc"):
        if field not in payload:
            missing.append(field)
    if missing:
        return {
            "status": "INVALID", "reason": "missing required fields: " + ", ".join(missing),
            "sidecar_path": relpath,
        }
    if payload.get("status") not in {"PASS", "WARNING"}:
        return {
            "status": "INVALID", "reason": "sidecar status must be PASS or WARNING",
            "sidecar_path": relpath,
        }
    if payload.get("status") == "PASS":
        required_realized = {
            "database.realized_version": (payload.get("database") or {}).get("realized_version"),
            "database.taxon": (payload.get("database") or {}).get("taxon"),
            "database.query_date_utc": (payload.get("database") or {}).get("query_date_utc"),
            "realized.mapped_seed_count": (payload.get("realized") or {}).get("mapped_seed_count"),
            "realized.mapped_string_id_count": (payload.get("realized") or {}).get("mapped_string_id_count"),
            "realized.interactions_returned_count": (payload.get("realized") or {}).get("interactions_returned_count"),
            "realized.interactions_passing_threshold_count": (payload.get("realized") or {}).get("interactions_passing_threshold_count"),
            "realized.node_count": (payload.get("realized") or {}).get("node_count"),
            "realized.edge_count": (payload.get("realized") or {}).get("edge_count"),
            "realized.module_count": (payload.get("realized") or {}).get("module_count"),
            "realized.layout_method": (payload.get("realized") or {}).get("layout_method"),
        }
        unobserved = [key for key, value in required_realized.items() if value is None or value == ""]
        if unobserved:
            return {
                "status": "INVALID",
                "reason": "PASS sidecar lacks realized facts: " + ", ".join(unobserved),
                "sidecar_path": relpath,
            }
    realized = dict(payload)
    realized["sidecar_path"] = relpath
    return realized


def _recorded(value) -> str:
    if value is None or value == "":
        return "not recorded"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def ppi_provenance_lines(payload: dict) -> list[str]:
    """Render only realized sidecar facts; never fall back to PPI config."""
    provenance = payload.get("ppi_provenance")
    if not isinstance(provenance, dict):
        provenance = {"status": "INVALID", "reason": "not recorded"}
    database = provenance.get("database") if isinstance(provenance.get("database"), dict) else {}
    software = provenance.get("software") if isinstance(provenance.get("software"), dict) else {}
    realized = provenance.get("realized") if isinstance(provenance.get("realized"), dict) else {}
    methods = provenance.get("methods") if isinstance(provenance.get("methods"), dict) else {}
    edge = methods.get("edge_source") if isinstance(methods.get("edge_source"), dict) else {}
    community = methods.get("community_detection") if isinstance(methods.get("community_detection"), dict) else {}
    betweenness = methods.get("betweenness") if isinstance(methods.get("betweenness"), dict) else {}
    layout = methods.get("layout") if isinstance(methods.get("layout"), dict) else {}
    labels = methods.get("figure_labels") if isinstance(methods.get("figure_labels"), dict) else {}
    return [
        f"Sidecar: {_recorded(provenance.get('sidecar_path'))}",
        f"Status: {_recorded(provenance.get('status'))}; reason: {_recorded(provenance.get('reason'))}",
        f"STRING realized version/build: {_recorded(database.get('realized_version'))} / {_recorded(database.get('realized_build'))}",
        f"STRING taxon/query date (UTC): {_recorded(database.get('taxon'))} / {_recorded(database.get('query_date_utc'))}",
        f"Mapped seeds: {_recorded(realized.get('mapped_seed_count'))} of {_recorded(realized.get('seed_after_limit_count'))} after limit "
        f"({_recorded(realized.get('seed_input_count'))} input); unique STRING IDs: {_recorded(realized.get('mapped_string_id_count'))}",
        f"Interactions returned/passing threshold: {_recorded(realized.get('interactions_returned_count'))} / "
        f"{_recorded(realized.get('interactions_passing_threshold_count'))}; combined-score threshold/min/max: "
        f"{_recorded(realized.get('score_threshold_combined'))} / {_recorded(realized.get('minimum_combined_score'))} / "
        f"{_recorded(realized.get('maximum_combined_score'))}",
        f"Network nodes/edges/modules: {_recorded(realized.get('node_count'))} / "
        f"{_recorded(realized.get('edge_count'))} / {_recorded(realized.get('module_count'))}",
        f"STRING edge semantics: method={_recorded(edge.get('method'))}; evidence={_recorded(edge.get('evidence'))}; "
        f"threshold={_recorded(edge.get('threshold'))}; stored weight={_recorded(edge.get('stored_weight'))}",
        f"Community detection: {_recorded(community.get('algorithm'))}; weights={_recorded(community.get('weights'))}; "
        f"seed={_recorded(community.get('seed'))}",
        f"Betweenness: {_recorded(betweenness.get('algorithm'))}; directed={_recorded(betweenness.get('directed'))}; "
        f"edge distance={_recorded(betweenness.get('edge_distance'))}",
        f"Layout: requested={_recorded(layout.get('requested'))}; realized={_recorded(layout.get('realized'))}; "
        f"seed={_recorded(layout.get('seed'))}; fallback={_recorded(realized.get('layout_fallback_reason'))}",
        f"Figure labels: {_recorded(labels.get('algorithm'))}; selection={_recorded(labels.get('selection'))}; "
        f"seed={_recorded(labels.get('seed'))}",
        "PPI package versions: " + "; ".join(
            f"{name}={_recorded(software.get(name))}"
            for name in ("R", "STRINGdb", "igraph", "ggrepel", "ggplot2")
            if name in software
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    config = yaml.safe_load((root / "config/config.yaml").read_text(encoding="utf-8")) or {}
    default_path = root / "config/default_config.yaml"
    defaults = yaml.safe_load(default_path.read_text(encoding="utf-8")) if default_path.exists() else {}
    customized = diff_configs(drop_project(defaults), drop_project(config))
    # This legacy fallback is captured explicitly beside the realized record on active
    # alignment routes and is inactive everywhere else; do not leak it back as an apparent
    # analysis setting through the generic customization list.
    customized.pop("featurecounts.strandedness", None)

    # Validate realized strandedness and the featureCounts command header before creating
    # or updating any report. A missing/damaged sidecar or a header disagreement is a hard
    # provenance failure, not a reason to fall back to the configured default.
    strandedness = load_realized_strandedness(root, config)
    featurecounts_config = dict(config.get("featurecounts") or {})
    featurecounts_config.pop("strandedness", None)

    versions = {name: run_version(command) for name, command in select_tools(config).items()}
    r_pkgs = r_package_versions(extra=[config.get("enrichment", {}).get("orgdb")])
    sanity_path = root / "checks/sanity_checks.txt"
    sanity_text = sanity_path.read_text(encoding="utf-8") if sanity_path.exists() else ""
    session_path = root / "results/reports/sessionInfo.txt"
    session_text = session_path.read_text(encoding="utf-8") if session_path.exists() else ""
    session_info = parse_session_info(session_text)
    project = config.get("project", {})

    payload = {
        "run_date": datetime.now().isoformat(timespec="seconds"),
        "app_version": project.get("app_version"),
        "workflow_version": project.get("workflow_version"),
        "workflow_git_commit": workflow_git_commit(),
        "environment_lock_md5": env_lock_md5(),
        "snakemake_version": versions.get("snakemake"),
        "project": project,
        "input": config.get("input", {}),
        "reference": config.get("reference", {}),
        "microarray": config.get("microarray", {}),
        "enrichment": config.get("enrichment", {}),
        "ppi": config.get("ppi", {}),
        "workflow": config.get("workflow", {}),
        "deseq2": config.get("deseq2", {}),
        "fastp": config.get("fastp", {}),
        "sortmerna": config.get("sortmerna", {}),
        "star": config.get("star", {}),
        "featurecounts": featurecounts_config,
        "gene_sets": config.get("gene_sets", {}),
        "resources": config.get("resources", {}),
        "rule_threads": config.get("rule_threads", {}),
        "software_versions": versions,
        "r_packages": r_pkgs,
        "customized_parameters": customized,
        "warnings": collect_warnings(sanity_text),
        "output_paths": existing_outputs(root),
        "download_integrity": download_integrity(root),
        "enrichment_mapping": enrichment_mapping_evidence(root),
        "ppi_provenance": ppi_provenance(root),
        "reference_integrity": (
            reference_integrity(root)
            if str((config.get("input") or {}).get("type", "fastq"))
            not in {"count_matrix", "microarray", "deseq2_results"}
            else {}
        ),
        "sanity_checks": sanity_text,
        "session_info": session_info,
        "platform": platform_provenance(session_info),
    }
    if strandedness is not None:
        payload["strandedness"] = strandedness
    effect_size_semantics = deseq2_effect_size_semantics(payload)
    if effect_size_semantics is not None:
        payload["effect_size_semantics"] = effect_size_semantics
    payload = normalize_microarray_report_payload(payload)
    payload = normalize_external_report_payload(payload)
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (reports / "software_versions.txt").write_text(
        "\n".join(f"{k}: {v}" for k, v in payload["software_versions"].items()) + "\n",
        encoding="utf-8")
    (reports / "run_summary.txt").write_text(render_text(payload), encoding="utf-8")
    (reports / "tools_references.txt").write_text(render_tools_references(payload), encoding="utf-8")
    samples_tsv, samples_label = configured_samples_path(root, payload)
    samples_text = samples_tsv.read_text(encoding="utf-8") if samples_tsv.exists() else ""
    (reports / "study_design.txt").write_text(
        render_study_design(payload, samples_text, samples_label), encoding="utf-8")
    return 0


def render_text(p: dict) -> str:
    input_type = p.get("input", {}).get("type")
    heading = ("Imported Differential-expression Results Summary"
               if input_type == "deseq2_results" else
               "Microarray Analysis Run Summary" if input_type == "microarray" else
               "RNA-seq Analysis Run Summary")
    lines = [heading, "=" * len(heading), ""]
    lines += ["Project", "-------",
              f"Project name: {p['project'].get('name')}",
              f"Working directory: {p['project'].get('working_directory')}",
              f"Run date: {p['run_date']}",
              f"App version: {p['app_version']}    Workflow version: {p['workflow_version']}",
              f"Workflow commit: {p.get('workflow_git_commit') or 'n/a (packaged build)'}",
              f"Environment lock md5: {p.get('environment_lock_md5') or 'n/a'}",
              f"Snakemake: {p['snakemake_version']}", ""]
    is_microarray = input_type == "microarray"
    is_uploaded_results = input_type == "deseq2_results"
    if is_microarray:
        # No reference genome in microarray mode; document the GEO source instead.
        ma = p.get("microarray", {})
        enr = p.get("enrichment", {})
        lines += ["Microarray Source", "-----------------",
                  f"Organism: {p['reference'].get('organism_name')}",
                  f"GEO series: {ma.get('gse_accession')}  Platform: {ma.get('platform')}",
                  f"Source: {ma.get('source')}  Normalization: {ma.get('normalization')}  log2: {ma.get('log2_transform')}",
                  f"Enrichment keytype: {enr.get('keytype') or '(organism default)'}", ""]
    elif not is_uploaded_results:
        ref = p["reference"]
        lines += ["Reference", "---------",
                  f"Organism: {ref.get('organism_name')}  Strain: {ref.get('strain')}",
                  f"Source/release: {ref.get('source')} {ref.get('release', '')}",
                  f"Configured genome MD5: {ref.get('genome_md5') or 'not configured'}  "
                  f"Configured annotation MD5: {ref.get('annotation_md5') or 'not configured'}",
                  *reference_integrity_lines(p), ""]
    de = p["deseq2"]
    # Name the engine that actually ran. Only DESeq2 shrinks: run_voom.R and run_edger.R
    # set resLFC <- res and never call lfcShrink, and limma (microarray) has no shrinkage
    # either, so reporting "DESeq2, shrinkage: apeglm" for those runs names both the wrong
    # engine and a method that was never applied. Prefer the REALISED shrinkage method
    # run_deseq2.R recorded in sessionInfo.txt over the configured one: apeglm can silently
    # fall back to ashr (see run_deseq2.R), so the config value alone can misreport what ran.
    shrink_realized = p.get("session_info", {}).get("shrinkage_used")
    de_method = de_engine_label(p, is_microarray, shrink_realized, de.get("shrinkage_method"))
    if is_uploaded_results:
        lines += ["Imported-results provenance", "---------------------------",
                  *uploaded_results_provenance_lines(p),
                  "Local analysis: BulkSeq Studio did not run read processing, mapping, count "
                  "quantification, a local differential-expression model, or local LFC shrinkage.",
                  f"Supplied-result thresholds: {uploaded_results_adjusted_p_name(p)} < {de.get('alpha')}  "
                  f"|log2FC| threshold: {de.get('lfc_threshold')}", ""]
    else:
        lines += ["Design", "------",
                  f"Design formula: {de.get('design_formula')}",
                  f"Reference level: {_format_reference_level(de.get('reference_level'))}",
                  f"Contrasts: {_format_contrasts(de.get('contrasts', []))}"]
        effect_lines = effect_size_semantics_lines(p)
        if effect_lines:
            lines += [f"Alpha (FDR): {de.get('alpha')}    Method: {de_method}", *effect_lines]
        else:
            lines.append(
                f"Alpha (FDR): {de.get('alpha')}  |log2FC| threshold: "
                f"{de.get('lfc_threshold')}  Method: {de_method}"
            )
        strandedness_text = realized_strandedness_text(p)
        if strandedness_text:
            lines.append(f"Realized strandedness: {strandedness_text}")
        lines.append("")
    reported_workflow = route_active_workflow(p)
    module_heading = ("Active downstream modules" if is_uploaded_results else
                      "Active microarray modules" if is_microarray else "Selected modules")
    lines += [module_heading, "-" * len(module_heading), json.dumps(reported_workflow, indent=2), ""]
    lines += ["Customized / Non-standard Parameters", "------------------------------------"]
    customized = (external_active_customizations(p) if is_uploaded_results else
                  microarray_active_customizations(p) if is_microarray else
                  p["customized_parameters"])
    if customized:
        for key, value in customized.items():
            display_key = _display_customization_key(key, input_type)
            lines.append(f"{display_key}: default={value['default']} used={value['used']}")
    else:
        lines.append("None detected against bundled defaults.")
    lines += ["", "Warnings", "--------"]
    lines += p["warnings"] or ["None."]
    di = p.get("download_integrity") or {}
    if di.get("total"):
        note = f"Checksum-verified against ENA MD5: {di['verified']} of {di['total']} FASTQ files"
        if di.get("no_checksum"):
            note += f" ({di['no_checksum']} without a published checksum)"
        lines += ["", "Data integrity (FASTQ downloads)", "--------------------------------", note]
    mapping = p.get("enrichment_mapping") or {}
    if mapping.get("evidence"):
        lines += ["", "Enrichment identifier mapping", "-----------------------------"]
        lines += [str(value) for value in mapping["evidence"]]
    lines += ["", "STRING PPI realized provenance", "------------------------------"]
    lines += ppi_provenance_lines(p)
    lines += ["", "Output paths", "------------"]
    lines += p["output_paths"] or ["None yet."]
    plat = p.get("platform", {})
    lines += ["", "Platform", "--------",
              f"OS: {plat.get('os')} {plat.get('os_release')}    Arch: {plat.get('arch')}",
              f"R platform: {plat.get('r_platform') or 'n/a (no R DE step ran)'}",
              f"R BLAS: {plat.get('r_blas') or 'n/a'}    R LAPACK: {plat.get('r_lapack') or 'n/a'}"]
    lines += ["", "Route-active software versions", "------------------------------"]
    lines += [f"{k}: {v}" for k, v in report_software_versions(p).items()]
    return "\n".join(lines) + "\n"


def render_tools_references(p: dict) -> str:
    ref = p.get("reference", {})
    enr = p.get("enrichment", {})
    ppi = p.get("ppi", {})
    ma = p.get("microarray", {})
    wf = p.get("workflow", {})
    input_type = p.get("input", {}).get("type")
    is_micro = input_type == "microarray"
    is_uploaded_results = input_type == "deseq2_results"
    input_description = (f"Input type: {input_type}    Aligner: {wf.get('aligner')}    "
                         f"Quantifier: {wf.get('quantifier')}")
    if is_micro:
        input_description = (
            "Input type: GEO series matrix (microarray); differential expression: limma; "
            "no local read processing, alignment, or count quantification"
        )
    if is_uploaded_results:
        input_description = "Input type: externally supplied differential-expression results (no local read processing)"
    lines = ["Tools, References and Databases", "===============================", "",
             f"Project: {p['project'].get('name')}",
             f"Run date: {p['run_date']}",
             f"App version: {p['app_version']}    Workflow version: {p['workflow_version']}",
             f"Workflow commit: {p.get('workflow_git_commit') or 'n/a (packaged build)'}",
             f"Environment lock md5: {p.get('environment_lock_md5') or 'n/a'}",
             input_description, ""]
    strandedness_text = realized_strandedness_text(p)
    if strandedness_text:
        lines += [f"Realized strandedness: {strandedness_text}", ""]
    effect_lines = effect_size_semantics_lines(p)
    if effect_lines:
        lines += ["DESeq2 effect-size semantics", "----------------------------",
                  *effect_lines, ""]
    if not is_micro and not is_uploaded_results and wf.get("rrna_filtering"):
        smr = p.get("sortmerna", {})
        lines += ["rRNA filtering: SortMeRNA (post-trim, pre-alignment)",
                  f"  paired mode: {smr.get('paired_mode') or 'paired_in'}    "
                  f"database: {smr.get('database') or 'SortMeRNA default rRNA db (smr_v4.3_default_db)'}", ""]
    if is_micro:
        lines += ["Microarray source", "-----------------",
                  f"Organism: {ref.get('organism_name')}",
                  f"GEO series: {ma.get('gse_accession')}    Platform: {ma.get('platform')}",
                  f"Source: {ma.get('source')}    Normalization: {ma.get('normalization')}", ""]
    elif is_uploaded_results:
        lines += ["Imported differential-expression results", "--------------------------------------",
                  *uploaded_results_provenance_lines(p),
                  "BulkSeq Studio did not run read processing, mapping, count quantification, a "
                  "local differential-expression model, or local LFC shrinkage on this route.", ""]
    else:
        lines += ["Reference genome and annotation", "-------------------------------",
                  f"Organism: {ref.get('organism_name')}    Strain: {ref.get('strain')}",
                  f"Assembly/package: {ref.get('package_id') or 'n/a'}    "
                  f"Source/release: {ref.get('source')} {ref.get('release', '')}".rstrip(),
                  f"Genome FASTA: {ref.get('genome_fasta_url') or ref.get('genome_fasta') or 'n/a'}",
                  f"Annotation: {ref.get('annotation_gtf_url') or ref.get('annotation_file') or 'n/a'}",
                  f"Genome MD5: {ref.get('genome_md5') or 'n/a'}    "
                  f"Annotation MD5: {ref.get('annotation_md5') or 'n/a'}",
                  *reference_integrity_lines(p), ""]
    lines += ["Enrichment databases and sources", "--------------------------------",
              f"KEGG organism code: {enr.get('kegg_organism') or 'n/a'}",
              f"Configured STRING taxon request: {ppi.get('taxon') or 'derive from organism'} "
              "(realized taxon is reported only from the sidecar below)",
              f"Bioconductor OrgDb: {enr.get('orgdb') or 'none (g:Profiler used for GO)'}",
              f"g:Profiler organism: {enr.get('gprofiler_organism') or 'n/a'}",
              f"Enrichment keytype: {enr.get('keytype') or '(organism default)'}    "
              f"Backend: {enr.get('backend') or 'clusterprofiler'}",
              "Note: KEGG, STRING and g:Profiler are queried live; their content version is the "
               "run date above. OrgDb / MSigDB versions are the R package versions listed below.", ""]
    mapping = p.get("enrichment_mapping") or {}
    if mapping.get("evidence"):
        lines += ["Enrichment identifier mapping", "-----------------------------"]
        lines += [str(value) for value in mapping["evidence"]] + [""]
    lines += ["STRING PPI realized provenance", "------------------------------"]
    lines += ppi_provenance_lines(p) + [""]
    lines += ["Route-active tool versions", "--------------------------"]
    lines += [f"{k}: {v}" for k, v in report_software_versions(p).items()]
    rp = report_r_packages(p)
    if rp:
        lines += ["", "Route-active R / Bioconductor package versions",
                  "---------------------------------------------"]
        lines += [f"{k}: {v}" for k, v in rp.items()]
    return "\n".join(lines) + "\n"


def render_study_design(p: dict, samples_tsv: str, samples_label: str | None = None) -> str:
    de = p.get("deseq2", {})
    wf = p.get("workflow", {})
    input_type = p.get("input", {}).get("type")
    is_micro = input_type == "microarray"
    is_uploaded_results = input_type == "deseq2_results"
    de_method = ("limma (microarray)" if is_micro else
                 "externally supplied results (no local differential-expression model)"
                 if is_uploaded_results else "DESeq2")
    lines = ["Study Design", "============", "",
             f"Project: {p['project'].get('name')}",
             f"Run date: {p['run_date']}",
             f"Input type: {input_type}    Differential expression: {de_method}", ""]
    if is_micro:
        ma = p.get("microarray", {})
        lines += [f"GEO series: {ma.get('gse_accession')}    Platform: {ma.get('platform')}"
                  f"    Source: {ma.get('source')}", ""]
    if is_uploaded_results:
        lines += ["Imported-results provenance", "---------------------------",
                  *uploaded_results_provenance_lines(p),
                  f"Supplied-result thresholds: {uploaded_results_adjusted_p_name(p)} < {de.get('alpha')}  "
                  f"|log2FC| threshold: {de.get('lfc_threshold')}",
                  "No per-sample metadata or local differential-expression model was used for this "
                  "results-only route.", ""]
        return "\n".join(lines) + "\n"
    sample_source = samples_label or str((p.get("input") or {}).get("samples") or "config/samples.tsv")
    lines += ["Design", "------",
              f"Design formula: {de.get('design_formula')}",
              f"Reference level: {_format_reference_level(de.get('reference_level'))}",
              f"Contrasts: {_format_contrasts(de.get('contrasts', []))}"]
    effect_lines = effect_size_semantics_lines(p)
    if effect_lines:
        lines += [f"Alpha (FDR): {de.get('alpha')}", *effect_lines]
    else:
        lines.append(
            f"Alpha (FDR): {de.get('alpha')}    |log2FC| threshold: {de.get('lfc_threshold')}"
        )
    if not is_micro:
        lines.append(f"Organellar genes: {wf.get('organellar_genes', 'keep')}")
    strandedness_text = realized_strandedness_text(p)
    if strandedness_text:
        lines.append(f"Realized strandedness: {strandedness_text}")
    lines += ["", f"Samples ({sample_source})", "----------------------------"]
    if samples_tsv.strip():
        lines += samples_tsv.rstrip("\n").splitlines()
    else:
        lines.append("samples.tsv not found.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
