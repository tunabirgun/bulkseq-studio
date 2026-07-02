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


def estimate_runtime(
    config: AppConfig,
    metadata: pd.DataFrame | None = None,
    *,
    threads: int | None = None,
    memory_gb: int | None = None,
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
    count_matrix = config.input.type == "count_matrix"
    microarray = config.input.type == "microarray"
    ref_cat = config.reference.genome_size_category
    ref_factor = REFERENCE_FACTORS.get(ref_cat, 1.0)
    threads = max(threads if threads is not None else config.resources.total_threads, 1)
    memory_gb = memory_gb if memory_gb is not None else config.resources.total_memory_gb
    compute_par = min(threads, 12) ** 0.5     # compute steps scale ~sqrt(threads)
    io_par = min(threads, 4) ** 0.5           # download/IO scale weakly

    overhead = 2.0
    download_gib = 0.0
    bottlenecks: list[str] = []

    if microarray:
        # GEO download/ingest + probe collapse + limma -> figures -> enrichment.
        minutes = overhead + 3.0 + sample_count * 0.3
        minutes += 1.0 if config.workflow.enrichment else 0.0
        minutes += 0.4 if config.workflow.figures else 0.0
    elif count_matrix:
        # Alignment, QC, and download are all skipped; only DESeq2 -> figures ->
        # enrichment run, which is fast.
        minutes = overhead + sample_count * 0.2
        minutes += 1.0 if config.workflow.enrichment else 0.0
        minutes += 0.4 if config.workflow.figures else 0.0
    else:
        is_sra = config.input.type == "sra"
        # Download is a weak per-gbase anchor only: real ENA download time is network-bound and
        # spans minutes to hours independent of size (measured ~80x spread across runs), so it is
        # surfaced separately (download_size + download_note) rather than trusted in the estimate.
        download = (gbase * 0.8) / io_par if is_sra else 0.0
        # Estimated FASTQ download size for SRA/ENA: gzipped FASTQ is ~0.35 bytes/base
        # (the same factor _total_gbase uses to invert local file sizes to bases).
        if is_sra and gbase > 0:
            download_gib = (gbase * 1e9 * 0.35) / (1024 ** 3)

        # Per-gbase alignment minutes, recalibrated against real benchmark runs (STAR fit from
        # 3 runs at 4/12 threads; HISAT2/Salmon scaled proportionally, preserving speed order).
        per_gbase = {"Salmon": 0.8, "HISAT2": 1.3}.get(config.workflow.aligner, 1.65)
        # Under-provisioned RAM makes STAR on a large genome swap, slowing alignment.
        # The penalty is 1.0 (no effect) unless RAM is genuinely tight for that case,
        # so adequately resourced runs are unchanged.
        low_ram_star = (
            memory_gb < 16 and config.workflow.aligner == "STAR" and ref_factor >= 1.4
        )
        mem_factor = 1.5 if low_ram_star else 1.0
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

        minutes = overhead + download + index + align + qc + trim + rrna + quant + downstream
        # Floor only when the data volume is unknown (no base_count and no local
        # FASTQ), so the estimate is not unrealistically tiny. When gbase is known
        # the model is trusted, so cores/RAM changes are reflected in the estimate.
        if gbase <= 0:
            minutes = max(minutes, 8.0 + sample_count * 1.5)

        if index >= 20:
            bottlenecks.append(f"{ref_cat} STAR index build (~{index:.0f} min)")
        if is_sra and download >= 10:
            bottlenecks.append("SRA/ENA download (network-bound)")
        if gbase >= 40:
            bottlenecks.append("large sequencing volume")
        if low_ram_star:
            bottlenecks.append("limited RAM for STAR on a large genome")

    # Range calibrated against real runs: low 0.8 keeps the low bound at/above the physical
    # critical-path floor (0.75 fell below it); high 2.5 covers ordinary compute + network jitter.
    # The pathological download tail is not chased here — it is called out in download_note.
    low = max(4.0, minutes * 0.8)
    high = max(low + 5.0, minutes * 2.5)

    return {
        "low_seconds": int(low * 60),
        "high_seconds": int(high * 60),
        "range": f"{_fmt(low)}-{_fmt(high)}",
        "sample_count": sample_count,
        "sequencing_gbase": round(gbase, 2),
        "reference_group": ref_cat,
        "aligner": ("n/a (microarray/limma)" if microarray
                    else "n/a (count matrix)" if count_matrix
                    else config.workflow.aligner),
        "threads": threads,
        "memory_gb": memory_gb,
        "download_size": _download_label(config, download_gib),
        "download_note": (
            "SRA/ENA download time is network-dependent and can range from minutes to hours "
            "regardless of size; it is not fully captured by the estimate above."
            if config.input.type == "sra" else "no read download"
        ),
        "bottlenecks": bottlenecks or (
            ["microarray mode: GEO download + limma, no alignment"] if microarray
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
