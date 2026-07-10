#!/usr/bin/env python3
"""Dedicated cross-study META-ANALYSIS HTML report (0.21.0). Imports the shared CSS/helpers from
make_html_report.py (no fork) and assembles a self-contained page: a convergence/divergence hero,
the comparative figures (volcano, forest, concordance scatter, convergent heatmap, heterogeneity,
integration gain, cross-study enrichment dotplot), the convergent-gene table, the per-study DE
summary, and the shared-vs-distinct enrichment terms. Degrades to an honest page when the meta
result is empty (confounded / no shared genes)."""
from __future__ import annotations

import argparse
import csv
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_html_report as R  # shared CSS, LOGO_SVG, _fig, _load_json, _badge, section, URLs


def _num(v, digits=1):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _csv_rows(path: Path, limit: int | None = None) -> tuple[list[str], list[list[str]]]:
    if not path.exists():
        return [], []
    with path.open(encoding="utf-8", newline="") as fh:
        rd = list(csv.reader(fh))
    if not rd:
        return [], []
    head, body = rd[0], rd[1:]
    return head, (body[:limit] if limit else body)


def _table(head: list[str], rows: list[list[str]], italic_col: int | None = None,
           num_cols: set[int] | None = None) -> str:
    if not head or not rows:
        return "<p class='muted'>No rows.</p>"
    num_cols = num_cols or set()
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    trs = []
    for r in rows:
        tds = []
        for i, c in enumerate(r):
            cell = html.escape(c)
            if i == italic_col and cell:
                cell = f"<i>{cell}</i>"
            sv = ""
            if i in num_cols:
                try:
                    sv = f" data-sort-value='{float(c)}'"
                except (TypeError, ValueError):
                    sv = ""
            tds.append(f"<td{sv}>{cell}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return (f"<div class='tw'><table class='sortable'><thead><tr>{th}</tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table></div>")


def _hero(summary: dict, meta_status: str) -> str:
    k = summary
    studies = _num(k.get("n_studies"))
    cards = [
        ("Studies combined", studies),
        ("Shared genes", _num(k.get("n_shared_genes"))),
        ("Convergent meta-DEGs", f"{_num(k.get('n_sig_up'))} up · {_num(k.get('n_sig_down'))} down"),
        ("Discordant (flagged)", _num(k.get("n_discordant"))),
        ("Direction concordance", (f"{_num(k.get('direction_concordance_pct'))}%"
                                    if k.get("direction_concordance_pct") is not None else "—")),
        ("Pooling", str(k.get("pooling", "—"))),
    ]
    if k.get("median_I2") is not None:
        cards.append(("Median I² (heterogeneity)", f"{_num(k.get('median_I2'))}%"))
    inner = "".join(
        f"<div class='card'><div class='card-k'>{html.escape(a)}</div>"
        f"<div class='card-v'>{html.escape(str(b))}</div></div>" for a, b in cards)
    n_sig = k.get("n_meta_sig") or 0
    conc = k.get("direction_concordance_pct")
    lead = (f"Across {studies} studies, {_num(n_sig)} genes reach the combined-FDR threshold with a "
            f"consistent direction in every study (convergent), "
            + (f"while {_num(k.get('n_discordant'))} genes are flagged discordant "
               "(significant combination but conflicting per-study signs) and are never called "
               "meta-DEGs. " if k.get("n_discordant") else "")
            + (f"Overall {_num(conc)}% of shared genes agree in direction across studies."
               if conc is not None else ""))
    badge = R._badge(meta_status) if meta_status else ""
    return (f"<section id='findings' class='hero'><div class='eyebrow'>Multi-study meta-analysis {badge}</div>"
            f"<p class='lead'>{html.escape(lead)}</p><div class='cards'>{inner}</div></section>")


def _fig_row(figs: Path, items: list[tuple[str, str]]) -> str:
    blocks = [R._fig(figs, base, title) for base, title in items]
    blocks = [b for b in blocks if b]
    return "".join(blocks)


def _enrichment_table(path: Path, top_per: int = 6) -> str:
    head, rows = _csv_rows(path)
    if not head or not rows:
        return "<p class='muted'>Cross-study enrichment was not available (organism unmapped or no significant terms).</p>"
    idx = {h: i for i, h in enumerate(head)}
    cN, dN, gN, pN = idx.get("Cluster"), idx.get("Description"), idx.get("GeneRatio"), idx.get("p.adjust")
    if cN is None or dN is None:
        return "<p class='muted'>Enrichment table unavailable.</p>"
    # Keep the top terms per cluster to keep the table readable.
    seen: dict[str, int] = {}
    keep = []
    for r in rows:
        c = r[cN]
        if seen.get(c, 0) >= top_per:
            continue
        seen[c] = seen.get(c, 0) + 1
        keep.append([r[cN], r[dN], r[gN] if gN is not None else "",
                     f"{float(r[pN]):.2e}" if pN is not None and r[pN] else ""])
    return _table(["Gene set", "GO term", "GeneRatio", "p.adjust"], keep, num_cols={3})


def build(project: Path) -> str:
    reports = project / "results" / "reports"
    figs = project / "results" / "figures"
    meta = project / "results" / "meta"
    name = project.resolve().name
    run = R._load_json(reports / "run_summary.json")
    summary = R._load_json(reports / "meta_analysis_summary.json")
    meta_check = R._load_json(project / "checks" / "17_meta_analysis_qc.json")
    status = (meta_check.get("messages") or [{}])[0].get("status", "") if meta_check else ""
    app_version = run.get("app_version") or ""
    ver_chip = f"<span class='ver'>v{html.escape(str(app_version))}</span>" if app_version else ""

    empty = not summary or (summary.get("n_shared_genes", 0) or 0) == 0 or (summary.get("n_meta_sig") is None)
    if empty:
        msg = (meta_check.get("messages") or [{}])[0].get("message", "") if meta_check else ""
        body = (f"<section class='hero'><div class='eyebrow'>Multi-study meta-analysis {R._badge(status or 'FAIL')}</div>"
                f"<p class='lead'>The meta-analysis did not produce a shared-gene result.</p>"
                f"<p class='muted'>{html.escape(msg or 'No shared genes across studies, or the studies are confounded with the contrast (no single study contains both arms).')}</p></section>")
    else:
        figures = R.section("Comparative figures", _fig_row(figs, [
            ("meta_volcano", "Meta-volcano — pooled effect vs combined FDR, coloured by cross-study direction"),
            ("meta_forest", "Forest plot — per-study effect + pooled estimate for the top convergent genes"),
            ("meta_concordance_scatter", "Cross-study concordance — per-study log2FC agreement (Spearman ρ)"),
            ("meta_convergent_heatmap", "Convergent genes — per-study signed log2FC"),
            ("meta_enrichment_dotplot", "Cross-study enrichment — shared vs distinct GO terms (compareCluster)"),
            ("meta_heterogeneity", "Heterogeneity — pooled |log2FC| vs I² (random-effects, k≥3)"),
            ("meta_integration_gain", "Integration gain — genes recovered by pooling vs single-study"),
            ("meta_combined_p_hist", "Combined-p diagnostic — inverse-normal p distribution"),
        ]), sid="figures")
        ch, cr = _csv_rows(meta / "meta_convergent_genes.csv", limit=40)
        conv = R.section("Convergent genes (top 40 by combined FDR)",
                         _table(ch, cr, italic_col=1 if ch and ch[1] == "gene_symbol" else None,
                                num_cols={i for i, h in enumerate(ch) if any(t in h for t in
                                          ("log2FC", "padj", "pvalue", "I2", "QEp", "tau2", "n_studies"))}),
                         sid="convergent")
        sh, sr = _csv_rows(meta / "meta_study_summary.csv")
        perstudy = R.section("Per-study differential expression",
                             _table(sh, sr, num_cols={i for i in range(1, len(sh))}), sid="per-study")
        enr = R.section("Cross-study functional enrichment (shared vs distinct)",
                        _enrichment_table(meta / "meta_enrichment_ora.csv"), sid="enrichment")
        body = _hero(summary, status) + figures + conv + perstudy + enr

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BulkSeq Studio meta-analysis — {html.escape(name)}</title><style>{R.CSS}</style></head>
<body>
<header class="top"><div class="brand">{R.LOGO_SVG}
<span class="wordmark">BulkSeq Studio</span>{ver_chip}<span class="tag">meta-analysis</span></div></header>
<main>
{body}
<p class='muted' style='margin-top:2rem'>The joint per-study results, main figures and QC live in the
standard <b>results_report.html</b>. This page focuses on the cross-study comparison.</p>
</main>
<footer><div class="flinks">
<a href="{R.REPO_URL}" target="_blank" rel="noopener">GitHub repository ↗</a>
<a href="{R.DOCS_URL}" target="_blank" rel="noopener">Documentation ↗</a></div>
<p>Generated by BulkSeq Studio{f' v{html.escape(str(app_version))}' if app_version else ''} ·
per-study DESeq2 → metaRNASeq inverse-normal + metafor effect-size pooling. Self-contained; figures embedded.</p>
</footer>
<div id="bsq-lb" class="lb" role="dialog" aria-modal="true" aria-label="Figure viewer" onclick="bsqClose()"><span class="hint">Click image to zoom · click background or press Esc to close</span><img id="bsq-lb-img" alt="" tabindex="-1"></div>
<script>
function bsqZoom(btn){{var img=btn.querySelector('img');var li=document.getElementById('bsq-lb-img');li.className='';li.src=img.src;li.alt=img.alt||'';document.getElementById('bsq-lb').classList.add('open');li.focus();}}
function bsqClose(){{document.getElementById('bsq-lb').classList.remove('open');}}
(function(){{var li=document.getElementById('bsq-lb-img');if(li)li.addEventListener('click',function(e){{this.classList.toggle('zoomed');e.stopPropagation();}});
document.addEventListener('keydown',function(e){{if(e.key==='Escape')bsqClose();}});}})();
(function(){{function cv(c){{if(!c)return'';var v=c.getAttribute('data-sort-value');return v!==null?v:(c.textContent||'');}}
function cmp(a,b){{var x=parseFloat(a),y=parseFloat(b);if(!isNaN(x)&&!isNaN(y))return x-y;return a.localeCompare(b);}}
document.querySelectorAll('table.sortable').forEach(function(t){{var tb=t.tBodies[0];if(!tb)return;var orig=Array.prototype.slice.call(tb.rows);
t.querySelectorAll('thead th').forEach(function(th,ci){{th.tabIndex=0;th.style.cursor='pointer';
th.addEventListener('click',function(){{var st=th.getAttribute('data-sort'),nx=st==='asc'?'desc':(st==='desc'?'none':'asc');
t.querySelectorAll('thead th').forEach(function(o){{o.removeAttribute('data-sort');}});var rows=Array.prototype.slice.call(tb.rows);
if(nx==='none'){{orig.forEach(function(r){{tb.appendChild(r);}});return;}}
rows.sort(function(a,b){{var c=cmp(cv(a.cells[ci]),cv(b.cells[ci]));return nx==='asc'?c:-c;}});
rows.forEach(function(r){{tb.appendChild(r);}});th.setAttribute('data-sort',nx);}});}});}});}})();
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    project = Path(args.project)
    out = Path(args.out) if args.out else project / "results" / "reports" / "meta_analysis_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(project), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
