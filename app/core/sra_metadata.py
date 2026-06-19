from __future__ import annotations

import io
import urllib.parse
import urllib.request

import pandas as pd

# ENA Portal API: resolve run/experiment/study/project accessions to per-run
# metadata + FASTQ URLs. Works from Windows over HTTPS (no WSL needed).
ENA_API = "https://www.ebi.ac.uk/ena/portal/api/filereport"
ENA_FIELDS = (
    "run_accession,experiment_accession,sample_accession,library_layout,"
    "read_count,base_count,fastq_ftp,fastq_md5,sample_title,scientific_name"
)


def fetch_ena_metadata(accessions: list[str], timeout: int = 60) -> pd.DataFrame:
    """Fetch read-run metadata for SRR/ERR/DRR/SRP/PRJ/GSE-linked accessions."""
    frames: list[pd.DataFrame] = []
    for raw in accessions:
        acc = raw.strip()
        if not acc:
            continue
        url = (
            f"{ENA_API}?accession={urllib.parse.quote(acc)}"
            f"&result=read_run&fields={ENA_FIELDS}&format=tsv"
        )
        with urllib.request.urlopen(url, timeout=timeout) as response:
            text = response.read().decode("utf-8")
        if not text.strip():
            continue
        frame = pd.read_csv(io.StringIO(text), sep="\t", dtype=str).fillna("")
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset="run_accession")


def metadata_to_samples(meta: pd.DataFrame) -> pd.DataFrame:
    """Convert ENA metadata into the app's samples.tsv schema, with recommended
    defaults (condition=unknown for the user to edit, replicate auto-numbered)."""
    rows: list[dict[str, str]] = []
    for _, record in meta.iterrows():
        run = str(record.get("run_accession", ""))
        if not run:
            continue
        layout = "paired" if str(record.get("library_layout", "")).upper() == "PAIRED" else "single"
        urls = [u for u in str(record.get("fastq_ftp", "")).split(";") if u]
        r1_url = next((u for u in urls if u.endswith("_1.fastq.gz")), "")
        r2_url = next((u for u in urls if u.endswith("_2.fastq.gz")), "")
        if layout == "paired" and r1_url and r2_url:
            fastq_1, fastq_2 = f"data/raw/{run}_1.fastq.gz", f"data/raw/{run}_2.fastq.gz"
        else:
            layout = "single"
            r1_url = r1_url or (urls[0] if urls else "")
            r2_url = ""
            fastq_1, fastq_2 = f"data/raw/{run}.fastq.gz", ""
        rows.append(
            {
                "sample_id": run,
                "original_accession": run,
                "experiment_accession": str(record.get("experiment_accession", "")),
                "layout": layout,
                "fastq_1": fastq_1,
                "fastq_2": fastq_2,
                "fastq_1_url": r1_url,
                "fastq_2_url": r2_url,
                "condition": "unknown",
                "replicate": str(len([r for r in rows]) + 1),
                "organism": str(record.get("scientific_name", "")),
                "read_count": str(record.get("read_count", "")),
                "base_count": str(record.get("base_count", "")),
                "sample_title": str(record.get("sample_title", "")),
            }
        )
    return pd.DataFrame(rows)
