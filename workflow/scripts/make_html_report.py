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


# Rejects brace-mangled symbols (e.g. "Transpac{}1439") from the plain-language headline;
# parses the |log2FC| cut-off out of the 09 sanity line when the JSON lacks the key.
_CLEAN_SYM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LFC_RE = re.compile(r"\|log2FC\|\s*>=\s*([0-9]+(?:\.[0-9]+)?)")


# One source of truth for every metric definition — feeds inline .term tooltips AND the §10 glossary.
GLOSS = {
 "padj":     "Adjusted p-value (FDR, Benjamini–Hochberg). The chance a gene looks changed just by luck after testing thousands at once. Smaller is stronger; below the run's cutoff is significant.",
 "pvalue":   "Raw p-value, before correcting for testing thousands of genes. Use padj — not this — to decide significance.",
 "log2fc":   "Log2 fold change: how much and which way a gene shifted. +1 = doubled, −1 = halved, +2 = four-fold. The sign is the direction; positive = higher in the treated group.",
 "basemean": "Average normalised read count across all samples — how strongly the gene is expressed overall. Very low values make fold changes noisy.",
 "biotype":  "Gene category from the annotation (protein_coding, lncRNA, transposable_element, …) — context, not a result.",
 "alpha":    "The FDR cutoff for calling a gene significant (here the run's α).",
 "pca":      "Principal-component plot. Each dot is a sample; dots close together are alike. A clean run separates the conditions and keeps replicates together.",
 "distance": "Sample-to-sample similarity as a colour grid — darker = more alike. Replicates of one group form blocks along the diagonal.",
 "pvalhist": "Spread of raw p-values across all genes. A tall spike near zero on an otherwise flat background = real signal; a hump in the middle warns of a modelling problem.",
 "volcano":  "Each dot is a gene: left–right = fold change (how much it moved), up = confidence. Coloured dots in the top corners are the large, trustworthy hits.",
 "ma":       "Fold change (y) against overall expression (x). Confirms changes aren't driven only by low-count genes.",
 "heatmap":  "Rows are genes, columns are samples; colour is relative expression (warm high, cool low). Samples of one group should look alike.",
 "nes":      "Normalised enrichment score (GSEA). Sign = whether the whole set trends up (+) or down (−); magnitude = strength. Uses every gene, not just the significant list.",
 "foldenr":  "Fold enrichment (ORA). How many more of your changed genes fall in this category than chance predicts.",
 "padjust":  "Adjusted p-value for the term — the expected false-alarm share if you trust it.",
 "setsize":  "How many measured genes belong to that set or category.",
 "ppi":      "Known protein–protein interactions among your changed genes (STRING). Tight clusters suggest genes acting together; highly connected nodes are hubs.",
 "wilcoxon": "A rank-based cross-check, not used to call genes. With few replicates it is underpowered — read it only as a rank-concordance check.",
}

# Terms shown in the §10 glossary, in reading order (label, GLOSS key).
GLOSS_ORDER = [
 ("Adjusted p-value (FDR / padj)", "padj"), ("Raw p-value", "pvalue"),
 ("log2 fold change", "log2fc"), ("baseMean", "basemean"), ("Biotype", "biotype"),
 ("α (alpha)", "alpha"), ("PCA", "pca"), ("Sample distance", "distance"),
 ("p-value histogram", "pvalhist"), ("Volcano plot", "volcano"), ("MA plot", "ma"),
 ("Heatmap", "heatmap"), ("NES", "nes"), ("Fold enrichment", "foldenr"),
 ("p.adjust (term)", "padjust"), ("Set size", "setsize"),
 ("Protein–protein interaction (PPI)", "ppi"), ("Wilcoxon cross-check", "wilcoxon"),
]

# basename -> (group, letter-title, cap-lead plain, cap-tech, howto text).
FIG = {
 "pca":                       ("quality", "Principal-component analysis", "Do the samples group the way the design expects?", "Principal components of variance-stabilised counts; axis labels give the % variance each explains.", "PCA compresses all genes into two axes so whole samples compare at a glance. Replicates of one group should cluster; the two conditions should sit apart. A replicate among the wrong group flags a swap or outlier."),
 "sample_distance":           ("quality", "Sample-to-sample distance", "Which samples resemble each other?", "Euclidean distance on variance-stabilised counts, hierarchically clustered.", "Darker cells are more alike. Replicates of one group form blocks along the diagonal; an off-diagonal dark cell points to a mislabelled or outlier sample."),
 "pvalue_histogram":          ("quality", "p-value histogram (diagnostic)", "Is there real signal, and is the model well-behaved?", "Distribution of raw p-values across all tested genes.", "A tall spike near zero on an otherwise flat background means real differences are present. A hump in the middle, or a spike at one, warns that the statistical model may not fit."),
 "volcano":                   ("de", "Volcano plot", "Which genes changed, and how confidently?", "x: log2 fold change; y: −log10 adjusted p-value. Dashed guides mark the significance and fold-change cut-offs.", "Every dot is a gene. Left–right is how much it changed; up is statistical confidence. The top corners hold large, reliable changes — the headline hits. Height is confidence, not effect size."),
 "ma_plot":                   ("de", "MA plot", "Are the changes independent of expression level?", "x: mean normalised counts (log); y: shrunken log2 fold change. Coloured points are significant.", "Fold change is plotted against overall expression. A healthy result shows significant genes across the whole expression range, not only among the lowest-count genes on the left."),
 "top_deg_heatmap":           ("de", "Top differentially-expressed genes", "The strongest genes, sample by sample.", "Z-scored variance-stabilised counts for the top genes by significance.", "Rows are genes, columns are samples; warm = high, cool = low relative to the row mean. Samples of one condition should share a colour pattern, and the two conditions should look different."),
 "top_upregulated_heatmap":   ("de", "Top up-regulated genes", "The strongest increases across samples.", "Z-scored variance-stabilised counts, top up-regulated by significance.", "The most confidently increased genes. Warm cells should concentrate in the treated group; a gene warm in both groups is worth a second look."),
 "top_downregulated_heatmap": ("de", "Top down-regulated genes", "The strongest decreases across samples.", "Z-scored variance-stabilised counts, top down-regulated by significance.", "The most confidently decreased genes. Cool cells should concentrate in the treated group; a gene cool in both groups is worth a second look."),
 "enrichment_dotplot":        ("function", "GO enrichment", "Which biological themes are over-represented?", "Dot size: gene count; colour: adjusted p-value; position: fold enrichment.", "Each dot is a biological category enriched among the changed genes. Bigger, further-right, darker dots are the stronger, more reliable themes."),
 "enrichment_kegg_dotplot":   ("function", "KEGG pathway enrichment", "Which pathways are over-represented?", "Dot size: gene count; colour: adjusted p-value.", "Each dot is a KEGG pathway over-represented among the changed genes. Bigger and darker dots are the stronger, more reliable pathways."),
 "ppi_network":               ("function", "STRING protein–protein interaction network", "Which changed proteins are known to interact?", "STRING edges above the confidence cut-off; nodes are seed genes, clusters are modules.", "Nodes are your changed genes; edges are known interactions from STRING. Tight clusters suggest proteins acting together, and highly connected hubs are candidates worth prioritising."),
}
FIG_GROUPS = [
 ("quality",  "Quality &amp; sample structure", "Do replicates group together, and do the conditions separate? These panels answer that before any gene is called."),
 ("de",       "Differential expression",   "Which genes changed, by how much, and how confidently."),
 ("function", "Function &amp; interactions",   "What biology the changed genes point to, and how they connect."),
]


def _term(key: str, label: str) -> str:
    # Inline glossable term — focusable <button> (never title=); the same GLOSS entry
    # is restated in the §10 glossary so nothing load-bearing lives only on hover.
    tip = html.escape(GLOSS.get(key, ""))
    return (f'<button class="term" type="button">{label}'
            f'<span class="tip" role="tooltip">{tip}</span></button>')


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


def _fig_src(figs: Path, basename: str) -> str:
    # Prefer the vector SVG (crisp at any zoom); fall back to PNG when the SVG is
    # absent or huge (dense point clouds — MA/volcano/dispersion — balloon as SVG
    # for no visible gain). Each figure is a self-contained data-URI so the report
    # needs no external files; embedding per <img> isolates SVG id namespaces.
    svg, png = figs / f"{basename}.svg", figs / f"{basename}.png"
    if svg.exists() and 0 < svg.stat().st_size <= SVG_MAX_BYTES:
        return "data:image/svg+xml;base64," + base64.b64encode(svg.read_bytes()).decode("ascii")
    if png.exists() and png.stat().st_size:
        return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode("ascii")
    return ""


def _fig(figs: Path, basename: str, title: str) -> str:
    src = _fig_src(figs, basename)
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
        # Gene symbols italic (HGNC convention), matching the report's prose (_fmt_genes).
        # <i> wraps the escaped text only, so the sort JS still reads the plain symbol.
        if col == "symbol" and (val or "").strip():
            return f"<i>{html.escape(val)}</i>"
        return html.escape(val or "")

    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{fmt(c, r.get(c, ''))}</td>" for c in cols) + "</tr>"
        for r in rows)
    # `sortable`: click a header to sort (numeric columns sort numerically). See the
    # embedded script in the page template.
    return (f"<div class='tablewrap'><table class='data sortable'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def _de_split(project: Path, top: int = 50) -> str:
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


def _org_clean(ref: dict):
    # Organism name, or None when unset/placeholder — keeps "in unset" out of the prose.
    o = (ref.get("organism_name") or "").strip()
    return o if o and o.lower() not in ("unset", "none", "na") else None


def _contrast_pair(run: dict):
    # (numerator, denominator) from the first contrast; BYO mode splits the name on _vs_.
    de = run.get("deseq2", {}) or {}
    contrasts = de.get("contrasts")
    c = contrasts[0] if isinstance(contrasts, list) and contrasts else {}
    num, den = c.get("numerator"), c.get("denominator")
    if not (num and den) and isinstance(c.get("name"), str) and "_vs_" in c["name"]:
        num, den = c["name"].split("_vs_", 1)
    return num, den


def _engine_name(run: dict) -> str:
    # Microarray always runs limma (the 09 sanity line confirms it); else the configured engine.
    if (run.get("input", {}) or {}).get("type") == "microarray":
        return "limma"
    return (run.get("workflow", {}) or {}).get("de_engine") or "DESeq2"


def _lfc_threshold(run: dict, sanity_text: str):
    # Prefer the JSON key; else parse the 09 sanity line; else None so the fold clause is omitted.
    t = (run.get("deseq2", {}) or {}).get("lfc_threshold")
    if t is not None:
        try:
            return float(t)
        except (TypeError, ValueError):
            pass
    m = _LFC_RE.search(sanity_text or "")
    return float(m.group(1)) if m else None


def _fold_phrase(t) -> str:
    # |log2FC| cutoff -> plain words; empty string when the cutoff is unknown.
    if not t:
        return ""
    fold = 2 ** float(t)
    if abs(fold - round(fold)) < 1e-6:
        words = {2: "two", 3: "three", 4: "four"}.get(int(round(fold)))
        return f"at least {words}-fold" if words else f"at least {int(round(fold))}-fold"
    return f"at least {fold:.1f}-fold"


def _fmt_genes(names: list[str]) -> str:
    # names already html-escaped.
    if not names:
        return ""
    if len(names) == 1:
        return f"<i>{names[0]}</i>"
    return ", ".join(f"<i>{g}</i>" for g in names[:-1]) + f" and <i>{names[-1]}</i>"


def res_has_de(project: Path) -> bool:
    d = project / "results" / "deseq2"
    return any((d / f).exists() for f in
               ("deseq2_results.csv", "upregulated_genes.csv", "downregulated_genes.csv"))


def _de_headline_stats(project: Path, alpha: float, lfc_t):
    # ONE source of truth so the headline can never contradict the DE tables:
    #  up/down = canonical thresholded CSV row counts (match _de_split); fall back to
    #  thresholding results.csv when those CSVs are absent (BYO mode). tested = non-NaN
    #  padj rows. top genes = padj-ascending, sign-split, brace-mangled symbols skipped.
    deseq = project / "results" / "deseq2"
    res = deseq / "deseq2_results.csv"
    up_csv = _count_csv_rows(deseq / "upregulated_genes.csv")
    down_csv = _count_csv_rows(deseq / "downregulated_genes.csv")
    tested = up_thr = down_thr = 0
    top_up: list[str] = []
    top_down: list[str] = []
    if res.exists():
        with res.open(encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    pj = float(r["padj"])
                except (KeyError, ValueError, TypeError):
                    continue
                if pj != pj:                       # NaN padj -> not tested
                    continue
                tested += 1
                try:
                    lfc = float(r["log2FoldChange"])
                except (KeyError, ValueError, TypeError):
                    lfc = 0.0
                if pj < alpha and (lfc_t is None or abs(lfc) >= lfc_t):
                    if lfc >= 0:
                        up_thr += 1
                    else:
                        down_thr += 1
                if len(top_up) < 3 or len(top_down) < 3:
                    sym = (r.get("symbol") or "").strip()
                    if sym and not _CLEAN_SYM.match(sym):
                        continue
                    name = sym or (r.get("gene_id") or "").strip()
                    if not name:
                        continue
                    bucket = top_up if lfc >= 0 else top_down
                    esc = html.escape(name)
                    if len(bucket) < 3 and esc not in bucket:
                        bucket.append(esc)
    up = up_csv if up_csv is not None else up_thr
    down = down_csv if down_csv is not None else down_thr
    return up, down, tested, top_up, top_down


def _key_finding(run: dict, project: Path, sanity_text: str) -> str:
    de = run.get("deseq2", {}) or {}
    ref = run.get("reference", {}) or {}
    inp = run.get("input", {}) or {}
    num, den = _contrast_pair(run)
    organism = _org_clean(ref)
    alpha = de.get("alpha", 0.05)
    lfc_t = _lfc_threshold(run, sanity_text)
    unit = "probes" if inp.get("type") == "microarray" else "genes"
    up, down, tested, top_up, top_down = _de_headline_stats(project, float(alpha), lfc_t)
    total = up + down

    if num and den:
        lead = f"Comparing <b>{html.escape(str(num))}</b> against <b>{html.escape(str(den))}</b>"
        if organism:
            lead += f" in <i>{html.escape(str(organism))}</i>"
    else:
        lead = "In this comparison"
    thr = f"FDR &lt; {alpha}"
    fold = _fold_phrase(lfc_t)
    if fold:
        thr += f" and {fold}"

    if not res_has_de(project):
        return f"{lead}, differential-expression results are not available for this run."
    if total == 0:
        return (f"{lead}, <b>no</b> {unit} changed at the chosen thresholds ({thr}). "
                f"See the volcano and p-value histogram for why.")
    of_m = f" of {tested:,}" if tested else ""
    s = (f"{lead}, <b>{total:,}</b>{of_m} {unit} changed significantly ({thr}): "
         f"<b class='up'>{up:,}</b> were higher and <b class='down'>{down:,}</b> lower.")
    if top_up:
        s += f" The strongest increases were {_fmt_genes(top_up)}."
    if top_down:
        s += f" The strongest decreases were {_fmt_genes(top_down)}."
    return s


def _status_sentence(sanity_text: str) -> str:
    overall, checks = _parse_sanity(sanity_text)
    if not checks:
        return ""
    n = len(checks)
    non = [c["name"].replace("_", " ") for c in checks if c["status"] != "PASS"]
    if not non:
        return f"All {n} automated quality checks passed."
    has_fail = any(c["status"] == "FAIL" for c in checks)
    word = ("issue" if has_fail else "advisory note") + ("s" if len(non) != 1 else "")
    tail = "" if has_fail else " The run completed and the results are usable."
    return (f"{n - len(non)} of {n} checks passed; {len(non)} {word}: "
            f"{', '.join(html.escape(x) for x in non)}.{tail}")


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
    plain = ('<div class="plain"><span class="tag">In plain terms</span>'
             '<p class="finding" style="font-size:1rem">These are the biological themes and pathways '
             'over-represented among the changed genes — they point to <i>what</i> the changes affect. '
             'Read each as a hypothesis to check, not a settled conclusion.</p></div>')
    return (f"<section id='enrichment'><h2>Functional enrichment</h2>{plain}{blocks}"
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

    # Machine the run executed on — recorded for reproducibility.
    machine = []
    if det.get("cpu_model"):
        cores = det.get("physical_cores")
        threads = det.get("logical_threads")
        cpu = str(det["cpu_model"])
        if cores or threads:
            cpu += f" ({cores or '?'} cores / {threads or '?'} threads)"
        machine.append(("CPU", cpu))
    if det.get("total_ram_gb") is not None:
        machine.append(("Total RAM", f"{det['total_ram_gb']} GB"))
    if det.get("os"):
        machine.append(("OS", str(det["os"])))
    if det.get("hostname"):
        machine.append(("Host", str(det["hostname"])))
    machine_html = ""
    if machine:
        rows = "".join(
            f"<tr><td>{html.escape(k)}</td><td class='mono'>{html.escape(v)}</td></tr>"
            for k, v in machine)
        machine_html = (f"<h3>Machine</h3><div class='tablewrap'><table class='data'>"
                        f"<tbody>{rows}</tbody></table></div>"
                        f"<p class='muted small'>Specs of the machine (WSL2 / native Linux) "
                        f"this run executed on, recorded for reproducibility.</p>")

    if not (fact_html or bars_html or steps_html or machine_html):
        return ""
    return (f"<section id='runtime'><h2>Runtime</h2>"
            f"<div class='stats'>{fact_html}</div>"
            f"{machine_html}"
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

    vhead = "<thead><tr><th>Name</th><th>Version</th></tr></thead>"
    blocks = ""
    if sw:
        blocks += ("<div class='vcol'><h3>Tools</h3><div class='tablewrap'>"
                   f"<table class='data'>{vhead}<tbody>{rows(sw)}</tbody></table></div></div>")
    if rp:
        blocks += ("<div class='vcol'><h3>R / Bioconductor</h3><div class='tablewrap'>"
                   f"<table class='data'>{vhead}<tbody>{rows(rp)}</tbody></table></div></div>")
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

    micro = run.get("microarray", {}) or {}
    is_micro = (run.get("input", {}) or {}).get("type") == "microarray"
    cards: list[tuple[str, str]] = []

    organism = _org_clean(ref)
    if organism:
        strain = ref.get("strain")
        cards.append(("Organism", organism + (f" ({strain})" if strain and strain != "None" else "")))

    # Contrast: numerator vs denominator when known (matches the headline), else the name.
    num, den = _contrast_pair(run)
    if num and den:
        cards.append(("Contrast", f"{num} vs {den}"))
    else:
        contrasts = de.get("contrasts") or []
        if contrasts and isinstance(contrasts, list):
            cards.append(("Contrast", str(contrasts[0].get("name", "—"))))
    if de.get("design_formula"):
        cards.append(("Design", str(de.get("design_formula"))))

    de_engine = _engine_name(run)
    alpha = de.get("alpha")
    lfc = de.get("lfc_threshold")
    thresholds = ""
    if alpha is not None:
        thresholds += f" · α={alpha}"
    if lfc is not None:
        thresholds += f" · |log2FC|≥{lfc}"
    cards.append(("DE method", f"{de_engine}{thresholds}"))

    # Microarray runs have no aligner/quantifier; surface the GEO platform + series instead.
    if is_micro:
        platform = micro.get("platform")
        gse = micro.get("gse_accession")
        if platform:
            cards.append(("Platform", str(platform)))
        if gse:
            cards.append(("GEO series", str(gse)))
    else:
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
  /* Brand — from the logo spectrum bar */
  --brand-blue:#0B65B1; --brand-blue-deep:#08327B;
  --brand-teal:#12A5B0;        /* AA teal: safe for plain-language TEXT/eyebrows */
  --brand-teal-bright:#22D1C5; /* decorative fills/bars/logo ONLY — fails text contrast */
  --spectrum:linear-gradient(90deg,#0B65B1 0%,#22D1C5 50%,#0B65B1 100%);
  /* Aliases so existing rules rebrand without edits */
  --accent:#0B65B1; --accent-2:#08327B; --accent-tint:#eaf3fb;
  /* Paper & ink */
  --bg:#f6f8fa; --surface:#ffffff; --text:#14151b; --muted:#585b6b;
  --border:#e6e9ef; --border-strong:#d6d9e3;
  /* Plain-language track (teal) */
  --plain-bg:#e7f6f5; --plain-border:#12A5B0; --plain-ink:#0a6e73;
  /* Direction — matches the volcano/heatmap palette (ColorBrewer RdBu, CVD-safe). */
  --up:#C0392B; --up-ink:#8e2a20; --up-bg:#fbeae7;
  --down:#2C7BB6; --down-ink:#1f5a87; --down-bg:#e7f0f8;
  /* Status (unchanged — keeps the parser + badges) */
  --ok:#0f7a53; --ok-bg:#e6f5ee; --warn:#8a5a00; --warn-bg:#fbf1de;
  --fail:#b42318; --fail-bg:#fdecea; --review:#1d4ed8; --review-bg:#e7edfd;
  /* Code + tooltip */
  --code-bg:#0f2233; --code-text:#e8f1f4; --tip-bg:#0f2233; --tip-text:#eaf6f6;
  /* Type — offline-safe fallbacks; named webfonts optional, never fetched */
  --serif:"EB Garamond",Georgia,"Times New Roman",serif;
  --sans:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  /* Radius / shadow */
  --r1:6px; --r2:10px; --r3:14px; --pill:999px;
  --sh:0 1px 2px rgba(17,24,39,.05); --sh2:0 8px 28px rgba(17,24,39,.12);
  --measure:46rem;
}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--text);background:var(--bg);line-height:1.6;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline;text-underline-offset:2px}
h1,h2,h3{font-family:var(--serif);font-weight:600;letter-spacing:-.01em}
header.top{position:sticky;top:0;z-index:50;background:var(--surface);border-bottom:1px solid var(--border)}
header.top::before{content:"";display:block;height:3px;background:var(--spectrum)}
.brand{display:flex;align-items:center;gap:12px;max-width:1080px;margin:0 auto;padding:14px clamp(16px,5vw,40px) 8px}
.skip{position:absolute;left:-999px}
.skip:focus{left:12px;top:12px;z-index:100;background:#fff;padding:8px 12px;border-radius:8px;box-shadow:var(--sh2)}
.chipnav{display:flex;gap:6px;flex-wrap:wrap;max-width:1080px;margin:0 auto;padding:0 clamp(16px,5vw,40px) 10px}
.chipnav a{font-family:var(--sans);font-size:.74rem;color:var(--muted);border:1px solid var(--border);
  border-radius:var(--pill);padding:.2rem .6rem;background:var(--surface)}
.chipnav a:hover{color:var(--brand-blue);border-color:var(--brand-blue);text-decoration:none}
.brand .logo{width:38px;height:38px;flex:0 0 38px;filter:drop-shadow(0 1px 2px rgba(17,24,39,.12))}
.brand .wordmark{font-family:var(--sans);font-weight:600;font-size:1.15rem;letter-spacing:-.01em}
.brand .ver{font-family:var(--mono);font-size:.66rem;color:var(--accent-2);background:var(--accent-tint);
  padding:.14rem .42rem;border-radius:5px;font-weight:500}
.brand .spacer{flex:1 1 auto}
.brand .rmeta{font-family:var(--sans);text-align:right;font-size:.8rem;color:var(--muted);line-height:1.35}
.brand .rmeta b{color:var(--text);font-weight:600}
main{max-width:1080px;margin:0 auto;padding:8px clamp(16px,5vw,40px) 8px}
.hero{padding:26px 0 4px}
.hero .kicker{font-family:var(--sans);text-transform:uppercase;letter-spacing:.12em;font-size:.7rem;
  font-weight:700;color:var(--brand-blue)}
.hero h1{font-family:var(--serif);font-size:clamp(1.9rem,4vw,2.5rem);line-height:1.1;margin:.35rem 0 .15rem}
.hero .sub{margin:0;color:var(--muted);font-family:var(--sans)}
.hero .sub b{color:var(--text);font-weight:600}
.hero .lede{margin:.35rem 0 0;color:var(--muted);font-family:var(--sans);font-size:.9rem;max-width:var(--measure)}
/* teal "In plain terms" callout — the read-along track */
.plain{background:var(--plain-bg);border:1px solid #cde9e7;border-left:4px solid var(--plain-border);
  border-radius:var(--r3);padding:16px 18px;margin:16px 0;max-width:var(--measure)}
.plain .tag{display:inline-block;font-family:var(--sans);font-size:.68rem;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;color:var(--plain-ink);margin-bottom:6px}
.plain .finding{font-family:var(--serif);font-size:clamp(1.02rem,1.5vw,1.18rem);line-height:1.5;margin:0}
.plain .finding b{font-weight:700} .plain .finding .up{color:var(--up-ink)} .plain .finding .down{color:var(--down-ink)}
.plain .note{font-family:var(--sans);font-size:.85rem;color:var(--plain-ink);margin:.6rem 0 0}
/* focusable glossary term — hover AND keyboard focus; never title= */
.term{position:relative;display:inline;border:0;background:none;padding:0;margin:0;font:inherit;
  color:var(--plain-ink);font-weight:600;cursor:help;border-bottom:1px dotted var(--brand-teal)}
.term:focus-visible{outline:2px solid var(--brand-teal);outline-offset:2px;border-radius:3px}
.term .tip{position:absolute;left:0;top:calc(100% + 9px);z-index:60;width:max-content;max-width:min(320px,82vw);
  background:var(--tip-bg);color:var(--tip-text);font-family:var(--sans);font-weight:400;font-size:.8rem;
  line-height:1.5;text-align:left;padding:10px 12px;border-radius:10px;box-shadow:var(--sh2);
  opacity:0;visibility:hidden;transition:opacity .12s;pointer-events:none}
.term:hover .tip,.term:focus-visible .tip{opacity:1;visibility:visible}
@media(max-width:560px){.term .tip{left:auto;right:0}}
/* "How to read this" — elaboration only, never the primary result */
.howto{margin-top:.6rem}
.howto>summary{cursor:pointer;list-style:none;font-family:var(--sans);font-weight:600;font-size:.82rem;
  color:var(--brand-blue-deep);display:inline-flex;align-items:center;gap:6px}
.howto>summary::-webkit-details-marker{display:none}
.howto>summary::before{content:"?";display:inline-flex;align-items:center;justify-content:center;width:18px;
  height:18px;border-radius:50%;background:var(--brand-teal);color:#04363a;font-weight:800;font-size:.72rem}
.howto>summary:focus-visible{outline:2px solid var(--brand-teal);outline-offset:2px;border-radius:3px}
.howto p{font-family:var(--sans);font-size:.82rem;color:var(--muted);line-height:1.55;margin:.45rem 0 0;
  border-left:3px solid var(--plain-border);background:var(--plain-bg);padding:.5rem .7rem;border-radius:0 6px 6px 0}
/* 3 headline stats + up/down mini-bar (the 10-second scan) */
.headline-stats{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0;max-width:var(--measure)}
.hstat{flex:1 1 120px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r2);
  padding:10px 12px;box-shadow:var(--sh)}
.hstat .v{font-family:var(--sans);font-weight:800;font-size:1.55rem;line-height:1;font-variant-numeric:tabular-nums}
.hstat.up .v{color:var(--up-ink)} .hstat.down .v{color:var(--down-ink)}
.hstat .k{font-family:var(--sans);font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);margin-top:4px}
.dirbar{height:8px;border-radius:var(--pill);overflow:hidden;display:flex;margin-top:8px;background:#eef0f5}
.dirbar .up{background:var(--up)} .dirbar .down{background:var(--down)}
.status-line{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap;
  font-family:var(--sans);font-size:.86rem;color:var(--muted)}
@media(max-width:560px){.chipnav{display:none}.brand .rmeta{display:none}}
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
.figgroup{font-family:var(--serif);font-size:1.06rem;font-weight:600;color:var(--brand-blue-deep);margin:22px 0 .1rem}
.figgroup-sub{font-family:var(--sans);font-size:.84rem;color:var(--muted);max-width:var(--measure);margin:0 0 12px}
.panels{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
figure.panel{margin:0;border:1px solid var(--border);border-radius:var(--r2);overflow:hidden;
  background:var(--surface);display:flex;flex-direction:column;box-shadow:var(--sh)}
.panel .frame{position:relative;background:#fff;border-bottom:1px solid var(--border)}
.panel .lab{position:absolute;top:8px;left:8px;z-index:2;font-family:var(--sans);font-weight:800;font-size:.78rem;
  color:var(--text);background:rgba(255,255,255,.92);border:1px solid var(--border-strong);border-radius:5px;
  width:22px;height:22px;display:flex;align-items:center;justify-content:center;box-shadow:var(--sh)}
.panel .figbtn{border:0;background:none;padding:0;margin:0;width:100%;display:block;cursor:zoom-in}
.panel .figbtn:focus-visible{outline:2px solid var(--brand-teal);outline-offset:-2px}
.panel img{width:100%;display:block;background:#fff}
figure.panel figcaption{padding:12px 14px}
.cap-lead{font-family:var(--serif);font-size:.98rem;font-weight:600;line-height:1.4;color:var(--text)}
.cap-tech{font-family:var(--sans);font-size:.8rem;color:var(--muted);margin-top:.35rem;line-height:1.5}
/* legend band above the DE scroll container — tooltips never clipped by overflow-x */
.legend{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;background:var(--bg);
  border:1px solid var(--border);border-radius:var(--r2);padding:10px 14px;margin:12px 0}
.legend .li{display:flex;align-items:center;gap:7px;font-family:var(--sans);font-size:.82rem;color:var(--text)}
.legend .sw{width:12px;height:12px;border-radius:3px;flex:0 0 12px}
.legend .sw.up{background:var(--up)} .legend .sw.down{background:var(--down)}
/* glossary */
.glossary dl{margin:0;display:grid;gap:12px 18px;grid-template-columns:1fr}
.gterm{font-family:var(--sans);font-weight:700;font-size:.9rem;color:var(--brand-blue-deep)}
.gdef{font-family:var(--sans);font-size:.86rem;color:var(--text);line-height:1.5;margin:.15rem 0 0}
@media(min-width:720px){.glossary dl{grid-template-columns:180px 1fr;align-items:baseline}
  .gterm{grid-column:1} .gdef{grid-column:2;margin:0}}
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
.pill.up{color:var(--up-ink);background:var(--up-bg)} .pill.down{color:var(--down-ink);background:var(--down-bg)}
.tablewrap{overflow-x:auto;border:1px solid var(--border);border-radius:8px}
table.data{border-collapse:collapse;width:100%;font-family:var(--sans);font-size:.83rem}
table.data th,table.data td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
table.data thead th{background:var(--accent-tint);color:var(--accent-2);font-weight:600;position:sticky;top:0}
table.sortable thead th{cursor:pointer;user-select:none}
table.sortable thead th[data-sort=asc]::after{content:" \\2191"}
table.sortable thead th[data-sort=desc]::after{content:" \\2193"}
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
.barfill{background:var(--spectrum);height:100%;border-radius:6px}
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


_MODE_LABEL = {"microarray": "Microarray", "fastq": "RNA-seq",
               "count_matrix": "Count matrix", "deseq2_results": "Provided DESeq2 results"}


def _hero_findings(run: dict, project: Path, sanity_text: str, name: str) -> str:
    # Hero + Key Findings: kicker, H1, subtitle, teal plain-language finding, 3 headline
    # stat chips + up/down mini-bar, status badge + sentence, and the "how to read" details.
    inp = run.get("input", {}) or {}
    de = run.get("deseq2", {}) or {}
    unit = "probes" if inp.get("type") == "microarray" else "genes"
    alpha = float(de.get("alpha", 0.05))
    lfc_t = _lfc_threshold(run, sanity_text)
    up, down, tested, top_up, top_down = _de_headline_stats(project, alpha, lfc_t)
    total = up + down
    num, den = _contrast_pair(run)

    mode = _MODE_LABEL.get(inp.get("type"), "")
    organism = _org_clean(run.get("reference", {}) or {})
    engine = _engine_name(run)
    sub = " · ".join(x for x in (mode, organism, engine) if x)
    sub_html = f'<p class="sub">{html.escape(sub)}</p>' if sub else ""

    finding = _key_finding(run, project, sanity_text)

    chips = ""
    if total > 0:
        up_pct = up / total * 100
        down_pct = 100 - up_pct
        num_lbl = html.escape(str(num)) if num else "treated"
        chips = (
            '<div class="headline-stats">'
            f'<div class="hstat"><div class="v">{total:,}</div>'
            f'<div class="k">{unit.capitalize()} changed</div>'
            '<div class="dirbar" aria-hidden="true">'
            f'<span class="up" style="width:{up_pct:.1f}%"></span>'
            f'<span class="down" style="width:{down_pct:.1f}%"></span></div></div>'
            f'<div class="hstat up"><div class="v">&#9650;&nbsp;{up:,}</div>'
            f'<div class="k">Higher in {num_lbl}</div></div>'
            f'<div class="hstat down"><div class="v">&#9660;&nbsp;{down:,}</div>'
            f'<div class="k">Lower in {num_lbl}</div></div></div>')

    overall, _ = _parse_sanity(sanity_text)
    status = _status_sentence(sanity_text)
    status_line = ""
    if overall or status:
        badge = _badge(overall) if overall else ""
        status_line = f'<div class="status-line">{badge}<span>{html.escape(status)}</span></div>'

    howto = (
        '<details class="howto"><summary>How to read this report</summary>'
        '<p>Each section opens with a <b>teal box</b> explaining the finding in plain language; the '
        'tables and figures below carry the full numbers — nothing is simplified away. '
        '<span style="border-bottom:1px dotted var(--brand-teal);color:var(--plain-ink);font-weight:600">'
        'Dotted-underlined</span> terms show a definition on hover or keyboard focus, and every one is '
        'collected in the Glossary at the end. Direction is colour-coded throughout: '
        '<b style="color:var(--up-ink)">&#9650; red = higher</b>, '
        '<b style="color:var(--down-ink)">&#9660; blue = lower</b> in the treated group.</p></details>')

    return (
        '<div class="hero"><div class="kicker">Guided results report</div>'
        f'<h1>{html.escape(name)}</h1>{sub_html}'
        '<p class="lede">Read the teal box for the plain-language story; the tables and figures '
        'below carry the full numbers.</p></div>'
        '<section id="findings" aria-label="Key findings">'
        '<div class="plain" style="margin-top:0"><span class="tag">In plain terms</span>'
        f'<p class="finding">{finding}</p>'
        '<p class="note">These are leads to confirm, not proof of function — treat the top '
        'genes as a shortlist to follow up.</p></div>'
        f'{chips}{status_line}{howto}</section>')


def _study_design_section(run: dict) -> str:
    de = run.get("deseq2", {}) or {}
    ref = run.get("reference", {}) or {}
    is_micro = (run.get("input", {}) or {}).get("type") == "microarray"
    num, den = _contrast_pair(run)
    engine = _engine_name(run)
    design = de.get("design_formula")
    if not (num and den) and not design:
        return ""

    if num and den:
        sentence = (f"This run compares <b>{html.escape(str(num))}</b> with "
                    f"<b>{html.escape(str(den))}</b>")
    else:
        sentence = "This run tests for differential expression"
    if design:
        sentence += f" on a <code>{html.escape(str(design))}</code> design"
    sentence += f", analysed with {html.escape(engine)}."

    rows: list[tuple[str, str]] = []

    def add(k: str, v) -> None:
        s = "" if v is None else str(v).strip()
        if s and s.lower() not in ("none", "unset", "na"):
            rows.append((k, s))

    add("Reference mode", ref.get("mode"))
    add("Reference source", ref.get("source"))
    add("Reference release", ref.get("release"))
    if de.get("lfc_shrinkage"):
        add("LFC shrinkage", de.get("shrinkage_method") or "enabled")
    if not is_micro:
        fc = run.get("featurecounts", {}) or {}
        if fc.get("strandedness") is not None:
            add("Strandedness", fc.get("strandedness"))
    custom = run.get("customized_parameters", {}) or {}
    for pkey, pv in custom.items():
        if isinstance(pv, dict) and not isinstance(pv.get("used"), (list, dict)):
            add(pkey, pv.get("used"))

    details = ""
    if rows:
        body = "".join(
            f"<tr><td>{html.escape(k)}</td><td class='mono'>{html.escape(v)}</td></tr>"
            for k, v in rows)
        details = ("<details class='howto'><summary>Full configuration</summary>"
                   "<div class='tablewrap' style='margin-top:.5rem'><table class='data'>"
                   f"<tbody>{body}</tbody></table></div></details>")
    return (f"<section id='design'><h2>Study design</h2><p>{sentence}</p>{details}</section>")


def _panel(figs: Path, basename: str, letter: str, cap_lead: str = "") -> str:
    # One grouped, letter-chipped figure panel. Preserves the exact zoom contract:
    # button.figbtn > img, onclick bsqZoom(this) reads querySelector('img').
    src = _fig_src(figs, basename)
    if not src:
        return ""
    _grp, title, lead, tech, howto = FIG[basename]
    lead = cap_lead or lead
    alt = html.escape(title)
    return (
        '<figure class="panel"><div class="frame">'
        f'<span class="lab" aria-hidden="true">{letter}</span>'
        f'<button class="figbtn" type="button" onclick="bsqZoom(this)" '
        f'aria-label="Open {alt} full size"><img alt="{alt}" src="{src}"/></button></div>'
        f'<figcaption><div class="cap-lead">{lead}</div>'
        f'<div class="cap-tech">{tech}</div>'
        f'<details class="howto"><summary>How to read this</summary><p>{howto}</p></details>'
        '</figcaption></figure>')


def _figure_groups(figs: Path, up: int, down: int, unit: str) -> str:
    total = up + down
    dyn = {}
    if total > 0:
        dyn["volcano"] = f"{up:,} {unit} rose and {down:,} fell past the cut-off."
        dyn["top_upregulated_heatmap"] = f"The {min(up, 50):,} strongest increases, sample by sample."
        dyn["top_downregulated_heatmap"] = f"The {min(down, 50):,} strongest decreases, sample by sample."
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    idx = 0
    out: list[str] = []
    for gkey, gtitle, gsub in FIG_GROUPS:
        panels: list[str] = []
        for basename, meta in FIG.items():
            if meta[0] != gkey:
                continue
            letter = letters[idx] if idx < len(letters) else str(idx + 1)
            p = _panel(figs, basename, letter, dyn.get(basename, ""))
            if not p:
                continue
            panels.append(p)
            idx += 1
        if not panels:
            continue
        out.append(f'<div class="figgroup">{gtitle}</div>'
                   f'<p class="figgroup-sub">{gsub}</p>'
                   f'<div class="panels">{"".join(panels)}</div>')
    if not out:
        return ""
    intro = ('<p class="muted small">Click any panel to open it full size and zoom — figures '
             'embed as vector SVG where practical, so they stay sharp.</p>')
    return f'<section id="figures"><h2>Figures</h2>{intro}{"".join(out)}</section>'


def _de_section(project: Path, up: int, down: int, num, den, unit: str) -> str:
    split = _de_split(project)
    if not split:
        return ""
    total = up + down
    one = unit[:-1] if unit.endswith("s") else unit
    badge = f'<span class="badge muted">{total:,} {unit}</span>' if total else ""
    num_lbl = html.escape(str(num)) if num else "the treated group"
    den_lbl = html.escape(str(den)) if den else "the control group"
    plain = (
        '<div class="plain"><span class="tag">In plain terms</span>'
        f'<p class="finding" style="font-size:1rem">Of the {unit} tested, <b>{up:,}</b> were clearly '
        f'<b class="up">higher</b> and <b>{down:,}</b> clearly <b class="down">lower</b> in '
        f'{num_lbl} than in {den_lbl}. The biggest movers in each direction are listed below — '
        'useful as leads to follow up.</p></div>')
    legend = (
        '<div class="legend" aria-label="How to read the columns">'
        f'<span class="li"><span class="sw up"></span>&#9650; higher in {num_lbl}</span>'
        f'<span class="li"><span class="sw down"></span>&#9660; lower in {num_lbl}</span>'
        f'<span class="li">{_term("log2fc", "log2 fold change")}</span>'
        f'<span class="li">{_term("padj", "FDR (padj)")}</span>'
        f'<span class="li">{_term("basemean", "baseMean")}</span></div>')
    howto = (
        '<details class="howto"><summary>How to read this table</summary>'
        f'<p>Each row is one {one}. <b>log2FC</b> is the size and direction of the change '
        '(red + = higher, blue &minus; = lower); <b>padj</b> is confidence (smaller = stronger, '
        f'and every {one} here is below &alpha;); <b>baseMean</b> is overall expression — a big fold '
        'change on a very low baseMean is worth checking before trusting it. Click any header to sort. '
        'Full lists: <code>results/deseq2/upregulated_genes.csv</code> and '
        '<code>downregulated_genes.csv</code>.</p></details>')
    return (f'<section id="de"><div class="sec-head"><h2>Which genes changed</h2>{badge}</div>'
            f'{plain}{legend}{split}{howto}</section>')


def _glossary_section() -> str:
    items = "".join(
        f'<div class="gterm">{html.escape(label)}</div>'
        f'<div class="gdef">{html.escape(GLOSS[key])}</div>'
        for label, key in GLOSS_ORDER if key in GLOSS)
    if not items:
        return ""
    return ('<section id="glossary" class="glossary"><h2>Glossary</h2>'
            '<p class="muted small">Every term marked with a dotted underline in this report, '
            'defined once here so it travels with the file even when printed.</p>'
            f'<dl>{items}</dl></section>')


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

    inp = run.get("input", {}) or {}
    de = run.get("deseq2", {}) or {}
    unit = "probes" if inp.get("type") == "microarray" else "genes"
    alpha = float(de.get("alpha", 0.05))
    lfc_t = _lfc_threshold(run, sanity)
    up, down, _tested, _tu, _td = _de_headline_stats(project, alpha, lfc_t)
    num, den = _contrast_pair(run)

    hero_findings = _hero_findings(run, project, sanity, name)
    meta_cards = _meta_cards(run, project)
    study = _study_design_section(run)
    figures = _figure_groups(figs, up, down, unit)
    de_html = _de_section(project, up, down, num, den, unit)
    enrichment = _enrichment_section(project)
    runtime = _timing_section(timing)
    sanity_html = _sanity_section(sanity)
    versions = _versions_table(run)
    glossary = _glossary_section()

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BulkSeq Studio report — {html.escape(name)}</title><style>{CSS}</style></head>
<body>
<a class="skip" href="#findings">Skip to key findings</a>
<header class="top"><div class="brand">{LOGO_SVG}
<span class="wordmark">BulkSeq Studio</span>{ver_chip}<span class="spacer"></span>{rmeta}</div>
<nav class="chipnav" aria-label="Jump to section">
<a href="#findings">Key findings</a><a href="#figures">Figures</a><a href="#de">Genes</a>
<a href="#enrichment">Pathways</a><a href="#sanity">Quality</a><a href="#runtime">Runtime</a>
<a href="#versions">Software</a><a href="#glossary">Glossary</a></nav></header>
<main>
{hero_findings}
{meta_cards}
{study}
{figures}
{de_html}
{enrichment}
{sanity_html}
{runtime}
{versions}
{glossary}
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
// Sortable tables: click a header to sort; numeric columns sort numerically. Third
// click restores the original order. Purely client-side, no dependencies.
(function(){{
  function cmp(a,b){{var x=parseFloat(a),y=parseFloat(b);
    if(!isNaN(x)&&!isNaN(y))return x-y; return a.localeCompare(b);}}
  document.querySelectorAll('table.sortable').forEach(function(tbl){{
    var tb=tbl.tBodies[0]; if(!tb)return;
    var orig=Array.prototype.slice.call(tb.rows);
    tbl.querySelectorAll('thead th').forEach(function(th,ci){{
      th.style.cursor='pointer'; th.title='Sort by '+(th.textContent||'').trim();
      th.addEventListener('click',function(){{
        var st=th.getAttribute('data-sort'); var next=st==='asc'?'desc':(st==='desc'?'none':'asc');
        tbl.querySelectorAll('thead th').forEach(function(o){{o.removeAttribute('data-sort');}});
        var rows=Array.prototype.slice.call(tb.rows);
        if(next==='none'){{orig.forEach(function(r){{tb.appendChild(r);}});return;}}
        rows.sort(function(r1,r2){{
          var c=cmp((r1.cells[ci]||{{}}).textContent||'',(r2.cells[ci]||{{}}).textContent||'');
          return next==='asc'?c:-c;}});
        rows.forEach(function(r){{tb.appendChild(r);}});
        th.setAttribute('data-sort',next);}});
    }});
  }});
}})();
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
