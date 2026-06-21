from app.core.sra_metadata import _GSE_RE, _parse_study_from_geo_text


def test_parses_sra_study_from_geo_relation():
    text = (
        "^SERIES = GSE78885\n"
        "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA314297\n"
        "!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP071140\n"
    )
    assert _parse_study_from_geo_text(text) == "SRP071140"


def test_microarray_series_has_no_sra_relation():
    # Only a BioProject, no SRA relation -> microarray, returns None.
    text = (
        "^SERIES = GSE5583\n"
        "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA95449\n"
    )
    assert _parse_study_from_geo_text(text) is None


def test_gse_pattern():
    assert _GSE_RE.match("GSE78885")
    assert _GSE_RE.match("gse5583")
    assert not _GSE_RE.match("SRP071140")
    assert not _GSE_RE.match("GSM12345")
