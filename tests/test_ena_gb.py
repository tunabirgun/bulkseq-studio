from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "app" / "data" / "default_config.yaml"

from app.core.sra_metadata import metadata_to_samples
from app.core.runtime_estimator import _total_download_bytes, estimate_runtime
from app.core.config_models import AppConfig


def _ena_record(run, layout, fastq_bytes, base_count):
    suf = "_1.fastq.gz;" + run + "_2.fastq.gz" if layout == "PAIRED" else ".fastq.gz"
    ftp = (f"ftp/{run}_1.fastq.gz;ftp/{run}_2.fastq.gz" if layout == "PAIRED"
           else f"ftp/{run}.fastq.gz")
    return {"run_accession": run, "library_layout": layout, "fastq_ftp": ftp,
            "fastq_md5": "a;b" if layout == "PAIRED" else "a", "fastq_bytes": fastq_bytes,
            "base_count": base_count, "scientific_name": "Homo sapiens", "sample_title": run}


def test_download_bytes_sums_fastq_bytes_for_paired_run():
    meta = pd.DataFrame([_ena_record("SRR1", "PAIRED", "1000000000;1100000000", "438206318")])
    s = metadata_to_samples(meta)
    assert s.loc[0, "download_bytes"] == "2100000000"


def test_download_bytes_falls_back_to_base_count_when_bytes_absent():
    # ENA sometimes omits fastq_bytes; fall back to base_count * 0.35 bytes/base per run.
    meta = pd.DataFrame([_ena_record("SRR2", "PAIRED", "", "1000000000")])
    s = metadata_to_samples(meta)
    assert s.loc[0, "download_bytes"] == str(int(1000000000 * 0.35))


def test_estimator_uses_actual_download_bytes_for_size():
    meta = pd.DataFrame([_ena_record("SRR1", "PAIRED", "1000000000;1100000000", "438206318"),
                         _ena_record("SRR2", "PAIRED", "", "1000000000")])
    s = metadata_to_samples(meta)
    assert _total_download_bytes(s) == 2100000000 + int(1000000000 * 0.35)
    cfg = AppConfig.model_validate(yaml.safe_load(_DEFAULT_CONFIG.read_text(encoding="utf-8")))
    cfg.input.type = "sra"
    est = estimate_runtime(cfg, s, threads=4, memory_gb=16)
    assert "GiB FASTQ" in est["download_size"]  # populated, not "size not known"
    assert "not known" not in est["download_size"]


def test_no_download_bytes_column_is_safe():
    # Hand-built sheet without the column: estimator must not crash, just returns 0 bytes.
    assert _total_download_bytes(pd.DataFrame({"sample_id": ["x"]})) == 0
