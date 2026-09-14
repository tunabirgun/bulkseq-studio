"""Identifier-mapping evidence lines lifted from results/enrichment/enrichment_summary.txt.

run_enrichment.R writes the run's mapping, universe, gate and KEGG-resource evidence into that
file. The run summary and the HTML report both quote it, and they must quote the SAME lines: a
prefix present in one and missing from the other means one of the two reports understates what
was excluded. tests/test_workflow_scripts.py re-derives the emitted prefixes from the R source
and fails when a new one is neither covered here nor classified as non-evidence.
"""

from __future__ import annotations

ENRICHMENT_EVIDENCE_PREFIXES = (
    "Eligible ID mapping keytypes:",
    "Identifier routing policy:",
    "Accepted ID mapping routes:",
    "Tested input IDs retained after mapping/exclusion:",
    "Significant input IDs retained after mapping/exclusion:",
    "Up-regulated input IDs retained after mapping/exclusion:",
    "Down-regulated input IDs retained after mapping/exclusion:",
    "Mapped tested-gene universe",
    "GO effective annotated ORA universes:",
    "GO readable-symbol conversion:",
    "DO effective annotated ORA universe:",
    "OrgDb annotation identity:",
    "KEGG identity verification:",
    "KEGG retrieval:",
    "KEGG retrieval date (UTC):",
    "KEGG effective resource universe:",
    "KEGG supported foreground:",
    "KEGG eligible hypotheses/gene sets:",
    "KEGG ranked-list annotation:",
    "KEGG adjusted results:",
    "KEGG ORA status:",
    "KEGG GSEA status:",
    "KEGG resource status:",
    "KEGG key form observed:",
    "Unmapped input IDs excluded:",
    "Ambiguous input IDs excluded:",
    "One-to-many mappings observed:",
    "Cross-keytype discordance observed:",
    "Many-to-one Entrez groups collapsed",
    "Direction-conflict Entrez IDs excluded:",
    "Source IDs present in both up/down inputs:",
    "Foreground intersection (up/down Entrez)",
    "Mapping interpretation gate:",
    "Direction-conflict gate:",
    "GO/DO annotation-resource status:",
    "Universe policy:",
    "ORA parameters:",
    "ORA multiple-testing families:",
    "GSEA parameters:",
    "GSEA ranking order:",
    "GSEA exact-score ties:",
    "GSEA duplicate canonical-ID collapse:",
    "Mapping limitation:",
)


def evidence_lines(summary_text: str) -> list[str]:
    return [line.strip() for line in summary_text.splitlines()
            if line.strip().startswith(ENRICHMENT_EVIDENCE_PREFIXES)]
