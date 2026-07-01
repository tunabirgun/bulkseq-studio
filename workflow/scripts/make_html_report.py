from __future__ import annotations

# Self-contained HTML results report: inlines the run's figures (base64 PNG), the top
# differential-expression genes, the enrichment summary, provenance/versions, and the sanity
# checks into one shareable file (no external assets). Stdlib only; every section degrades
# gracefully when an artifact is absent (mode-dependent), so it runs in every input mode.

import argparse
import base64
import csv
import html
from pathlib import Path


def _read(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit:
        lines = text.splitlines()
        if len(lines) > limit:
            text = "\n".join(lines[:limit]) + f"\n… ({len(lines) - limit} more lines)"
    return text


def _img(path: Path, title: str) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return ""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    cap = html.escape(title)
    return (f'<figure><img alt="{cap}" src="data:image/png;base64,{data}"/>'
            f'<figcaption>{cap}</figcaption></figure>')


def _de_table(results_csv: Path, top: int = 25) -> str:
    if not results_csv.exists():
        return "<p class='muted'>No differential-expression table.</p>"
    rows: list[dict[str, str]] = []
    with results_csv.open(encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            if i >= top:
                break
            rows.append(row)
    if not rows:
        return "<p class='muted'>The differential-expression table is empty.</p>"
    cols = [c for c in ("gene_id", "symbol", "log2FoldChange", "padj", "baseMean", "biotype")
            if c in rows[0]]

    def fmt(col: str, val: str) -> str:
        if col in ("log2FoldChange", "padj", "baseMean"):
            try:
                f = float(val)
                return f"{f:.3g}"
            except (ValueError, TypeError):
                return html.escape(val or "")
        return html.escape(val or "")

    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{fmt(c, r.get(c, ''))}</td>" for c in cols) + "</tr>"
        for r in rows)
    return f"<table class='de'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def build(project: Path) -> str:
    reports = project / "results" / "reports"
    figs = project / "results" / "figures"
    name = project.resolve().name
    summary = _read(reports / "run_summary.txt")
    versions = _read(reports / "software_versions.txt")
    enrichment = _read(project / "results" / "enrichment" / "enrichment_summary.txt", limit=60)
    sanity = _read(project / "checks" / "sanity_checks.txt", limit=200)

    fig_specs = [
        ("pca.png", "PCA"), ("volcano.png", "Volcano"), ("ma_plot.png", "MA plot"),
        ("top_deg_heatmap.png", "Top-DEG heatmap"), ("sample_distance.png", "Sample distance"),
        ("pvalue_histogram.png", "p-value histogram"),
        ("enrichment_dotplot.png", "GO enrichment"),
        ("enrichment_kegg_dotplot.png", "KEGG enrichment"),
        ("ppi_network.png", "STRING PPI network"),
    ]
    imgs = "".join(_img(figs / f, t) for f, t in fig_specs)
    if not imgs:
        imgs = "<p class='muted'>No figures were produced for this run.</p>"

    de_table = _de_table(project / "results" / "deseq2" / "deseq2_results.csv")

    def section(title: str, body: str, pre: bool = False) -> str:
        if not body.strip():
            return ""
        inner = f"<pre>{html.escape(body)}</pre>" if pre else body
        return f"<section><h2>{html.escape(title)}</h2>{inner}</section>"

    css = """
    body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;
      color:#1f2933;background:#f5f7fa;line-height:1.5}
    header{background:#3b3f8c;color:#fff;padding:22px 32px}
    header h1{margin:0;font-size:22px} header p{margin:4px 0 0;opacity:.85;font-size:13px}
    main{max-width:1100px;margin:0 auto;padding:24px 32px}
    section{background:#fff;border:1px solid #e4e7eb;border-radius:8px;padding:18px 22px;margin:18px 0}
    h2{margin-top:0;font-size:17px;color:#3b3f8c;border-bottom:1px solid #eef;padding-bottom:6px}
    .gallery{display:flex;flex-wrap:wrap;gap:16px}
    figure{margin:0;flex:1 1 320px;max-width:520px} figure img{width:100%;border:1px solid #e4e7eb;border-radius:6px}
    figcaption{font-size:12px;color:#616e7c;margin-top:4px;text-align:center}
    pre{background:#f5f7fa;border:1px solid #e4e7eb;border-radius:6px;padding:12px;overflow:auto;font-size:12px}
    table.de{border-collapse:collapse;width:100%;font-size:13px}
    table.de th,table.de td{border:1px solid #e4e7eb;padding:5px 8px;text-align:left}
    table.de th{background:#eef1fb} .muted{color:#616e7c}
    footer{max-width:1100px;margin:0 auto;padding:12px 32px 32px;color:#9aa5b1;font-size:12px}
    """
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BulkSeq Studio report — {html.escape(name)}</title><style>{css}</style></head>
<body><header><h1>BulkSeq Studio results report</h1>
<p>Project: {html.escape(name)} — self-contained (open in any browser)</p></header>
<main>
{section("Run summary", summary, pre=True)}
<section><h2>Figures</h2><div class="gallery">{imgs}</div></section>
{section("Top differential-expression genes", de_table)}
{section("Functional enrichment", enrichment, pre=True)}
{section("Sanity checks", sanity, pre=True)}
{section("Software versions", versions, pre=True)}
</main>
<footer>Generated by BulkSeq Studio. Figures and tables are embedded; no internet or external
files are needed to view this report.</footer></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    project = Path(args.project)
    out = Path(args.out) if args.out else project / "results" / "reports" / "results_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(project), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
