from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime


HELPER = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "enrichment_mapping.R"


def test_installed_orgdb_keeps_direct_entrez_targets_and_excludes_unknown_ids(tmp_path):
    runtime = rscript_runtime("clusterProfiler", "org.Hs.eg.db")
    if runtime is None:
        if os.environ.get("BULKSEQ_REQUIRE_ORGDB_MAPPING") == "1":
            pytest.fail("Required Rscript with clusterProfiler and org.Hs.eg.db is unavailable")
        pytest.skip("Rscript with clusterProfiler and org.Hs.eg.db is unavailable")
    command, convert = runtime
    code = f'''
suppressPackageStartupMessages(library(org.Hs.eg.db))
source({convert(HELPER)!r})
ids <- c("7157", "1956", "672", "NOT_A_HUMAN_GENE_0340")
resolved <- map_ids_with_routing(ids, org.Hs.eg.db, "ENTREZID", "org.Hs.eg.db")
stopifnot(resolved$mapped_inputs == 3L, resolved$unmapped_inputs == 1L,
          resolved$ambiguous_excluded == 0L,
          identical(resolved$map$input_id, ids[1:3]),
          identical(resolved$map$ENTREZID, ids[1:3]),
          identical(resolved$map$resolution, rep("routed:ENTREZID", 3)),
          identical(resolved$exclusions$input_id, ids[[4]]),
          identical(resolved$exclusions$reason, "unmapped"))
mixed <- map_ids_with_routing(c("7157", "TP53", "NOT_A_HUMAN_GENE_0340"),
                              org.Hs.eg.db, "ENTREZID", "org.Hs.eg.db")
stopifnot(mixed$mapped_inputs == 2L, mixed$unmapped_inputs == 1L,
          identical(mixed$map$ENTREZID, c("7157", "7157")),
          mixed$duplicate_entrez == 1L,
          identical(mixed$exclusions$reason, "unmapped"))
cat("installed OrgDb direct-Entrez and mixed-ID routing PASS\\n")
'''
    harness = tmp_path / "orgdb_entrez.R"
    harness.write_text(code, encoding="utf-8", newline="\n")
    done = subprocess.run([*command, convert(harness)], capture_output=True, text=True,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "installed OrgDb direct-Entrez and mixed-ID routing PASS" in done.stdout
