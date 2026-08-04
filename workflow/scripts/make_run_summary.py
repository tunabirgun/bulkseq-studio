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
# app/core/config_models.py). TOOLS above only covers tools every run touches; these three are
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


def de_engine_label(payload: dict, is_microarray: bool, shrink_realized, shrink_configured) -> str:
    """Human-readable "<engine>, shrinkage: <method>" for the run summary.

    Only DESeq2 applies LFC shrinkage; run_voom.R and run_edger.R set resLFC <- res,
    and the microarray route uses limma. Naming DESeq2/apeglm for those runs reports an
    engine and a method that never ran.
    """
    if is_microarray:
        return "limma (no LFC shrinkage)"
    engine_key = str((payload.get("workflow") or {}).get("de_engine", "deseq2")).lower()
    engine = _DE_ENGINE_LABELS.get(engine_key, engine_key or "DESeq2")
    if engine != "DESeq2":
        return f"{engine} (no LFC shrinkage)"
    return f"DESeq2, shrinkage: {shrink_realized or shrink_configured}"


def select_tools(config: dict) -> dict:
    # Tool version probes for this run: TOOLS (always run) plus whichever trimmer / rRNA
    # filter / contamination screen the config actually enabled, so a run that used
    # trim_galore, ribodetector, or fastq_screen is reproducible from what gets recorded.
    wf = config.get("workflow", {})
    tools = dict(TOOLS)
    # Mirror the Snakefile's gates exactly (workflow/Snakefile: TRIMMING, RRNA_FILTER,
    # CONTAM_SCREEN). They are strictly stronger than the workflow switches alone:
    #   - the read-processing steps do not exist at all when the input is a count matrix,
    #     a microarray series, or an uploaded DESeq2 table, regardless of the switches,
    #     which keep their defaults in a project converted from a FASTQ run;
    #   - the contamination screen additionally needs a FastQ Screen config path.
    # Recording a version for a tool the run never executed is the misreporting this
    # function exists to prevent, so the two sets of conditions must not drift.
    reads_processed = str((config.get("input") or {}).get("type", "fastq")) not in (
        "count_matrix", "microarray", "deseq2_results")

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


def existing_outputs(root: Path) -> list[str]:
    candidates = [
        "results/counts/counts.txt",
        "results/deseq2/deseq2_results.csv",
        "results/qc/multiqc/multiqc_report.html",
        "results/figures/pca.png",
        "results/figures/volcano.png",
        "results/enrichment/enrichment_summary.txt",
    ]
    return [c for c in candidates if (root / c).exists()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    root = Path(args.project)
    config = yaml.safe_load((root / "config/config.yaml").read_text(encoding="utf-8")) or {}
    default_path = root / "config/default_config.yaml"
    defaults = yaml.safe_load(default_path.read_text(encoding="utf-8")) if default_path.exists() else {}
    customized = diff_configs(drop_project(defaults), drop_project(config))

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
        "featurecounts": config.get("featurecounts", {}),
        "gene_sets": config.get("gene_sets", {}),
        "resources": config.get("resources", {}),
        "rule_threads": config.get("rule_threads", {}),
        "software_versions": versions,
        "r_packages": r_pkgs,
        "customized_parameters": customized,
        "warnings": collect_warnings(sanity_text),
        "output_paths": existing_outputs(root),
        "download_integrity": download_integrity(root),
        "sanity_checks": sanity_text,
        "session_info": session_info,
        "platform": platform_provenance(session_info),
    }
    reports = root / "results/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (reports / "software_versions.txt").write_text("\n".join(f"{k}: {v}" for k, v in versions.items()) + "\n", encoding="utf-8")
    (reports / "run_summary.txt").write_text(render_text(payload), encoding="utf-8")
    (reports / "tools_references.txt").write_text(render_tools_references(payload), encoding="utf-8")
    samples_tsv = root / "config" / "samples.tsv"
    samples_text = samples_tsv.read_text(encoding="utf-8") if samples_tsv.exists() else ""
    (reports / "study_design.txt").write_text(render_study_design(payload, samples_text), encoding="utf-8")
    return 0


def render_text(p: dict) -> str:
    lines = ["RNA-seq Analysis Run Summary", "============================", ""]
    lines += ["Project", "-------",
              f"Project name: {p['project'].get('name')}",
              f"Working directory: {p['project'].get('working_directory')}",
              f"Run date: {p['run_date']}",
              f"App version: {p['app_version']}    Workflow version: {p['workflow_version']}",
              f"Workflow commit: {p.get('workflow_git_commit') or 'n/a (packaged build)'}",
              f"Environment lock md5: {p.get('environment_lock_md5') or 'n/a'}",
              f"Snakemake: {p['snakemake_version']}", ""]
    input_type = p.get("input", {}).get("type")
    is_microarray = input_type == "microarray"
    if is_microarray:
        # No reference genome in microarray mode; document the GEO source instead.
        ma = p.get("microarray", {})
        enr = p.get("enrichment", {})
        lines += ["Microarray Source", "-----------------",
                  f"Organism: {p['reference'].get('organism_name')}",
                  f"GEO series: {ma.get('gse_accession')}  Platform: {ma.get('platform')}",
                  f"Source: {ma.get('source')}  Normalization: {ma.get('normalization')}  log2: {ma.get('log2_transform')}",
                  f"Enrichment keytype: {enr.get('keytype') or '(organism default)'}", ""]
    else:
        ref = p["reference"]
        lines += ["Reference", "---------",
                  f"Organism: {ref.get('organism_name')}  Strain: {ref.get('strain')}",
                  f"Source/release: {ref.get('source')} {ref.get('release', '')}",
                  f"Genome MD5: {ref.get('genome_md5')}  Annotation MD5: {ref.get('annotation_md5')}", ""]
    de = p["deseq2"]
    # Name the engine that actually ran. Only DESeq2 shrinks: run_voom.R and run_edger.R
    # set resLFC <- res and never call lfcShrink, and limma (microarray) has no shrinkage
    # either, so reporting "DESeq2, shrinkage: apeglm" for those runs names both the wrong
    # engine and a method that was never applied. Prefer the REALISED shrinkage method
    # run_deseq2.R recorded in sessionInfo.txt over the configured one: apeglm can silently
    # fall back to ashr (see run_deseq2.R), so the config value alone can misreport what ran.
    shrink_realized = p.get("session_info", {}).get("shrinkage_used")
    de_method = de_engine_label(p, is_microarray, shrink_realized, de.get("shrinkage_method"))
    lines += ["Design", "------",
              f"Design formula: {de.get('design_formula')}",
              f"Reference level: {de.get('reference_level')}",
              f"Contrasts: {json.dumps(de.get('contrasts', []))}",
              f"Alpha (FDR): {de.get('alpha')}  |log2FC| threshold: {de.get('lfc_threshold')}  "
              f"Method: {de_method}", ""]
    lines += ["Selected modules", "----------------", json.dumps(p["workflow"], indent=2), ""]
    lines += ["Customized / Non-standard Parameters", "------------------------------------"]
    if p["customized_parameters"]:
        for key, value in p["customized_parameters"].items():
            lines.append(f"{key}: default={value['default']} used={value['used']}")
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
    lines += ["", "Output paths", "------------"]
    lines += p["output_paths"] or ["None yet."]
    plat = p.get("platform", {})
    lines += ["", "Platform", "--------",
              f"OS: {plat.get('os')} {plat.get('os_release')}    Arch: {plat.get('arch')}",
              f"R platform: {plat.get('r_platform') or 'n/a (no R DE step ran)'}",
              f"R BLAS: {plat.get('r_blas') or 'n/a'}    R LAPACK: {plat.get('r_lapack') or 'n/a'}"]
    lines += ["", "Software Versions", "-----------------"]
    lines += [f"{k}: {v}" for k, v in p["software_versions"].items()]
    return "\n".join(lines) + "\n"


def render_tools_references(p: dict) -> str:
    ref = p.get("reference", {})
    enr = p.get("enrichment", {})
    ppi = p.get("ppi", {})
    ma = p.get("microarray", {})
    wf = p.get("workflow", {})
    input_type = p.get("input", {}).get("type")
    is_micro = input_type == "microarray"
    lines = ["Tools, References and Databases", "===============================", "",
             f"Project: {p['project'].get('name')}",
             f"Run date: {p['run_date']}",
             f"App version: {p['app_version']}    Workflow version: {p['workflow_version']}",
             f"Workflow commit: {p.get('workflow_git_commit') or 'n/a (packaged build)'}",
             f"Environment lock md5: {p.get('environment_lock_md5') or 'n/a'}",
             f"Input type: {input_type}    Aligner: {wf.get('aligner')}    "
             f"Quantifier: {wf.get('quantifier')}", ""]
    if not is_micro and wf.get("rrna_filtering"):
        smr = p.get("sortmerna", {})
        lines += ["rRNA filtering: SortMeRNA (post-trim, pre-alignment)",
                  f"  paired mode: {smr.get('paired_mode') or 'paired_in'}    "
                  f"database: {smr.get('database') or 'SortMeRNA default rRNA db (smr_v4.3_default_db)'}", ""]
    if is_micro:
        lines += ["Microarray source", "-----------------",
                  f"Organism: {ref.get('organism_name')}",
                  f"GEO series: {ma.get('gse_accession')}    Platform: {ma.get('platform')}",
                  f"Source: {ma.get('source')}    Normalization: {ma.get('normalization')}", ""]
    else:
        lines += ["Reference genome and annotation", "-------------------------------",
                  f"Organism: {ref.get('organism_name')}    Strain: {ref.get('strain')}",
                  f"Assembly/package: {ref.get('package_id') or 'n/a'}    "
                  f"Source/release: {ref.get('source')} {ref.get('release', '')}".rstrip(),
                  f"Genome FASTA: {ref.get('genome_fasta_url') or ref.get('genome_fasta') or 'n/a'}",
                  f"Annotation: {ref.get('annotation_gtf_url') or ref.get('annotation_file') or 'n/a'}",
                  f"Genome MD5: {ref.get('genome_md5') or 'n/a'}    "
                  f"Annotation MD5: {ref.get('annotation_md5') or 'n/a'}", ""]
    lines += ["Enrichment databases and sources", "--------------------------------",
              f"KEGG organism code: {enr.get('kegg_organism') or 'n/a'}",
              f"STRING taxon: {ppi.get('taxon') or 'derived from organism'}",
              f"Bioconductor OrgDb: {enr.get('orgdb') or 'none (g:Profiler used for GO)'}",
              f"g:Profiler organism: {enr.get('gprofiler_organism') or 'n/a'}",
              f"Enrichment keytype: {enr.get('keytype') or '(organism default)'}    "
              f"Backend: {enr.get('backend') or 'clusterprofiler'}",
              "Note: KEGG, STRING and g:Profiler are queried live; their content version is the "
              "run date above. OrgDb / MSigDB versions are the R package versions listed below.", ""]
    lines += ["Tool versions", "-------------"]
    lines += [f"{k}: {v}" for k, v in p.get("software_versions", {}).items()]
    rp = p.get("r_packages", {})
    if rp:
        lines += ["", "R / Bioconductor package versions", "---------------------------------"]
        lines += [f"{k}: {v}" for k, v in rp.items()]
    return "\n".join(lines) + "\n"


def render_study_design(p: dict, samples_tsv: str) -> str:
    de = p.get("deseq2", {})
    wf = p.get("workflow", {})
    input_type = p.get("input", {}).get("type")
    is_micro = input_type == "microarray"
    de_method = "limma (microarray)" if is_micro else "DESeq2"
    lines = ["Study Design", "============", "",
             f"Project: {p['project'].get('name')}",
             f"Run date: {p['run_date']}",
             f"Input type: {input_type}    Differential expression: {de_method}", ""]
    if is_micro:
        ma = p.get("microarray", {})
        lines += [f"GEO series: {ma.get('gse_accession')}    Platform: {ma.get('platform')}"
                  f"    Source: {ma.get('source')}", ""]
    lines += ["Design", "------",
             f"Design formula: {de.get('design_formula')}",
             f"Reference level: {de.get('reference_level')}",
             f"Contrasts: {json.dumps(de.get('contrasts', []))}",
             f"Alpha (FDR): {de.get('alpha')}    |log2FC| threshold: {de.get('lfc_threshold')}",
             f"Organellar genes: {wf.get('organellar_genes', 'keep')}", "",
             "Samples (config/samples.tsv)", "----------------------------"]
    if samples_tsv.strip():
        lines += samples_tsv.rstrip("\n").splitlines()
    else:
        lines.append("samples.tsv not found.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
