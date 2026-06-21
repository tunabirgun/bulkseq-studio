from __future__ import annotations

import io
import re
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

# ENA Portal API: resolve run/experiment/study/project accessions to per-run
# metadata + FASTQ URLs. Works from Windows over HTTPS (no WSL needed).
ENA_API = "https://www.ebi.ac.uk/ena/portal/api/filereport"
# GEO accession record (text); used to map a GEO series (GSE) to its SRA study,
# because ENA's filereport does not accept GEO/GSE accessions as search terms.
GEO_ACC_API = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
ENA_FIELDS = (
    "run_accession,experiment_accession,sample_accession,library_layout,"
    "read_count,base_count,fastq_ftp,fastq_md5,sample_title,scientific_name"
)

_GSE_RE = re.compile(r"^GSE\d+$", re.IGNORECASE)
_SRP_RE = re.compile(r"((?:SRP|ERP|DRP)\d{4,})", re.IGNORECASE)


def _parse_study_from_geo_text(text: str) -> str | None:
    """Pull the SRA study (SRP/ERP/DRP) out of a GEO series text record's
    `!Series_relation` lines. The SRA relation is the reliable marker of sequencing
    data; a series with only a BioProject and no SRA relation is a microarray series,
    so this returns None for it (the caller then points the user to the microarray flow)."""
    m = _SRP_RE.search(text)
    return m.group(1).upper() if m else None


def _resolve_gse_to_study(gse: str, timeout: int = 60) -> str | None:
    """Resolve a GEO series (GSE…) to its SRA study / BioProject accession, or None."""
    url = (f"{GEO_ACC_API}?acc={urllib.parse.quote(gse)}"
           f"&targ=self&form=text&view=brief")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None
    return _parse_study_from_geo_text(text)


def fetch_ena_metadata(accessions: list[str], timeout: int = 60) -> pd.DataFrame:
    """Fetch read-run metadata for SRA accessions (SRR/ERR/DRR runs, SRX experiments,
    SRP/PRJ studies). GEO series (GSE…) are auto-resolved to their SRA study first,
    since ENA does not accept GEO accessions directly."""
    frames: list[pd.DataFrame] = []
    for raw in accessions:
        acc = raw.strip()
        if not acc:
            continue
        # GEO series are not valid ENA search terms -> resolve to the SRA study.
        if _GSE_RE.match(acc):
            study = _resolve_gse_to_study(acc, timeout=timeout)
            if not study:
                raise ValueError(
                    f"{acc.upper()} has no linked SRA sequencing data. If it is a "
                    f"microarray series, use 'Fetch a GEO microarray series' instead; "
                    f"otherwise paste the SRA study (SRP…/PRJNA…) or run (SRR…) accessions.")
            acc = study
        url = (
            f"{ENA_API}?accession={urllib.parse.quote(acc)}"
            f"&result=read_run&fields={ENA_FIELDS}&format=tsv"
        )
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                raise ValueError(
                    f"ENA did not recognise the accession '{acc}'. Use an SRA run "
                    f"(SRR/ERR/DRR…), experiment (SRX…), or study (SRP…/PRJNA…), or a "
                    f"GEO series (GSE…). GEO samples (GSM…) are not supported here.") from exc
            raise ValueError(f"ENA query failed for '{acc}': HTTP {exc.code}.") from exc
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
