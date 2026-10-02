from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime


SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "run_enrichment.R"


def test_production_gprofiler_client_uses_https_and_preserves_archive_and_payload(tmp_path):
    runtime = rscript_runtime("gprofiler2")
    if runtime is None:
        if os.environ.get("BULKSEQ_REQUIRE_GPROFILER") == "1":
            pytest.fail("Required Rscript with gprofiler2 is unavailable")
        pytest.skip("Rscript with the installed gprofiler2 client is unavailable")
    command, convert = runtime
    source = SCRIPT.read_text(encoding="utf-8")
    route = source.split("gp_run <- tryCatch({", 1)[1].split("gp <- gp_run$result", 1)[0]
    assert route.index("ensure_gprofiler_https()") < route.index("query_gprofiler(")

    code = f'''
exprs <- parse(file={convert(SCRIPT)!r})
matches <- Filter(function(expr) is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
  identical(as.character(expr[[2]]), "ensure_gprofiler_https"), exprs)
stopifnot(length(matches) == 1L)
eval(matches[[1]], envir = .GlobalEnv)

probe <- function() {{
  ns <- asNamespace("gprofiler2")
  original <- get("gprofiler_request", envir = ns)
  calls <- list()
  unlockBinding("gprofiler_request", ns)
  assign("gprofiler_request", function(url, payload) {{
    calls[[length(calls) + 1L]] <<- list(url = url, payload = payload)
    stop("request intercepted before network")
  }}, envir = ns)
  lockBinding("gprofiler_request", ns)
  original_base <- gprofiler2::get_base_url()
  on.exit({{
    unlockBinding("gprofiler_request", ns)
    assign("gprofiler_request", original, envir = ns)
    lockBinding("gprofiler_request", ns)
    gprofiler2::set_base_url(original_base)
  }}, add = TRUE)

  archive <- "http://biit.cs.ut.ee/gprofiler_archive3/e95_eg42_p13"
  gprofiler2::set_base_url(archive)
  before <- try(gprofiler2::gconvert(c("TP53", "EGFR"), organism = "hsapiens"), silent = TRUE)
  stopifnot(inherits(before, "try-error"), length(calls) == 1L,
            identical(calls[[1]]$url, paste0(archive, "/api/convert/convert")))
  ensure_gprofiler_https()
  after <- try(gprofiler2::gconvert(c("TP53", "EGFR"), organism = "hsapiens"), silent = TRUE)
  stopifnot(inherits(after, "try-error"), length(calls) == 2L,
            identical(gprofiler2::get_base_url(), sub("^http://", "https://", archive)),
            identical(calls[[2]]$url, sub("^http://", "https://", calls[[1]]$url)),
            identical(calls[[2]]$payload, calls[[1]]$payload))
  body <- jsonlite::fromJSON(calls[[2]]$payload)
  stopifnot(identical(body$organism, "hsapiens"),
            identical(body$query, c("TP53", "EGFR")),
            identical(body$target, "ENSG"))
  ensure_gprofiler_https()
  stopifnot(identical(gprofiler2::get_base_url(), sub("^http://", "https://", archive)))
}}
probe()
cat("production g:Profiler HTTPS transport PASS\\n")
'''
    harness = tmp_path / "gprofiler_transport.R"
    harness.write_text(code, encoding="utf-8", newline="\n")
    done = subprocess.run([*command, convert(harness)], capture_output=True, text=True,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "production g:Profiler HTTPS transport PASS" in done.stdout
