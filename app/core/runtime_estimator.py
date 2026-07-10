from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

from app.core.config_models import AppConfig


# Alignment cost multiplier by genome-size class, relative to a 1.0 baseline.
REFERENCE_FACTORS = {
    "bacterial": 0.35,
    "yeast": 0.5,
    "fungal": 0.8,
    "plant": 1.4,
    "mammalian": 1.8,
    "custom": 1.0,
}

# STAR index build time (minutes) by genome-size class. Index time scales steeply
# with genome size, so it is tabulated rather than derived from REFERENCE_FACTORS
# (a small fungal index takes <1 min; a mammalian index tens of minutes).
INDEX_MINUTES = {
    "bacterial": 0.2,
    "yeast": 0.4,
    "fungal": 0.6,
    "plant": 25.0,
    "mammalian": 45.0,
    "custom": 10.0,
}


def _confidence_note(n: int) -> str:
    if n <= 0:
        return ("Uncalibrated — a first-run estimate can be off by 2-3x. Accuracy improves "
                "after each run on this machine.")
    if n < 3:
        return f"Rough — based on {n} past run{'s' if n != 1 else ''} on this machine."
    return f"Calibrated to this machine ({n} past runs)."


def estimate_runtime(
    config: AppConfig,
    metadata: pd.DataFrame | None = None,
    *,
    threads: int | None = None,
    memory_gb: int | None = None,
    calibration_factor: float = 1.0,
    calibration_runs: int = 0,
) -> dict[str, object]:
    # Phase model calibrated against a real fungal 6-sample paired-end run (4 cores):
    # download 15 min, alignment 18 min, QC 12 min cumulative. Coefficients are
    # per sequenced gigabase (gbase); thread-parallel phases are divided by an
    # efficiency factor, network-bound download by a weaker one.
    #
    # threads/memory_gb override the config values so the caller can estimate
    # against the machine the pipeline will actually run on (the GUI passes the
    # locally detected/allocated cores and RAM); they default to the config.
    sample_count = len(metadata) if metadata is not None else 0
    gbase = _total_gbase(metadata)
    # A DESeq2-results upload skips alignment/QC/download entirely (like a count matrix),
    # so it must not fall through to the alignment branch and report an inflated estimate.
    count_matrix = config.input.type in ("count_matrix", "deseq2_results")
    microarray = config.input.type == "microarray"
    ref_cat = config.reference.genome_size_category
    ref_factor = REFERENCE_FACTORS.get(ref_cat, 1.0)
    threads = max(threads if threads is not None else config.resources.total_threads, 1)
    memory_gb = memory_gb if memory_gb is not None else config.resources.total_memory_gb
    compute_par = min(threads, 12) ** 0.5     # compute steps scale ~sqrt(threads)
    io_par = min(threads, 4) ** 0.5           # download/IO scale weakly

    overhead = 2.0
    download = 0.0        # network-bound; kept OUT of the calibrated compute term
    download_gib = 0.0
    bottlenecks: list[str] = []

    if microarray:
        # GEO download/ingest + probe collapse + limma -> figures -> enrichment.
        compute = 3.0 + sample_count * 0.3
        compute += 1.0 if config.workflow.enrichment else 0.0
        compute += 0.4 if config.workflow.figures else 0.0
    elif count_matrix:
        # count_matrix / deseq2_results: alignment, QC and download are all skipped; only
        # DESeq2 -> figures -> enrichment run, which is fast.
        compute = sample_count * 0.2
        compute += 1.0 if config.workflow.enrichment else 0.0
        compute += 0.4 if config.workflow.figures else 0.0
    else:
        is_sra = config.input.type == "sra"
        # Download is a weak per-gbase anchor only: real ENA download time is network-bound and
        # spans minutes to hours independent of size (measured ~80x spread across runs), so it is
        # surfaced separately (download_size + download_note) rather than trusted in the estimate.
        download = (gbase * 0.8) / io_par if is_sra else 0.0
        # Download size for SRA/ENA: prefer ENA's reported gzipped byte sizes (download_bytes,
        # summed per run by metadata_to_samples); fall back to the ~0.35 bytes/base estimate
        # only when those are absent, so the GB figure populates even without fastq_bytes.
        if is_sra:
            dl_bytes = _total_download_bytes(metadata)
            if dl_bytes > 0:
                download_gib = dl_bytes / (1024 ** 3)
            elif gbase > 0:
                download_gib = (gbase * 1e9 * 0.35) / (1024 ** 3)

        # Per-gbase alignment minutes, recalibrated against real benchmark runs (STAR fit from
        # 3 runs at 4/12 threads; HISAT2/Salmon scaled proportionally, preserving speed order).
        per_gbase = {"Salmon": 0.8, "HISAT2": 1.3}.get(config.workflow.aligner, 1.65)
        # Graded RAM penalty: below the aligner's genome-scaled need, alignment slows toward a
        # 2x cap as memory gets tighter (STAR on a mammalian genome wants ~30 GB; Salmon/HISAT2
        # far less). At or above the need there is no penalty, so well-resourced runs are unchanged.
        need = {"STAR": 30, "HISAT2": 8, "Salmon": 8}.get(config.workflow.aligner, 8) * (ref_factor / 1.8)
        mem_factor = 1.0 if memory_gb >= need else min(2.0, 1.0 + 0.6 * (need - memory_gb) / max(need, 1e-6))
        align = (gbase * per_gbase * ref_factor * mem_factor) / compute_par
        index = 0.0
        if config.workflow.aligner == "STAR" and not config.reference.star_index:
            index = INDEX_MINUTES.get(ref_cat, 10.0)

        # QC/trim/rRNA/quant per-gbase minutes, recalibrated from benchmark runs (rRNA is high
        # because SortMeRNA is genuinely slow — one measured run, treated as an anchor).
        qc = (gbase * 0.63) / compute_par if (config.workflow.fastqc_pre_trim or config.workflow.fastqc_post_trim) else 0.0
        trim = (gbase * 0.5) / compute_par if config.workflow.trimming else 0.0
        rrna = (gbase * 9.0) / compute_par if config.workflow.rrna_filtering else 0.0
        quant = (gbase * 0.1) / compute_par
        downstream = 0.5 + (1.0 if config.workflow.enrichment else 0.0) + (0.3 if config.workflow.figures else 0.0)

        compute = index + align + qc + trim + rrna + quant + downstream
        # Floor only when the data volume is unknown (no base_count and no local FASTQ), so the
        # estimate is not unrealistically tiny. Overhead is added separately below.
        if gbase <= 0:
            compute = max(compute, 6.0 + sample_count * 1.5)

        if index >= 20:
            bottlenecks.append(f"{ref_cat} STAR index build (~{index:.0f} min)")
        if is_sra and download >= 10:
            bottlenecks.append("SRA/ENA download (network-bound)")
        if gbase >= 40:
            bottlenecks.append("large sequencing volume")
        if mem_factor > 1.15:
            bottlenecks.append("limited RAM for the aligner on this genome")

    # Apply the learned per-machine speed correction to the COMPUTE term only (not overhead
    # or the network-bound download). raw_compute (pre-factor) is what calibration compares the
    # actual wall time against, so the factor converges instead of compounding across runs.
    raw_compute = compute
    compute *= max(calibration_factor, 0.05)
    minutes = overhead + download + compute

    # Range widens when uncalibrated and narrows as the machine's speed is learned.
    lo_m, hi_m = ((0.8, 2.5) if calibration_runs == 0
                  else (0.7, 1.8) if calibration_runs < 3
                  else (0.85, 1.4))
    low = max(4.0, minutes * lo_m)
    high = max(low + 5.0, minutes * hi_m)

    return {
        "low_seconds": int(low * 60),
        "high_seconds": int(high * 60),
        "range": f"{_fmt(low)}-{_fmt(high)}",
        "confidence_note": _confidence_note(calibration_runs),
        "calibrated": calibration_runs >= 1,
        "calibration_runs": calibration_runs,
        "sample_count": sample_count,
        "sequencing_gbase": round(gbase, 2),
        "reference_group": ref_cat,
        "aligner": ("n/a (results upload)" if config.input.type == "deseq2_results"
                    else "n/a (microarray/limma)" if microarray
                    else "n/a (count matrix)" if count_matrix
                    else config.workflow.aligner),
        "threads": threads,
        "memory_gb": memory_gb,
        "compute_minutes": round(compute, 2),
        "raw_compute_minutes": round(raw_compute, 2),
        "download_minutes": round(download, 2),
        "download_size": _download_label(config, download_gib),
        "download_note": (
            "SRA/ENA download time is network-dependent and can range from minutes to hours "
            "regardless of size; it is not fully captured by the estimate above."
            if config.input.type == "sra" else "no read download"
        ),
        "bottlenecks": bottlenecks or (
            ["microarray mode: GEO download + limma, no alignment"] if microarray
            else ["results-upload mode: enrichment/figures only"] if config.input.type == "deseq2_results"
            else ["count-matrix mode: alignment skipped"] if count_matrix
            else ["none obvious from current configuration"]
        ),
    }


def _download_label(config: AppConfig, download_gib: float) -> str:
    # Human-readable estimate of what will be downloaded before/at run start, so the
    # user knows the network cost. Only the SRA/ENA FASTQ size is derivable up front
    # (from ENA base counts); other routes vary or use local inputs.
    itype = config.input.type
    if itype == "sra":
        if download_gib > 0:
            return f"~{download_gib:.1f} GiB FASTQ (SRA/ENA), plus the reference genome/annotation if not provided"
        return "SRA/ENA FASTQ (size not known until ENA metadata is fetched)"
    if itype == "microarray":
        return "GEO series matrix (size varies by platform)"
    if itype in ("count_matrix", "deseq2_results"):
        return "none (inputs are local)"
    # Local FASTQ route: reads are already on disk; only the reference may download.
    return "none for reads (local FASTQ); the reference genome/annotation downloads if not provided"


def _total_download_bytes(df: pd.DataFrame | None) -> int:
    # Sum ENA's reported gzipped FASTQ byte sizes (download_bytes column, written by
    # metadata_to_samples) across runs. 0 when the column is absent (non-SRA / hand-built sheet),
    # in which case the caller falls back to the per-base size estimate.
    if df is None or "download_bytes" not in df.columns:
        return 0
    return int(pd.to_numeric(df["download_bytes"], errors="coerce").fillna(0).sum())


def _total_gbase(df: pd.DataFrame | None) -> float:
    # Total sequenced gigabases. Prefer the ENA-provided base_count (present even
    # before download); fall back to estimating bases from local compressed FASTQ
    # size (~0.35 bytes/base for gzipped FASTQ including headers and qualities).
    if df is None:
        return 0.0
    if "base_count" in df.columns:
        total = pd.to_numeric(df["base_count"], errors="coerce").fillna(0).sum()
        if total > 0:
            return float(total) / 1e9
    total_bytes = 0
    for col in ("fastq_1", "fastq_2"):
        if col in df.columns:
            for value in df[col].dropna().astype(str):
                if value and Path(value).exists():
                    total_bytes += Path(value).stat().st_size
    if total_bytes > 0:
        return (total_bytes / 0.35) / 1e9
    return 0.0


def _fmt(minutes: float) -> str:
    return str(timedelta(seconds=int(minutes * 60)))
