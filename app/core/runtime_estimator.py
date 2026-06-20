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


def estimate_runtime(config: AppConfig, metadata: pd.DataFrame | None = None) -> dict[str, object]:
    # Phase model calibrated against a real fungal 6-sample paired-end run (4 cores):
    # download 15 min, alignment 18 min, QC 12 min cumulative. Coefficients are
    # per sequenced gigabase (gbase); thread-parallel phases are divided by an
    # efficiency factor, network-bound download by a weaker one.
    sample_count = len(metadata) if metadata is not None else 0
    gbase = _total_gbase(metadata)
    count_matrix = config.input.type == "count_matrix"
    microarray = config.input.type == "microarray"
    ref_cat = config.reference.genome_size_category
    ref_factor = REFERENCE_FACTORS.get(ref_cat, 1.0)
    threads = max(config.resources.total_threads, 1)
    compute_par = min(threads, 12) ** 0.5     # compute steps scale ~sqrt(threads)
    io_par = min(threads, 4) ** 0.5           # download/IO scale weakly

    overhead = 2.0
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
        download = (gbase * 2.3) / io_par if is_sra else 0.0

        per_gbase = {"Salmon": 1.6, "HISAT2": 2.7}.get(config.workflow.aligner, 3.4)
        align = (gbase * per_gbase * ref_factor) / compute_par
        index = 0.0
        if config.workflow.aligner == "STAR" and not config.reference.star_index:
            index = INDEX_MINUTES.get(ref_cat, 10.0)

        qc = (gbase * 1.8) / compute_par if (config.workflow.fastqc_pre_trim or config.workflow.fastqc_post_trim) else 0.0
        trim = (gbase * 0.6) / compute_par if config.workflow.trimming else 0.0
        rrna = (gbase * 2.0) / compute_par if config.workflow.rrna_filtering else 0.0
        quant = (gbase * 0.1) / compute_par
        downstream = 0.5 + (1.0 if config.workflow.enrichment else 0.0) + (0.3 if config.workflow.figures else 0.0)

        minutes = overhead + download + index + align + qc + trim + rrna + quant + downstream
        # Floor so an estimate with unknown data volume is not unrealistically tiny.
        minutes = max(minutes, 8.0 + sample_count * 1.5)

        if index >= 20:
            bottlenecks.append(f"{ref_cat} STAR index build (~{index:.0f} min)")
        if is_sra and download >= 10:
            bottlenecks.append("SRA/ENA download (network-bound)")
        if gbase >= 40:
            bottlenecks.append("large sequencing volume")
        if config.resources.total_memory_gb < 16 and config.workflow.aligner == "STAR" and ref_factor >= 1.4:
            bottlenecks.append("limited RAM for STAR on a large genome")

    # Estimates target wall-clock and have historically skewed low, so the range
    # leans toward the high side.
    low = max(4.0, minutes * 0.75)
    high = max(low + 5.0, minutes * 1.6)

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
        "memory_gb": config.resources.total_memory_gb,
        "bottlenecks": bottlenecks or (
            ["microarray mode: GEO download + limma, no alignment"] if microarray
            else ["count-matrix mode: alignment skipped"] if count_matrix
            else ["none obvious from current configuration"]
        ),
    }


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
