from app.core.sra_metadata import _GSE_RE, _parse_study_from_geo_text


def test_parses_sra_study_from_geo_relation():
    text = (
        "^SERIES = GSE78885\n"
        "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA314297\n"
        "!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP071140\n"
    )
    assert _parse_study_from_geo_text(text) == "SRP071140"


def test_prefers_sra_study_over_bioproject():
    # When both are present the explicit SRA study wins over the BioProject.
    text = (
        "^SERIES = GSE78885\n"
        "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA314297\n"
        "!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP071140\n"
    )
    assert _parse_study_from_geo_text(text) == "SRP071140"


def test_falls_back_to_bioproject_when_no_sra_relation():
    # No SRA relation but a BioProject (e.g. GSE280426 via !Series_gp_id) -> return the
    # BioProject; ENA accepts it and returns runs for sequencing series (empty for arrays).
    assert _parse_study_from_geo_text("!Series_gp_id = PRJNA1177667\n") == "PRJNA1177667"
    assert _parse_study_from_geo_text(
        "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA95449\n"
    ) == "PRJNA95449"


def test_no_study_when_neither_present():
    assert _parse_study_from_geo_text("^SERIES = GSE1\n!Series_title = x\n") is None


def test_gse_pattern():
    assert _GSE_RE.match("GSE78885")
    assert _GSE_RE.match("gse5583")
    assert not _GSE_RE.match("SRP071140")
    assert not _GSE_RE.match("GSM12345")
