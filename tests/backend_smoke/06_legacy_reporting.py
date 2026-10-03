from __future__ import annotations

import os
from pathlib import Path


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"])
current = root / "projects/custom_new/workflow/scripts/run_custom_enrichment.R"
target = root / "projects/custom_old/workflow/scripts/run_custom_enrichment.R"
source = current.read_text(encoding="utf-8")
old = target.read_text(encoding="utf-8")
if source != old:
    raise SystemExit("Old/new projects did not start from identical analytic source")
replacements = (
    ('  ora_populations <- custom_ora_populations(eora, length(all_sig) > 0)\n', ''),
    ('    sprintf("Supplied ORA universe: %d (%s)", length(universe), if (nzchar(bg)) "background file" else "tested genes"),\n'
     '    sprintf("Supplied selected genes (ORA input): %d", length(all_sig)),\n'
     '    sprintf("Custom ORA model evidence: %s", ora_populations$model),\n'
     '    sprintf("Effective ORA annotated background: %s", if (is.na(ora_populations$background)) "unavailable" else ora_populations$background),\n'
     '    sprintf("Effective ORA annotated selected genes: %s", if (is.na(ora_populations$selected)) "unavailable" else ora_populations$selected),\n',
     '    sprintf("Universe: %d (%s)", length(universe), if (nzchar(bg)) "background file" else "tested genes"),\n'
     '    sprintf("Significant genes (ORA input): %d", length(all_sig)),\n'),
)
for before, after in replacements:
    if source.count(before) != 1:
        raise SystemExit("Reviewed reporting-only replacement no longer matches once")
    source = source.replace(before, after, 1)
anchor = '  saveRDS(list(eora = eora, egse = egse, n_terms = length(unique(t2g$term))), out[["objects"]])'
if source.split(anchor, 1)[0] != old.split(anchor, 1)[0]:
    raise SystemExit("Analytic source before saved objects changed")
target.write_text(source, encoding="utf-8", newline="\n")
print("Prepared reviewed legacy reporting variant; analytic prefix is identical")
