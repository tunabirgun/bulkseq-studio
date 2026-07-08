from __future__ import annotations

import gzip
import io
import re
import urllib.request

import pandas as pd

# Sample columns that are never candidates for the experimental "condition"; every other
# column (the parsed GEO characteristics plus source_name) is a candidate.
FIXED_SAMPLE_COLS = {
    "sample_id", "gsm_accession", "condition", "layout", "fastq_1", "platform", "organism", "title",
}

# Ordered biological keywords. A candidate column whose name equals, or contains as a whole
# underscore-delimited segment, one of these is preferred as the condition (earliest wins).
CONDITION_PRIORITY = [
    "condition", "treatment", "treated", "agent", "compound", "drug", "stimulus", "stimulation",
    "genotype", "knockout", "knockdown", "mutant", "variant", "disease", "diagnosis", "tumor",
    "infection", "infected", "dose", "concentration", "group", "timepoint", "time",
    "cell_type", "celltype", "tissue", "source_name", "phenotype", "status",
]

# Donor / technical / demographic covariates — never the experimental group, even if they
# split the samples. Matched by whole underscore segment or exact name.
COVARIATE_KEYS = {
    "strain", "background", "gender", "sex", "age", "batch", "replicate", "rep", "donor",
    "subject", "individual", "patient", "cell_line", "cellline", "ercc", "spike", "spikein",
    "rin", "barcode", "library", "run", "passage", "id",
}

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


def _ascii_slug(value: str, cap: int = 48) -> str:
    # ASCII-only, factor-clean group label. Keeps a char only if it is an ASCII alphanumeric,
    # else "_"; collapses/strips underscores, lowercases, caps length. Critically NOT the same
    # as the column _slug() (which keeps any isalnum char, including a Greek delta).
    s = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in str(value))
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s[:cap].strip("_")


def _label_map(values: list[str]) -> dict[str, str]:
    # Map each distinct non-blank raw value to a distinct ASCII label, disambiguating any
    # two values that slug to the same label so the group count is never silently collapsed.
    labels: dict[str, str] = {}
    counts: dict[str, int] = {}
    for raw in values:
        v = (raw or "").strip()
        if not v or v in labels:
            continue
        base = _ascii_slug(v) or "group"
        if base in counts:
            counts[base] += 1
            labels[v] = f"{base}_{counts[base]}"
        else:
            counts[base] = 1
            labels[v] = base
    return labels


def _distinct_nonblank(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _is_covariate(col: str) -> bool:
    segs = col.split("_")
    return col in COVARIATE_KEYS or any(s in COVARIATE_KEYS for s in segs)


def _priority_index(col: str) -> int:
    segs = set(col.split("_"))
    best = len(CONDITION_PRIORITY)
    for i, key in enumerate(CONDITION_PRIORITY):
        if col == key or key in segs:
            best = min(best, i)
    return best


def _conditions_from_titles(titles: list[str], n_samples: int) -> list[str] | None:
    # Last-resort grouping from sample titles: strip a trailing replicate suffix, then group.
    if not all(t and t.strip() for t in titles):
        return None
    rep = re.compile(r"[\s,;_-]*(?:biological[\s_-]*)?(?:replicate|biorep|rep|clone|run|batch|r|s)?[\s_-]?\d+$", re.I)
    labels = [_ascii_slug(rep.sub("", t.strip()).strip(" ,;_-")) for t in titles]
    groups = _distinct_nonblank(labels)
    if 2 <= len(groups) <= n_samples - 1:
        return [lab if lab else "unknown" for lab in labels]
    return None


def _infer_condition(rows: list[dict], n_samples: int) -> tuple[list[str], str]:
    """Suggest a per-sample condition (experimental group) from the parsed metadata.

    Returns (conditions, source_label). Non-destructive: the user still confirms/edits it,
    and any sample left blank stays "unknown" (surfaced by validate_metadata).
    """
    if n_samples < 2:
        return ["unknown"] * n_samples, "unknown"
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in FIXED_SAMPLE_COLS and key not in seen:
                seen.add(key)
                cols.append(key)
    # (priority_index, n_distinct, column_order, col) — sorts to the best candidate.
    candidates: list[tuple[int, int, int, str]] = []
    for order, col in enumerate(cols):
        if _is_covariate(col):
            continue
        vals = [str(row.get(col, "")) for row in rows]
        n_distinct = len(_distinct_nonblank(vals))
        if 2 <= n_distinct <= n_samples - 1:
            candidates.append((_priority_index(col), n_distinct, order, col))
    if candidates:
        candidates.sort()
        chosen = candidates[0][3]
        vals = [str(row.get(chosen, "")).strip() for row in rows]
        labels = _label_map(vals)
        conds = [labels.get(v, "unknown") if v else "unknown" for v in vals]
        return conds, chosen
    titles = [str(row.get("title", "")) for row in rows]
    from_titles = _conditions_from_titles(titles, n_samples)
    if from_titles is not None:
        return from_titles, "title"
    return ["unknown"] * n_samples, "unknown"


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
    # GEO characteristics are "key: value" pairs, but different samples can carry
    # different keys on the same line (heterogeneous submissions). Route each pair
    # to a column named by ITS key so nothing is conflated/lost; samples missing a
    # key simply leave that column blank.
    def _slug(name: str, fallback: str) -> str:
        s = "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()
        return s or fallback
    for idx, vals in enumerate(characteristics):
        fallback = f"characteristic_{idx + 1}"
        for i in range(len(rows)):
            v = vals[i] if i < len(vals) else ""
            if ":" in v:
                key, _, val = v.partition(":")
                rows[i][_slug(key, fallback)] = val.strip()
            elif v:
                rows[i][fallback] = v

    # Suggest a condition from the parsed characteristics/source/title (was hardcoded
    # "unknown"). The user still confirms/edits it in the Metadata tab.
    conditions, condition_source = _infer_condition(rows, len(gsms))
    for i in range(len(rows)):
        rows[i]["condition"] = conditions[i]

    # Heterogeneous GEO submissions give samples different characteristic keys, so some
    # cells are missing; without this they serialize/display as the literal string "nan"
    # and become a spurious factor level if that column is used as a contrast/covariate.
    samples = pd.DataFrame(rows).fillna("")
    return {
        "samples": samples,
        "platform": platform,
        "organism": organisms[0] if organisms else "",
        "series_type": series_type,
        "title": series.get("title", ""),
        "is_microarray": is_microarray,
        "condition_source": condition_source,
    }
