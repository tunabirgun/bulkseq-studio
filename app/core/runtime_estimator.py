from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

from app.core.config_models import AppConfig


REFERENCE_FACTORS = {
    "bacterial": 0.35,
    "yeast": 0.5,
    "fungal": 0.8,
    "plant": 1.4,
    "mammalian": 1.8,
    "custom": 1.0,
}


def estimate_runtime(config: AppConfig, metadata: pd.DataFrame | None = None) -> dict[str, object]:
    sample_count = len(metadata) if metadata is not None else 0
    size_gb = _total_fastq_size_gb(metadata) if metadata is not None else 0.0
    ref_factor = REFERENCE_FACTORS.get(config.reference.genome_size_category, 1.0)
    module_factor = 1.0
    module_factor += 0.15 if config.workflow.trimming else 0
    module_factor += 0.25 if config.workflow.rrna_filtering else 0
    module_factor += 0.2 if config.workflow.enrichment else 0
    module_factor += 0.1 if config.workflow.figures else 0
    if config.workflow.aligner == "Salmon":
        module_factor *= 0.55
    elif config.workflow.aligner == "HISAT2":
        module_factor *= 0.9

    threads = max(config.resources.total_threads, 1)
    base_minutes = 10 + sample_count * 12 + size_gb * 18 * ref_factor
    index_penalty = 35 * ref_factor if not config.reference.star_index else 0
    minutes = (base_minutes + index_penalty) * module_factor / min(threads, 12) ** 0.45
    low = max(5, minutes * 0.75)
    high = max(low + 5, minutes * 1.35)
    bottlenecks = []
    if not config.reference.star_index and config.workflow.aligner == "STAR":
        bottlenecks.append("STAR index build")
    if size_gb > 20:
        bottlenecks.append("large compressed FASTQ input")
    if config.resources.total_memory_gb < 16 and config.workflow.aligner == "STAR":
        bottlenecks.append("limited RAM for STAR")
    return {
        "low_seconds": int(low * 60),
        "high_seconds": int(high * 60),
        "range": f"{_fmt(low)}-{_fmt(high)}",
        "sample_count": sample_count,
        "input_size_gb": round(size_gb, 2),
        "reference_group": config.reference.genome_size_category,
        "aligner": config.workflow.aligner,
        "threads": threads,
        "memory_gb": config.resources.total_memory_gb,
        "bottlenecks": bottlenecks or ["none obvious from current configuration"],
    }


def _total_fastq_size_gb(df: pd.DataFrame) -> float:
    total = 0
    for col in ("fastq_1", "fastq_2"):
        if col in df.columns:
            for value in df[col].dropna().astype(str):
                if value and Path(value).exists():
                    total += Path(value).stat().st_size
    return total / (1024**3)


def _fmt(minutes: float) -> str:
    return str(timedelta(seconds=int(minutes * 60)))
