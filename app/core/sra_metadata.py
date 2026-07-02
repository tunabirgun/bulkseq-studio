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
_PRJ_RE = re.compile(r"(PRJ[A-Z]{1,2}\d+)", re.IGNORECASE)


def _parse_study_from_geo_text(text: str) -> str | None:
    """Resolve a GEO series text record to an ENA-queryable study accession.

    Prefer an explicit SRA study (SRP/ERP/DRP) from a `!Series_relation` line. If none is
    present, fall back to the BioProject (`!Series_gp_id`/relation, PRJNA/PRJEB/PRJDB) — many
    sequencing GEO series link SRA only via the BioProject and carry no SRA relation, and ENA
    accepts BioProject accessions and returns their read runs. ENA returns no runs for a
    microarray-only BioProject, which the caller reports. Returns None if neither is found."""
    m = _SRP_RE.search(text)
    if m:
        return m.group(1).upper()
    p = _PRJ_RE.search(text)
    return p.group(1).upper() if p else None


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
        # GEO series are not valid ENA search terms -> resolve to the SRA study/BioProject.
        gse_src = ""
        if _GSE_RE.match(acc):
            study = _resolve_gse_to_study(acc, timeout=timeout)
            if not study:
                raise ValueError(
                    f"{acc.upper()} has no linked SRA sequencing data. If it is a "
                    f"microarray series, use 'Fetch a GEO microarray series' instead; "
                    f"otherwise paste the SRA study (SRP…/PRJNA…) or run (SRR…) accessions.")
            gse_src = acc.upper()
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
        frame = (pd.read_csv(io.StringIO(text), sep="\t", dtype=str).fillna("")
                 if text.strip() else pd.DataFrame())
        if frame.empty:
            # ENA returns a header-only response for a study/BioProject with no read runs
            # (e.g. a microarray GEO series resolved via its BioProject).
            if gse_src:
                raise ValueError(
                    f"{gse_src} resolved to {acc}, but ENA has no sequencing runs for it. "
                    f"If {gse_src} is a microarray series, use 'Fetch a GEO microarray series' "
                    f"instead.")
            continue
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset="run_accession")


def _ascii_slug(value: str, cap: int = 40) -> str:
    s = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in str(value))
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s[:cap].strip("_")


_REP_SUFFIX = re.compile(
    r"[\s,;_-]*(?:biological[\s_-]*)?(?:replicate|biorep|rep|clone|run|batch|r|s)?[\s_-]?\d+$", re.I
)


def _suggest_conditions_from_titles(titles: list[str]) -> dict[int, str] | None:
    """Suggest a condition per run from free-form ENA sample_titles — conservatively.

    Strips a trailing replicate suffix, drops a leading token shared by ALL titles (a
    donor/cell-line prefix), then groups by the remaining stem. Returns a mapping ONLY when
    the stems form 2..N-1 real groups each with >=2 runs (a replicated design); otherwise
    None, so free-form/per-individual titles keep condition="unknown". Never a silent guess.
    """
    titles = [str(t).strip() for t in titles]
    n = len(titles)
    if n < 4 or not all(titles):
        return None
    stems = [_REP_SUFFIX.sub("", t).strip(" ,;_-") for t in titles]
    tok_lists = [re.split(r"[\s_,;:-]+", s) for s in stems]
    # Drop a leading token shared by every sample (e.g. donor/cell-line prefix).
    while all(len(tl) > 1 for tl in tok_lists) and len({tl[0].lower() for tl in tok_lists}) == 1:
        tok_lists = [tl[1:] for tl in tok_lists]
    labels = [_ascii_slug(" ".join(tl)) for tl in tok_lists]
    if any(not lab for lab in labels):
        return None
    counts: dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    if not (2 <= len(counts) <= n - 1):
        return None
    if any(c < 2 for c in counts.values()):
        return None
    return {i: labels[i] for i in range(n)}


def metadata_to_samples(meta: pd.DataFrame) -> pd.DataFrame:
    """Convert ENA metadata into the app's samples.tsv schema. Condition is suggested from
    the sample titles when they form a clear replicated design, else left "unknown" for the
    user to edit; replicate is auto-numbered."""
    rows: list[dict[str, str]] = []
    for _, record in meta.iterrows():
        run = str(record.get("run_accession", ""))
        if not run:
            continue
        layout = "paired" if str(record.get("library_layout", "")).upper() == "PAIRED" else "single"
        ftp = [u.strip() for u in str(record.get("fastq_ftp", "")).split(";")]
        md5 = [m.strip() for m in str(record.get("fastq_md5", "")).split(";")]
        # ENA lists fastq_ftp and fastq_md5 in the same order; map each URL to its checksum.
        url_md5 = {u: (md5[i] if i < len(md5) else "") for i, u in enumerate(ftp) if u}
        urls = [u for u in ftp if u]
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
                "fastq_1_md5": url_md5.get(r1_url, ""),
                "fastq_2_md5": url_md5.get(r2_url, ""),
                "condition": "unknown",
                "replicate": str(len([r for r in rows]) + 1),
                "organism": str(record.get("scientific_name", "")),
                "read_count": str(record.get("read_count", "")),
                "base_count": str(record.get("base_count", "")),
                "sample_title": str(record.get("sample_title", "")),
            }
        )
    # Non-destructive condition suggestion from titles (only when they form clear groups).
    suggested = _suggest_conditions_from_titles([r["sample_title"] for r in rows])
    if suggested:
        for i in range(len(rows)):
            rows[i]["condition"] = suggested.get(i, rows[i]["condition"])
    return pd.DataFrame(rows)
