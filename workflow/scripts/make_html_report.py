from __future__ import annotations

# Self-contained, branded HTML results report. Inlines the run's figures (base64 PNG),
# the top differential-expression genes, functional enrichment, per-step runtimes, the
# sanity checks (as status badges), and provenance/versions into one shareable file.
# No external assets or network are needed to view it: the logo is inlined SVG and the
# font stack falls back to system fonts. Stdlib only; every section degrades gracefully
# when an artifact is absent (mode-dependent), so it runs in every input mode.

import argparse
import base64
import csv
import html
import json
import re
from pathlib import Path

REPO_URL = "https://github.com/tunabirgun/bulkseq-studio"
RELEASES_URL = "https://github.com/tunabirgun/bulkseq-studio/releases/latest"
AUTHOR_URL = "https://github.com/tunabirgun"
DOCS_URL = "https://tunabirgun.github.io/bulkseq-studio/"

# Inline logo (viewBox only; CSS sizes it). Keeps the report offline-safe.
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000" fill="none" class="logo" role="img" aria-label="BulkSeq Studio logo">
<defs>
<linearGradient id="bsBar" x1="150" y1="0" x2="850" y2="0" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#0B65B1"/><stop offset="0.5" stop-color="#22D1C5"/><stop offset="1" stop-color="#0B65B1"/></linearGradient>
<linearGradient id="bsTeal" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#18B8C3"/><stop offset="1" stop-color="#20D2C6"/></linearGradient>
<linearGradient id="bsBlue" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0C3E8F"/><stop offset="1" stop-color="#08327B"/></linearGradient>
</defs>
<g>
<rect x="170" y="455" width="40" height="110" rx="18" fill="#0B65B1"/><rect x="235" y="390" width="40" height="175" rx="18" fill="#0D78B9"/><rect x="300" y="315" width="40" height="250" rx="18" fill="#1091C3"/><rect x="365" y="230" width="40" height="335" rx="18" fill="#15AAC8"/><rect x="430" y="145" width="40" height="420" rx="18" fill="#19BFC7"/><rect x="495" y="110" width="40" height="455" rx="18" fill="#22D1C5"/><rect x="560" y="150" width="40" height="415" rx="18" fill="#17B9D7"/><rect x="625" y="230" width="40" height="335" rx="18" fill="#16A7DB"/><rect x="690" y="315" width="40" height="250" rx="18" fill="#1394D6"/><rect x="755" y="385" width="40" height="180" rx="18" fill="#1080CA"/><rect x="820" y="455" width="40" height="110" rx="18" fill="#0B65B1"/>
</g>
<g>
<rect x="90" y="615" width="820" height="14" rx="7" fill="url(#bsBlue)"/><rect x="170" y="590" width="130" height="64" rx="18" fill="url(#bsBlue)"/><path d="M355 590 H540 L575 622 L540 654 H355 Q335 654 335 634 V610 Q335 590 355 590 Z" fill="url(#bsBlue)"/><rect x="605" y="590" width="105" height="64" rx="18" fill="url(#bsBlue)"/><path d="M770 590 H865 L895 622 L865 654 H770 Q750 654 750 634 V610 Q750 590 770 590 Z" fill="url(#bsBlue)"/>
</g>
<g>
<rect x="190" y="705" width="110" height="18" rx="9" fill="url(#bsBlue)"/><rect x="335" y="705" width="120" height="18" rx="9" fill="url(#bsBlue)"/><rect x="490" y="705" width="130" height="18" rx="9" fill="url(#bsTeal)"/><rect x="655" y="705" width="110" height="18" rx="9" fill="url(#bsBlue)"/><rect x="790" y="705" width="100" height="18" rx="9" fill="url(#bsBlue)"/>
<rect x="250" y="770" width="210" height="20" rx="10" fill="url(#bsBlue)"/><rect x="515" y="770" width="160" height="20" rx="10" fill="url(#bsBlue)"/><rect x="705" y="770" width="140" height="20" rx="10" fill="url(#bsTeal)"/>
<rect x="315" y="835" width="42" height="20" rx="10" fill="url(#bsBlue)"/><rect x="390" y="835" width="120" height="20" rx="10" fill="url(#bsBlue)"/><rect x="545" y="835" width="190" height="20" rx="10" fill="url(#bsTeal)"/>
<rect x="390" y="900" width="170" height="20" rx="10" fill="url(#bsBlue)"/><rect x="595" y="900" width="110" height="20" rx="10" fill="url(#bsTeal)"/>
<rect x="470" y="965" width="140" height="20" rx="10" fill="url(#bsBlue)"/>
</g>
</svg>"""


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


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


SVG_MAX_BYTES = 2_000_000  # embed SVG (lossless zoom) unless it balloons; then PNG


def _fig(figs: Path, basename: str, title: str) -> str:
    # Prefer the vector SVG (crisp at any zoom); fall back to PNG when the SVG is
    # absent or huge (dense point clouds — MA/volcano/dispersion — balloon as SVG
    # for no visible gain). Each figure is a self-contained data-URI so the report
    # needs no external files; embedding per <img> isolates SVG id namespaces.
    svg, png = figs / f"{basename}.svg", figs / f"{basename}.png"
    src = ""
    if svg.exists() and 0 < svg.stat().st_size <= SVG_MAX_BYTES:
        src = "data:image/svg+xml;base64," + base64.b64encode(svg.read_bytes()).decode("ascii")
    elif png.exists() and png.stat().st_size:
        src = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode("ascii")
    if not src:
        return ""
    cap = html.escape(title)
    return (f'<figure><button class="figbtn" type="button" onclick="bsqZoom(this)" '
            f'aria-label="Open {cap} full size"><img alt="{cap}" src="{src}"/></button>'
            f'<figcaption>{cap}</figcaption></figure>')


def _count_csv_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            return max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    except OSError:
        return None


def _de_table(results_csv: Path, top: int = 25, empty_msg: str = "No differential-expression table.") -> str:
    if not results_csv.exists():
        return f"<p class='muted small'>{html.escape(empty_msg)}</p>"
    rows: list[dict[str, str]] = []
    with results_csv.open(encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            if i >= top:
                break
            rows.append(row)
    if not rows:
        return f"<p class='muted small'>{html.escape(empty_msg)}</p>"
    cols = [c for c in ("gene_id", "symbol", "log2FoldChange", "padj", "baseMean", "biotype")
            if c in rows[0]]

    def fmt(col: str, val: str) -> str:
        if col in ("log2FoldChange", "padj", "baseMean"):
            try:
                return f"{float(val):.3g}"
            except (ValueError, TypeError):
                return html.escape(val or "")
        return html.escape(val or "")

    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{fmt(c, r.get(c, ''))}</td>" for c in cols) + "</tr>"
        for r in rows)
    return (f"<div class='tablewrap'><table class='data'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def _de_split(project: Path, top: int = 15) -> str:
    # Up- and down-regulated genes shown separately, from the canonical DEG sets
    # (run_deseq2.R: padj-significant, raw |log2FC| >= threshold), each ordered by
    # fold change. Empty when a direction has no genes or DE has not run.
    deseq = project / "results" / "deseq2"
    up = _de_table(deseq / "upregulated_genes.csv", top,
                   empty_msg="No up-regulated genes passed the significance and fold-change thresholds.")
    down = _de_table(deseq / "downregulated_genes.csv", top,
                     empty_msg="No down-regulated genes passed the significance and fold-change thresholds.")
    if not (deseq / "upregulated_genes.csv").exists() and not (deseq / "downregulated_genes.csv").exists():
        return ""
    return (f"<div class='de-split'>"
            f"<div><h3>Top up-regulated <span class='pill up'>▲ up</span></h3>{up}</div>"
            f"<div><h3>Top down-regulated <span class='pill down'>▼ down</span></h3>{down}</div>"
            f"</div><p class='muted small'>Top {top} genes per direction, ordered by fold change. "
            f"Full lists: <code>results/deseq2/upregulated_genes.csv</code> and "
            f"<code>downregulated_genes.csv</code>.</p>")


_ORA_COLS = [("Description", "Description", "desc"), ("Fold enrichment", "FoldEnrichment", "g3"),
             ("Genes", "Count", "int"), ("p.adjust", "p.adjust", "g2")]
_GSEA_COLS = [("Description", "Description", "desc"), ("NES", "NES", "g3"),
              ("p.adjust", "p.adjust", "g2"), ("Set size", "setSize", "int")]


def _enrich_rows(csv_path: Path, top: int) -> list[dict]:
    if not csv_path.exists():
        return []
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8", errors="replace", newline="") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            if i >= top:
                break
            rows.append(r)
    return rows


def _enrich_block(title: str, csv_path: Path, mode: str, top: int = 10) -> str:
    rows = _enrich_rows(csv_path, top)
    if not rows:
        return ""
    spec = [c for c in (_GSEA_COLS if mode == "gsea" else _ORA_COLS) if c[1] in rows[0]]

    def fmt(kind: str, val: str) -> str:
        if kind == "desc":
            return f"<td class='desc'>{html.escape(val or '')}</td>"
        try:
            f = float(val)
        except (ValueError, TypeError):
            return f"<td class='num'>{html.escape(val or '')}</td>"
        txt = f"{int(round(f))}" if kind == "int" else (f"{f:.2g}" if kind == "g2" else f"{f:.3g}")
        return f"<td class='num'>{txt}</td>"

    head = "".join(f"<th class='{'desc' if k == 'desc' else 'num'}'>{html.escape(h)}</th>"
                   for h, _, k in spec)
    body = "".join("<tr>" + "".join(fmt(k, r.get(key, "")) for _, key, k in spec) + "</tr>"
                   for r in rows)
    return (f"<div class='enr-block'><h3>{html.escape(title)}</h3>"
            f"<div class='tablewrap'><table class='data enr'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div></div>")


def _enrichment_section(project: Path) -> str:
    enr = project / "results" / "enrichment"
    if not enr.exists():
        return ""
    blocks = ""
    # GO over-representation: combined when present, else the up / down splits.
    if _enrich_rows(enr / "go_ora_all.csv", 1):
        blocks += _enrich_block("GO terms — over-representation", enr / "go_ora_all.csv", "ora")
    else:
        blocks += _enrich_block("GO terms — over-represented among up-regulated genes", enr / "go_ora_up.csv", "ora")
        blocks += _enrich_block("GO terms — over-represented among down-regulated genes", enr / "go_ora_down.csv", "ora")
    blocks += _enrich_block("GO gene-set enrichment (GSEA)", enr / "gsea.csv", "gsea")
    blocks += _enrich_block("KEGG pathways — over-representation", enr / "kegg_ora.csv", "ora")
    blocks += _enrich_block("KEGG pathways — gene-set enrichment (GSEA)", enr / "kegg_gsea.csv", "gsea")
    if not blocks:
        if not (enr / "enrichment_summary.txt").exists():
            return ""
        return ("<section id='enrichment'><h2>Functional enrichment</h2>"
                "<p class='muted'>No GO or KEGG terms passed the significance threshold for this run.</p>"
                "</section>")
    return (f"<section id='enrichment'><h2>Functional enrichment</h2>{blocks}"
            f"<p class='muted small'>Top terms by adjusted p-value. Full tables (all terms, "
            f"gene members, GSEA leading edges): <code>results/enrichment/</code>.</p></section>")


def _badge(status: str) -> str:
    s = (status or "").upper()
    cls = {"PASS": "ok", "WARNING": "warn", "FAIL": "fail",
           "REVIEW_REQUIRED": "review"}.get(s, "muted")
    label = "REVIEW" if s == "REVIEW_REQUIRED" else s or "—"
    return f"<span class='badge {cls}'>{html.escape(label)}</span>"


def _parse_sanity(text: str) -> tuple[str, list[dict]]:
    # Parse the aggregate sanity_checks.txt into (overall, [{name, status, messages}]).
    overall = ""
    checks: list[dict] = []
    current: dict | None = None
    head_re = re.compile(r"^([0-9A-Za-z_]+):\s+(PASS|WARNING|FAIL|REVIEW_REQUIRED)\s*$")
    for raw in text.splitlines():
        line = raw.rstrip()
        m_overall = re.match(r"^Overall:\s+(\w+)", line)
        if m_overall:
            overall = m_overall.group(1)
            continue
        m = head_re.match(line.strip())
        if m:
            current = {"name": m.group(1), "status": m.group(2), "messages": []}
            checks.append(current)
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current is not None:
            msg = stripped[2:].strip()
            msg = re.sub(r"^(PASS|WARNING|FAIL|REVIEW_REQUIRED):\s*", "", msg)
            current["messages"].append(msg)
    return overall, checks


def _sanity_section(text: str) -> str:
    if not text.strip():
        return ""
    overall, checks = _parse_sanity(text)
    if not checks:
        return f"<section id='sanity'><h2>Sanity checks</h2><pre>{html.escape(text)}</pre></section>"
    rows = ""
    for c in checks:
        msgs = "".join(f"<li>{html.escape(m)}</li>" for m in c["messages"])
        pretty = c["name"].replace("_", " ")
        rows += (f"<tr><td class='chk-status'>{_badge(c['status'])}</td>"
                 f"<td><div class='chk-name'>{html.escape(pretty)}</div>"
                 f"<ul class='chk-msgs'>{msgs}</ul></td></tr>")
    note = ("<p class='muted small'>A <b>WARNING</b> is advisory — the run completed and the "
            "outputs are usable; it flags something to keep in mind (for example a small-replicate "
            "diagnostic). Only a <b>FAIL</b> blocks a run.</p>")
    return (f"<section id='sanity'><div class='sec-head'><h2>Sanity checks</h2>"
            f"{_badge(overall) if overall else ''}</div>{note}"
            f"<table class='checks'>{rows}</table></section>")


def _timing_section(t: dict) -> str:
    if not t:
        return ""
    wall = t.get("wall_clock_approx_hms")
    cumulative = t.get("cumulative_job_hms")
    conf = t.get("configured_resources", {}) or {}
    det = t.get("detected_resources", {}) or {}
    per_phase_s = t.get("per_phase_seconds", {}) or {}
    per_phase_h = t.get("per_phase_hms", {}) or {}
    per_step = t.get("per_step_seconds", {}) or {}

    facts = []
    if wall:
        facts.append(("Wall-clock (approx.)", wall))
    if cumulative:
        facts.append(("Cumulative job time", cumulative))
    if conf.get("snakemake_cores") is not None:
        facts.append(("Cores", str(conf.get("snakemake_cores"))))
    if conf.get("memory_gb") is not None:
        facts.append(("Memory (GB)", str(conf.get("memory_gb"))))
    if det.get("logical_threads") is not None:
        facts.append(("Host threads", str(det.get("logical_threads"))))
    fact_html = "".join(
        f"<div class='stat'><div class='stat-v'>{html.escape(v)}</div>"
        f"<div class='stat-k'>{html.escape(k)}</div></div>" for k, v in facts)

    bars = ""
    if per_phase_s:
        top = max(per_phase_s.values()) or 1
        for phase, secs in per_phase_s.items():
            if secs <= 0:
                continue
            pct = max(round(100 * secs / top), 1)
            label = per_phase_h.get(phase, f"{secs:.0f}s")
            bars += (f"<div class='barrow'><div class='barlab'>{html.escape(phase)}</div>"
                     f"<div class='bartrack'><div class='barfill' style='width:{pct}%'></div></div>"
                     f"<div class='barval'>{html.escape(label)}</div></div>")
    bars_html = f"<div class='bars'>{bars}</div>" if bars else ""

    steps_html = ""
    if per_step:
        rows = "".join(
            f"<tr><td>{html.escape(step)}</td><td class='num'>{secs:g}</td></tr>"
            for step, secs in per_step.items())
        steps_html = (f"<details class='steps'><summary>Per-step wall-clock "
                      f"({len(per_step)} steps)</summary>"
                      f"<div class='tablewrap'><table class='data'><thead><tr><th>Step</th>"
                      f"<th class='num'>Seconds</th></tr></thead><tbody>{rows}</tbody></table></div>"
                      f"<p class='muted small'>Wall-clock per Snakemake rule, from the run's "
                      f"<code>benchmarks/*.tsv</code>. Steps run in parallel, so the phase and "
                      f"per-step times sum to more than the overall wall-clock.</p></details>")

    if not (fact_html or bars_html or steps_html):
        return ""
    return (f"<section id='runtime'><h2>Runtime</h2>"
            f"<div class='stats'>{fact_html}</div>"
            f"<h3>Time by phase</h3>{bars_html}{steps_html}</section>")


def _versions_table(run: dict) -> str:
    sw = run.get("software_versions", {}) or {}
    rp = run.get("r_packages", {}) or {}
    if not sw and not rp:
        return ""

    def rows(d: dict) -> str:
        return "".join(
            f"<tr><td>{html.escape(str(k))}</td><td class='mono'>{html.escape(str(v))}</td></tr>"
            for k, v in d.items())

    blocks = ""
    if sw:
        blocks += ("<div class='vcol'><h3>Tools</h3><div class='tablewrap'>"
                   f"<table class='data'><tbody>{rows(sw)}</tbody></table></div></div>")
    if rp:
        blocks += ("<div class='vcol'><h3>R / Bioconductor</h3><div class='tablewrap'>"
                   f"<table class='data'><tbody>{rows(rp)}</tbody></table></div></div>")
    lock = run.get("environment_lock_md5")
    commit = run.get("workflow_git_commit")
    prov = []
    if lock:
        prov.append(f"Environment lock md5 <code>{html.escape(str(lock))}</code>")
    if commit:
        prov.append(f"Workflow commit <code>{html.escape(str(commit)[:12])}</code>")
    prov_html = f"<p class='muted small'>{' · '.join(prov)}</p>" if prov else ""
    return (f"<section id='versions'><h2>Software &amp; provenance</h2>"
            f"<div class='vgrid'>{blocks}</div>{prov_html}</section>")


def _meta_cards(run: dict, project: Path) -> str:
    ref = run.get("reference", {}) or {}
    de = run.get("deseq2", {}) or {}
    wf = run.get("workflow", {}) or {}
    timing = _load_json(project / "results" / "reports" / "timing_summary.json")

    cards: list[tuple[str, str]] = []

    organism = ref.get("organism_name")
    if organism:
        strain = ref.get("strain")
        cards.append(("Organism", organism + (f" ({strain})" if strain and strain != "None" else "")))

    contrasts = de.get("contrasts") or []
    if contrasts and isinstance(contrasts, list):
        cards.append(("Contrast", str(contrasts[0].get("name", "—"))))
    if de.get("design_formula"):
        cards.append(("Design", str(de.get("design_formula"))))

    de_engine = wf.get("de_engine") or ("limma" if run.get("input", {}).get("type") == "microarray" else "DESeq2")
    alpha = de.get("alpha")
    cards.append(("DE method", f"{de_engine}" + (f" · α={alpha}" if alpha is not None else "")))

    aligner = wf.get("aligner")
    quant = wf.get("quantifier")
    if aligner or quant:
        cards.append(("Aligner · quantifier", " · ".join(x for x in (aligner, quant) if x)))

    up = _count_csv_rows(project / "results" / "deseq2" / "upregulated_genes.csv")
    down = _count_csv_rows(project / "results" / "deseq2" / "downregulated_genes.csv")
    if up is not None or down is not None:
        cards.append(("Differential genes", f"{up or 0} up · {down or 0} down"))

    wall = timing.get("wall_clock_approx_hms")
    if wall:
        cards.append(("Wall-clock", wall))

    di = run.get("download_integrity") or {}
    if di.get("total"):
        extra = f" (+{di['no_checksum']} unverified)" if di.get("no_checksum") else ""
        cards.append(("Data integrity", f"{di['verified']}/{di['total']} FASTQ verified · ENA MD5{extra}"))

    if not cards:
        return ""
    inner = "".join(
        f"<div class='card'><div class='card-k'>{html.escape(k)}</div>"
        f"<div class='card-v'>{html.escape(str(v))}</div></div>" for k, v in cards)
    return f"<div class='cards'>{inner}</div>"


CSS = """
:root{
  --bg:#f7f7fb; --surface:#ffffff; --text:#14151b; --muted:#585b6b;
  --border:#e6e6ef; --border-strong:#d6d6e3; --accent:#4338ca; --accent-2:#3730a3;
  --accent-tint:#ecebfb; --code-bg:#15162a; --code-text:#e8e8f5;
  --ok:#0f7a53; --ok-bg:#e6f5ee; --warn:#8a5a00; --warn-bg:#fbf1de;
  --fail:#b42318; --fail-bg:#fdecea; --review:#1d4ed8; --review-bg:#e7edfd;
  --serif:"EB Garamond",Georgia,"Times New Roman",serif;
  --sans:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--text);background:var(--bg);line-height:1.6;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline;text-underline-offset:2px}
h1,h2,h3{font-family:var(--serif);font-weight:600;letter-spacing:-.01em}
header.top{background:var(--surface);border-bottom:1px solid var(--border);
  padding:18px clamp(16px,5vw,40px)}
.brand{display:flex;align-items:center;gap:12px;max-width:1080px;margin:0 auto}
.brand .logo{width:38px;height:38px;flex:0 0 38px;filter:drop-shadow(0 1px 2px rgba(17,24,39,.12))}
.brand .wordmark{font-family:var(--sans);font-weight:600;font-size:1.15rem;letter-spacing:-.01em}
.brand .ver{font-family:var(--mono);font-size:.66rem;color:var(--accent-2);background:var(--accent-tint);
  padding:.14rem .42rem;border-radius:5px;font-weight:500}
.brand .spacer{flex:1 1 auto}
.brand .rmeta{font-family:var(--sans);text-align:right;font-size:.8rem;color:var(--muted);line-height:1.35}
.brand .rmeta b{color:var(--text);font-weight:600}
main{max-width:1080px;margin:0 auto;padding:8px clamp(16px,5vw,40px) 8px}
.hero{padding:26px 0 6px}
.hero .kicker{font-family:var(--sans);text-transform:uppercase;letter-spacing:.12em;font-size:.7rem;
  font-weight:600;color:var(--accent)}
.hero h1{font-size:clamp(1.9rem,4vw,2.5rem);line-height:1.1;margin:.35rem 0 .2rem}
.hero p{margin:.1rem 0 0;color:var(--muted);font-family:var(--sans)}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));margin:22px 0 6px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;
  box-shadow:0 1px 2px rgba(17,24,39,.04)}
.card-k{font-family:var(--sans);font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.card-v{font-family:var(--sans);font-weight:600;font-size:1.02rem;margin-top:3px;word-break:break-word}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:20px 24px;margin:18px 0;box-shadow:0 1px 2px rgba(17,24,39,.04)}
section h2{font-size:1.4rem;margin:0 0 .7rem;padding-bottom:.4rem;border-bottom:1px solid var(--border)}
section h3{font-family:var(--sans);font-size:.95rem;font-weight:600;color:var(--text);margin:1.2rem 0 .5rem}
.sec-head{display:flex;align-items:center;justify-content:space-between;gap:12px;
  border-bottom:1px solid var(--border);margin-bottom:.7rem}
.sec-head h2{border:none;margin-bottom:0}
.gallery{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
figure{margin:0} figure img{width:100%;border:1px solid var(--border);border-radius:8px;background:#fff;display:block}
figcaption{font-family:var(--sans);font-size:.78rem;color:var(--muted);margin-top:6px;text-align:center}
.figbtn{border:0;background:none;padding:0;margin:0;width:100%;display:block;cursor:zoom-in}
.figbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:8px}
.lb{display:none;position:fixed;inset:0;background:rgba(12,13,26,.86);z-index:9999;padding:28px;overflow:auto;cursor:zoom-out}
.lb.open{display:flex;align-items:center;justify-content:center}
.lb img{max-width:100%;max-height:100%;background:#fff;border-radius:10px;box-shadow:0 12px 48px rgba(0,0,0,.5);cursor:zoom-in}
.lb img.zoomed{max-width:none;max-height:none;width:170%;cursor:zoom-out}
.lb .hint{position:fixed;top:14px;left:0;right:0;text-align:center;color:#e8e8f5;font-family:var(--sans);font-size:.78rem;opacity:.85;pointer-events:none}
.enr-block{margin:0 0 1.3rem}
.enr-block h3{margin:.2rem 0 .5rem}
table.enr th.desc,table.enr td.desc{white-space:normal;min-width:190px;max-width:460px;text-align:left;font-family:var(--sans)}
.de-split{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.de-split h3{display:flex;align-items:center;gap:8px;margin-top:0}
.pill{font-family:var(--sans);font-size:.66rem;font-weight:700;padding:.1rem .45rem;border-radius:999px}
/* up=red, down=blue — echoes the volcano/figure direction palette (#C0392B / #2C7BB6). */
.pill.up{color:#a5281b;background:#f8e6e2} .pill.down{color:#1f6091;background:#e4eef7}
.tablewrap{overflow-x:auto;border:1px solid var(--border);border-radius:8px}
table.data{border-collapse:collapse;width:100%;font-family:var(--sans);font-size:.83rem}
table.data th,table.data td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
table.data thead th{background:var(--accent-tint);color:var(--accent-2);font-weight:600;position:sticky;top:0}
table.data tbody tr:last-child td{border-bottom:none}
table.data td.num,table.data th.num{text-align:right;font-variant-numeric:tabular-nums}
td.mono,.mono{font-family:var(--mono);font-size:.8rem;white-space:normal;overflow-wrap:anywhere}
pre{background:var(--code-bg);color:var(--code-text);border-radius:10px;padding:14px 16px;overflow:auto;
  font-family:var(--mono);font-size:.78rem;line-height:1.55}
code{font-family:var(--mono);font-size:.85em;background:#eef1fb;color:var(--accent-2);
  padding:.1em .36em;border-radius:5px}
.muted{color:var(--muted)} .small{font-size:.8rem}
.badge{font-family:var(--sans);font-size:.7rem;font-weight:700;letter-spacing:.04em;padding:.18rem .5rem;
  border-radius:999px;display:inline-block;white-space:nowrap}
.badge.ok{color:var(--ok);background:var(--ok-bg)} .badge.warn{color:var(--warn);background:var(--warn-bg)}
.badge.fail{color:var(--fail);background:var(--fail-bg)} .badge.review{color:var(--review);background:var(--review-bg)}
.badge.muted{color:var(--muted);background:#eef0f5}
table.checks{border-collapse:collapse;width:100%}
table.checks td{padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:top}
table.checks td.chk-status{width:96px} .chk-name{font-family:var(--sans);font-weight:600;font-size:.9rem}
.chk-msgs{margin:.3rem 0 0;padding-left:1.1rem;font-family:var(--sans);font-size:.83rem;color:var(--muted)}
.chk-msgs li{margin:.15rem 0}
.stats{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
.stat{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px 14px;text-align:center}
.stat-v{font-family:var(--sans);font-weight:700;font-size:1.25rem}
.stat-k{font-family:var(--sans);font-size:.72rem;color:var(--muted);margin-top:2px}
.bars{display:flex;flex-direction:column;gap:8px;margin-top:.5rem}
.barrow{display:grid;grid-template-columns:150px 1fr 78px;align-items:center;gap:10px}
.barrow>*{min-width:0}
.barlab{font-family:var(--sans);font-size:.82rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bartrack{background:#eef0f5;border-radius:6px;height:16px;overflow:hidden}
.barfill{background:linear-gradient(90deg,var(--accent),#22D1C5);height:100%;border-radius:6px}
.barval{font-family:var(--mono);font-size:.76rem;color:var(--muted);text-align:right}
details.steps{margin-top:1rem} details.steps summary{cursor:pointer;font-family:var(--sans);
  font-weight:600;font-size:.88rem;color:var(--accent-2);padding:.3rem 0}
.vgrid{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
footer{max-width:1080px;margin:8px auto 0;padding:22px clamp(16px,5vw,40px) 40px;
  font-family:var(--sans);color:var(--muted);font-size:.82rem;border-top:1px solid var(--border)}
footer .flinks{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:8px}
@media(max-width:560px){.barrow{grid-template-columns:100px 1fr 64px}.brand .rmeta{display:none}}
"""


def section(title: str, body: str, pre: bool = False, sid: str = "") -> str:
    if not body.strip():
        return ""
    inner = f"<pre>{html.escape(body)}</pre>" if pre else body
    ida = f" id='{sid}'" if sid else ""
    return f"<section{ida}><h2>{html.escape(title)}</h2>{inner}</section>"


def build(project: Path) -> str:
    reports = project / "results" / "reports"
    figs = project / "results" / "figures"
    name = project.resolve().name

    run = _load_json(reports / "run_summary.json")
    timing = _load_json(reports / "timing_summary.json")
    sanity = _read(project / "checks" / "sanity_checks.txt", limit=200)

    app_version = run.get("app_version") or ""
    run_date = run.get("run_date") or ""
    ver_chip = f"<span class='ver'>v{html.escape(str(app_version))}</span>" if app_version else ""
    rmeta = ""
    if name or run_date:
        rmeta = (f"<div class='rmeta'><b>{html.escape(name)}</b>"
                 + (f"<br>{html.escape(run_date.replace('T', ' '))}" if run_date else "")
                 + "</div>")

    fig_specs = [
        ("pca", "PCA"), ("volcano", "Volcano"), ("ma_plot", "MA plot"),
        ("top_deg_heatmap", "Top DEGs — by significance"),
        ("top_upregulated_heatmap", "Top up-regulated genes"),
        ("top_downregulated_heatmap", "Top down-regulated genes"),
        ("sample_distance", "Sample distance"),
        ("pvalue_histogram", "p-value histogram"),
        ("enrichment_dotplot", "GO enrichment"),
        ("enrichment_kegg_dotplot", "KEGG enrichment"),
        ("ppi_network", "STRING PPI network"),
    ]
    imgs = "".join(_fig(figs, b, t) for b, t in fig_specs)
    if not imgs:
        imgs = "<p class='muted'>No figures were produced for this run.</p>"

    de_split = _de_split(project)
    meta_cards = _meta_cards(run, project)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BulkSeq Studio report — {html.escape(name)}</title><style>{CSS}</style></head>
<body>
<header class="top"><div class="brand">{LOGO_SVG}
<span class="wordmark">BulkSeq Studio</span>{ver_chip}<span class="spacer"></span>{rmeta}</div></header>
<main>
<div class="hero"><div class="kicker">Results report</div>
<h1>{html.escape(name)}</h1>
<p>Bulk RNA-seq / microarray analysis — self-contained (figures and data embedded; open in any browser).</p></div>
{meta_cards}
<section id="figures"><h2>Figures</h2>
<p class="muted small">Click any figure to open it full size and zoom — figures embed as vector SVG where practical, so they stay sharp at any magnification.</p>
<div class="gallery">{imgs}</div></section>
{section("Differential genes by direction", de_split, sid="de")}
{_enrichment_section(project)}
{_timing_section(timing)}
{_sanity_section(sanity)}
{_versions_table(run)}
</main>
<footer>
<div class="flinks">
<a href="{REPO_URL}" target="_blank" rel="noopener">GitHub repository ↗</a>
<a href="{RELEASES_URL}" target="_blank" rel="noopener">Latest release ↗</a>
<a href="{DOCS_URL}" target="_blank" rel="noopener">Documentation ↗</a>
<a href="{AUTHOR_URL}" target="_blank" rel="noopener">@tunabirgun ↗</a>
</div>
<p>Generated by BulkSeq Studio{f' v{html.escape(str(app_version))}' if app_version else ''} ·
free and open-source under the MIT License. This report is fully self-contained —
figures, tables and the logo are embedded, so no internet or external files are needed to view it.</p>
</footer>
<div id="bsq-lb" class="lb" onclick="bsqClose()"><span class="hint">Click image to zoom · click background or press Esc to close</span><img id="bsq-lb-img" alt=""></div>
<script>
function bsqZoom(btn){{var img=btn.querySelector('img');var li=document.getElementById('bsq-lb-img');li.className='';li.src=img.src;document.getElementById('bsq-lb').classList.add('open');}}
function bsqClose(){{document.getElementById('bsq-lb').classList.remove('open');}}
(function(){{var li=document.getElementById('bsq-lb-img');
li.addEventListener('click',function(e){{this.classList.toggle('zoomed');e.stopPropagation();}});
document.addEventListener('keydown',function(e){{if(e.key==='Escape')bsqClose();}});}})();
</script>
</body></html>"""


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
