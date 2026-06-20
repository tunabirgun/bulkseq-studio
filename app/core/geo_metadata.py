from __future__ import annotations

import gzip
import io
import urllib.request

import pandas as pd

# GEO series-matrix header fetch (Windows-side, HTTPS — mirrors sra_metadata.py).
# The full normalization/DE happens later in WSL (ingest_geo.R + run_limma.R);
# this only builds samples.tsv and detects microarray-vs-sequencing up front.
GEO_FTP = "https://ftp.ncbi.nlm.nih.gov/geo/series"


class GeoFetchError(RuntimeError):
    pass


def _series_matrix_url(gse: str) -> str:
    gse = gse.strip().upper()
    if not gse.startswith("GSE") or not gse[3:].isdigit():
        raise GeoFetchError(f"'{gse}' is not a GSE accession (expected e.g. GSE5583).")
    digits = gse[3:]
    stub = "GSEnnn" if len(digits) <= 3 else f"GSE{digits[:-3]}nnn"
    return f"{GEO_FTP}/{stub}/{gse}/matrix/{gse}_series_matrix.txt.gz"


def _strip(value: str) -> str:
    return value.strip().strip('"').strip()


def fetch_geo_series(gse: str, timeout: int = 120) -> dict[str, object]:
    """Fetch and parse a GSE series-matrix header.

    Returns a dict with: samples (DataFrame in the app's samples.tsv schema),
    platform (GPL id), organism, series_type, title, is_microarray (bool).
    Raises GeoFetchError on a missing/multi-platform series or a network failure.
    """
    url = _series_matrix_url(gse)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GeoFetchError(
                f"No single-platform series matrix found for {gse}. Multi-platform GSEs "
                "are not supported in this version; pick a single-platform series."
            ) from exc
        raise GeoFetchError(f"Could not fetch {gse} from GEO (HTTP {exc.code}).") from exc
    except OSError as exc:
        raise GeoFetchError(f"Could not reach GEO for {gse}: {exc}") from exc

    text = gzip.decompress(raw).decode("utf-8", errors="replace")
    series: dict[str, str] = {}
    sample_rows: dict[str, list[str]] = {}
    characteristics: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("!series_matrix_table_begin"):
            break
        if not line.startswith("!"):
            continue
        parts = line.split("\t")
        key = parts[0].strip().lstrip("!")
        values = [_strip(v) for v in parts[1:]]
        if key.startswith("Sample_"):
            field = key[len("Sample_"):]
            if field == "characteristics_ch1":
                characteristics.append(values)
            else:
                # First occurrence wins for repeated single-value sample fields.
                sample_rows.setdefault(field, values)
        elif key.startswith("Series_"):
            series.setdefault(key[len("Series_"):], " ".join(values).strip())

    gsms = sample_rows.get("geo_accession", [])
    if not gsms:
        raise GeoFetchError(f"Could not parse sample accessions from the {gse} series matrix.")

    series_type = series.get("type", "")
    is_microarray = "array" in series_type.lower()
    platform = series.get("platform_id", "")
    titles = sample_rows.get("title", [""] * len(gsms))
    organisms = sample_rows.get("organism_ch1", [""] * len(gsms))
    sources = sample_rows.get("source_name_ch1", [""] * len(gsms))

    rows: list[dict[str, str]] = []
    for i, gsm in enumerate(gsms):
        rows.append({
            "sample_id": gsm,
            "gsm_accession": gsm,
            "condition": "unknown",
            "layout": "n/a",
            "fastq_1": "",
            "platform": platform,
            "organism": organisms[i] if i < len(organisms) else "",
            "title": titles[i] if i < len(titles) else "",
            "source_name": sources[i] if i < len(sources) else "",
        })
    # Each !Sample_characteristics_ch1 line becomes a column; "key: value" pairs
    # are split so a column is named by its characteristic (e.g. genotype).
    for idx, vals in enumerate(characteristics):
        keys = {v.split(":", 1)[0].strip().lower() for v in vals if ":" in v}
        col = next(iter(keys)) if len(keys) == 1 else f"characteristic_{idx + 1}"
        col = "".join(c if c.isalnum() else "_" for c in col).strip("_") or f"characteristic_{idx + 1}"
        for i in range(len(rows)):
            v = vals[i] if i < len(vals) else ""
            rows[i][col] = v.split(":", 1)[1].strip() if ":" in v else v

    samples = pd.DataFrame(rows)
    return {
        "samples": samples,
        "platform": platform,
        "organism": organisms[0] if organisms else "",
        "series_type": series_type,
        "title": series.get("title", ""),
        "is_microarray": is_microarray,
    }
