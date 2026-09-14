"""The ENA importer carries the archive's library_name into the sample sheet."""
from __future__ import annotations

import pandas as pd

from app.core.sra_metadata import ENA_FIELDS, metadata_to_samples

# One run with a submitter library name, one without. SRR1039508 (airway) really does return
# an empty library_name and a populated sample_title, which is why the two must not be mixed.
_META = pd.DataFrame([
    {"run_accession": "SRR1", "library_layout": "SINGLE", "fastq_ftp": "ftp/SRR1.fastq.gz",
     "fastq_md5": "m1", "fastq_bytes": "100", "library_name": "Liver pool A",
     "sample_title": "liver untreated 1", "scientific_name": "Homo sapiens"},
    {"run_accession": "SRR2", "library_layout": "SINGLE", "fastq_ftp": "ftp/SRR2.fastq.gz",
     "fastq_md5": "m2", "fastq_bytes": "100", "library_name": "",
     "sample_title": "liver treated 1", "scientific_name": "Homo sapiens"},
])


def test_ena_request_asks_for_library_name() -> None:
    # Verified against https://www.ebi.ac.uk/ena/portal/api/returnFields?result=read_run:
    # library_name is a valid read_run field, so the request still returns HTTP 200.
    assert "library_name" in ENA_FIELDS.split(",")


def test_library_name_is_carried_verbatim_and_never_filled_from_sample_title() -> None:
    samples = metadata_to_samples(_META)
    assert list(samples.columns)[:2] == ["sample_id", "library_name"]
    assert list(samples["library_name"]) == ["Liver pool A", ""]
    assert list(samples["sample_title"]) == ["liver untreated 1", "liver treated 1"]


def test_a_sheet_with_no_library_names_still_carries_the_column() -> None:
    # save_metadata drops the all-blank column on write; metadata_to_samples itself stays
    # uniform so the interface always sees the same schema from an import.
    blank = _META.assign(library_name=["", ""])
    assert list(metadata_to_samples(blank)["library_name"]) == ["", ""]
