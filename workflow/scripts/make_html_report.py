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
# R serialises a missing gene_name (NA_character_) as the bare token NA in the CSV; treat NA and the
# other missing-value spellings as empty so the report falls back to the gene_id instead of printing
# a literal "NA" as a gene symbol (breaks the headline + the symbol column on non-model organisms).
_NA_SYMBOL_TOKENS = {"", "na", "nan", "n/a", "null", "none", "."}


def _sym_or_blank(val: str | None) -> str:
    s = (val or "").strip()
    return "" if s.lower() in _NA_SYMBOL_TOKENS else s


# One source of truth for every metric definition — feeds inline .term tooltips AND the §10 glossary.
GLOSS = {
 "padj":     "Adjusted p-value (FDR, Benjamini–Hochberg). The chance a gene looks changed just by luck after testing thousands at once. Smaller is stronger; below the run's cutoff is significant.",
 "pvalue":   "Raw p-value, before correcting for testing thousands of genes. Use padj — not this — to decide significance.",
 "log2fc":   "Log2 fold change: how much and which way a gene shifted. +1 = doubled, −1 = halved, +2 = four-fold. The sign is the direction; positive = higher in the numerator group.",
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
 "padjust":  "Adjusted p-value for the term — the expected false-alarm share if you trust it. Corrected by Benjamini–Hochberg on the clusterProfiler route (shown as 'p.adjust (BH)') and by g:SCS, g:Profiler's own graph-aware method, on the g:Profiler route (shown as 'p (g:SCS)'). Both are already corrected for multiple testing; they are not comparable to each other term-for-term.",
 "setsize":  "How many measured genes belong to that set or category.",
 "ppi":      "STRING-supported functional and physical protein associations among your changed genes. The combined score integrates multiple evidence channels; an edge does not necessarily mean direct physical binding.",
 "wilcoxon": "A rank-based cross-check, not used to call genes. With few replicates it is underpowered — read it only as a rank-concordance check.",
}

# Terms shown in the §10 glossary, in reading order (label, GLOSS key).
GLOSS_ORDER = [
 ("Adjusted p-value (FDR / padj)", "padj"), ("Raw p-value", "pvalue"),
 ("log2 fold change", "log2fc"), ("baseMean", "basemean"), ("Biotype", "biotype"),
 ("α (alpha)", "alpha"), ("PCA", "pca"), ("Sample distance", "distance"),
 ("p-value histogram", "pvalhist"), ("Volcano plot", "volcano"), ("MA plot", "ma"),
 ("Heatmap", "heatmap"), ("NES", "nes"), ("Fold enrichment", "foldenr"),
 ("Adjusted p-value (term)", "padjust"), ("Set size", "setsize"),
 ("Protein–protein interaction (PPI)", "ppi"), ("Wilcoxon cross-check", "wilcoxon"),
]

# basename -> (group, letter-title, cap-lead plain, cap-tech, howto text).
FIG = {
 "pca":                       ("quality", "Principal-component analysis", "Do the samples group the way the design expects?", "Principal components of variance-stabilised counts; axis labels give the % variance each explains.", "PCA compresses all genes into two axes so whole samples compare at a glance. Replicates of one group should cluster; the two conditions should sit apart. A replicate among the wrong group flags a swap or outlier."),
 "sample_distance":           ("quality", "Sample-to-sample distance", "Which samples resemble each other?", "Euclidean distance on variance-stabilised counts, hierarchically clustered.", "Darker cells are more alike. Replicates of one group form blocks along the diagonal; an off-diagonal dark cell points to a mislabelled or outlier sample."),
 "pvalue_histogram":          ("quality", "p-value histogram (diagnostic)", "Is there real signal, and is the model well-behaved?", "Distribution of raw p-values across all tested genes.", "A tall spike near zero on an otherwise flat background means real differences are present. A hump in the middle, or a spike at one, warns that the statistical model may not fit."),
 "volcano":                   ("de", "Volcano plot", "Which genes changed, and how confidently?", "x: log2 fold change; y: −log10 adjusted p-value. Dashed guides mark the significance and fold-change cut-offs.", "Every dot is a gene. Left–right is how much it changed; up is statistical confidence. The top corners hold large, reliable changes — the headline hits. Height is confidence, not effect size."),
 "ma_plot":                   ("de", "MA plot", "Are the changes independent of expression level?", "x: mean normalised counts (log); y: shrunken log2 fold change. Coloured points are significant.", "Fold change is plotted against overall expression. A healthy result shows significant genes across the whole expression range, not only among the lowest-count genes on the left."),
 "top_deg_heatmap":           ("de", "Top differentially-expressed genes", "The most statistically supported genes, sample by sample.", "Z-scored variance-stabilised counts for the top genes by significance.", "Rows are genes, columns are samples; warm = high, cool = low relative to the row mean. Samples of one condition should share a colour pattern, and the two conditions should look different."),
 "top_upregulated_heatmap":   ("de", "Top up-regulated genes", "The most statistically supported increases across samples.", "Z-scored variance-stabilised counts, top up-regulated by significance.", "The most confidently increased genes. Warm cells should concentrate in the numerator group; a gene warm in both groups is worth a second look."),
 "top_downregulated_heatmap": ("de", "Top down-regulated genes", "The most statistically supported decreases across samples.", "Z-scored variance-stabilised counts, top down-regulated by significance.", "The most confidently decreased genes. Cool cells should concentrate in the numerator group; a gene cool in both groups is worth a second look."),
 "enrichment_dotplot":        ("function", "GO enrichment", "Which biological themes are over-represented?", "Dot size: gene count; colour: adjusted p-value; position: fold enrichment.", "Each dot is a biological category enriched among the changed genes. Bigger, further-right, darker dots are the stronger, more reliable themes."),
 "enrichment_kegg_dotplot":   ("function", "KEGG pathway enrichment", "Which pathways are over-represented?", "Dot size: gene count; colour: adjusted p-value.", "Each dot is a KEGG pathway over-represented among the changed genes. Bigger and darker dots are the stronger, more reliable pathways."),
 "ppi_network":               ("function", "STRING protein-association network", "Which changed proteins have STRING-supported functional or physical associations?", "STRING combined-score associations above the confidence cut-off; nodes are seed genes, clusters are modules.", "Nodes are your changed genes; edges are STRING-supported functional or physical associations, not necessarily direct binding. Tight clusters suggest related proteins, and highly connected hubs are candidates worth prioritising."),
}
FIG_GROUPS = [
 ("quality",  "Quality &amp; sample structure", "Do replicates group together, and do the conditions separate? These panels answer that before any gene is called."),
 ("de",       "Differential expression",   "Which genes changed, by how much, and how confidently."),
 ("function", "Function &amp; interactions",   "What biology the changed genes point to, and how they connect."),
]


def _term(key: str, label: str, definition: str | None = None) -> str:
    # Inline glossable term — focusable <button> (never title=); the same GLOSS entry
    # is restated in the §10 glossary so nothing load-bearing lives only on hover.
    tip = html.escape(GLOSS.get(key, "") if definition is None else definition)
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
# Dense keyed figures need this rendered width to keep their smallest measured
# labels legible. Narrow reports expose a contained scroll viewport instead.
DENSE_FIGURE_MIN_WIDTH_PX = 760
DENSE_FIGURE_BASENAMES = frozenset({"ppi_network", "volcano"})


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


def _panel_slug(basename: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", basename.casefold()).strip("-") or "figure"


def _panel_attrs(basename: str) -> str:
    """Return stable, escaped figure identity and derived layout attributes."""
    slug = _panel_slug(basename)
    identity = html.escape(basename, quote=True)
    dense = ' data-panel-layout="dense"' if basename in DENSE_FIGURE_BASENAMES else ""
    return f'class="panel panel--{slug}" data-figure="{identity}"{dense}'


def _panel_frame_open(basename: str, title: str) -> str:
    if basename not in DENSE_FIGURE_BASENAMES:
        return '<div class="frame">'
    label = html.escape(title, quote=True)
    hint_id = f"{_panel_slug(basename)}-scroll-hint"
    return (
        '<div class="frame dense-figure-viewport" tabindex="0" role="region" '
        f'aria-label="Scrollable {label} preview" aria-describedby="{hint_id}" '
        f'data-min-content-width="{DENSE_FIGURE_MIN_WIDTH_PX}" '
        f'style="--dense-min-width:{DENSE_FIGURE_MIN_WIDTH_PX}px">'
        f'<span class="sr-only" id="{hint_id}">Use Left and Right Arrow keys to inspect '
        'this dense figure preview. Activate the figure button to open it full size.</span>'
    )


def _fig(figs: Path, basename: str, title: str) -> str:
    src = _fig_src(figs, basename)
    if not src:
        return ""
    cap = html.escape(title)
    panel_attrs = _panel_attrs(basename)
    frame_open = _panel_frame_open(basename, title)
    return (
        f'<figure {panel_attrs}>{frame_open}'
        f'<button class="figbtn" type="button" onclick="bsqZoom(this)" '
        f'aria-label="Open {cap} full size"><img alt="{cap}" src="{src}"/></button></div>'
        f'<figcaption><div class="cap-lead">{cap}</div></figcaption></figure>'
    )


def _count_csv_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            return max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    except OSError:
        return None


def _de_table(
    results_csv: Path,
    top: int = 25,
    empty_msg: str = "No differential-expression table.",
    *,
    base_mean_kind: str = "count",
) -> str:
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

    numeric = ("log2FoldChange", "padj", "baseMean")

    def cell(col: str, val: str) -> str:
        # For count-based routes, a thousands-separated integer is easier to read than 1.23e+03.
        # Microarray and externally supplied routes use an expression-scale value that can be
        # fractional or negative, so preserve its numeric scale instead of rounding it to a count.
        # data-sort-value keeps sorting numeric when comma grouping is used.
        if col == "baseMean":
            try:
                f = float(val)
                shown = f"{int(round(f)):,}" if base_mean_kind == "count" else f"{f:.3g}"
                return f"<td class='num' data-sort-value='{f}'>{shown}</td>"
            except (ValueError, TypeError):
                return f"<td class='num'>{html.escape(val or '')}</td>"
        if col in numeric:
            try:
                return f"<td class='num'>{float(val):.3g}</td>"
            except (ValueError, TypeError):
                return f"<td class='num'>{html.escape(val or '')}</td>"
        # Gene symbols italic (HGNC convention), matching the report's prose (_fmt_genes).
        # <i> wraps the escaped text only, so the sort JS still reads the plain symbol. A missing
        # symbol (NA/blank on a non-model organism) renders as an empty cell, not a literal "NA".
        if col == "symbol":
            sym = _sym_or_blank(val)
            return f"<td><i>{html.escape(sym)}</i></td>" if sym else "<td></td>"
        return f"<td>{html.escape(val or '')}</td>"

    head = "".join(f"<th scope='col' class='num'>{html.escape(c)}</th>" if c in numeric
                   else f"<th scope='col'>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(cell(c, r.get(c, "")) for c in cols) + "</tr>" for r in rows)
    # `sortable`: click a header to sort (numeric columns sort numerically). See the
    # embedded script in the page template.
    return (f"<div class='tablewrap'><table class='data sortable'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def _de_split(project: Path, top: int = 50, *, base_mean_kind: str = "count") -> str:
    # Up- and down-regulated genes shown separately, from the canonical DEG sets
    # (run_deseq2.R: padj-significant, raw |log2FC| >= threshold), each ordered by
    # fold change. Empty when a direction has no genes or DE has not run.
    deseq = project / "results" / "deseq2"
    up = _de_table(
        deseq / "upregulated_genes.csv", top,
        empty_msg="No up-regulated genes passed the significance and fold-change thresholds.",
        base_mean_kind=base_mean_kind,
    )
    down = _de_table(
        deseq / "downregulated_genes.csv", top,
        empty_msg="No down-regulated genes passed the significance and fold-change thresholds.",
        base_mean_kind=base_mean_kind,
    )
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


def _is_external_results(run: dict) -> bool:
    return (run.get("input", {}) or {}).get("type") == "deseq2_results"


def _external_provenance(run: dict) -> dict:
    provenance = (run.get("input", {}) or {}).get("deseq2_results_provenance") or {}
    return provenance if isinstance(provenance, dict) else {}


def _recorded_method(run: dict, key: str) -> str:
    value = str(_external_provenance(run).get(key) or "").strip()
    return value if value and value.casefold() != "unknown" else "unknown"


def _adjusted_p_name(run: dict) -> str:
    """Use a generic name unless the upstream correction method was recorded."""
    if not _is_external_results(run):
        return "FDR"
    method = _recorded_method(run, "p_adjustment_method")
    return "adjusted p-value" if method == "unknown" else f"adjusted p-value ({method})"


def _contrast_pair(run: dict):
    # Imported results need their own source-table provenance. Do not fall back to a
    # potentially stale local-model contrast: BulkSeq Studio did not fit one on this route.
    inp = run.get("input", {}) or {}
    if inp.get("type") == "deseq2_results":
        direction = inp.get("deseq2_results_direction") or {}
        if isinstance(direction, dict) and direction.get("confirmed") is True:
            numerator = str(direction.get("numerator") or "").strip()
            denominator = str(direction.get("denominator") or "").strip()
            return numerator or None, denominator or None
        return None, None
    # (numerator, denominator) from the first local-model contrast.
    de = run.get("deseq2", {}) or {}
    contrasts = de.get("contrasts")
    c = contrasts[0] if isinstance(contrasts, list) and contrasts else {}
    num, den = c.get("numerator"), c.get("denominator")
    if not (num and den) and isinstance(c.get("name"), str) and "_vs_" in c["name"]:
        num, den = c["name"].split("_vs_", 1)
    return num, den


def _route_gloss(run: dict) -> dict[str, str]:
    """Return report definitions that do not infer external statistical methods."""
    definitions = dict(GLOSS)
    input_type = (run.get("input", {}) or {}).get("type")
    num, den = _contrast_pair(run)
    if num and den:
        definitions["log2fc"] = (
            "Log2 fold change: how much and which way a gene shifted. "
            f"+1 = doubled, −1 = halved, +2 = four-fold. Positive values mean higher "
            f"expression in {num} (the numerator) than in {den} (the denominator)."
        )
    if input_type == "microarray":
        definitions["basemean"] = (
            "Mean normalized log2 expression intensity across all samples. Fractional and negative "
            "values are valid on this array-expression scale; lower values indicate lower overall "
            "measured expression, not negative molecule counts."
        )
        definitions["ma"] = (
            "Fold change (y) against overall normalized log2 expression intensity (x). Confirms "
            "changes are not confined to genes with low measured expression."
        )
        return definitions
    if not _is_external_results(run):
        return definitions
    method = _recorded_method(run, "p_adjustment_method")
    if method == "unknown":
        adjustment = ("The upstream multiple-testing correction method was not recorded; "
                      "do not infer one from the column name.")
    else:
        adjustment = f"The recorded upstream correction method is {method}."
    definitions["padj"] = (
        "Adjusted p-value supplied by the source analysis. Smaller values provide stronger "
        f"evidence after the source analysis' multiple-testing adjustment. {adjustment}"
    )
    definitions["alpha"] = (
        "The threshold this report applies to the source-supplied adjusted p-values. "
        "It does not identify an unrecorded upstream correction method."
    )
    if num and den:
        direction = (f"Positive values mean higher expression in {num} (the confirmed numerator) "
                     f"than {den} (the confirmed denominator).")
    else:
        direction = "The positive and negative directions must not be assigned to groups without confirmed labels."
    definitions["log2fc"] = (
        "Source-supplied log2 fold change: how much and which way a gene shifted. "
        f"+1 = doubled, −1 = halved, +2 = four-fold. {direction}"
    )
    definitions["pvalue"] = (
        "Raw p-value supplied by the source analysis, before its multiple-testing adjustment. "
        "Use the supplied adjusted p-value for this report's threshold."
    )
    definitions["basemean"] = (
        "Source-supplied mean-expression measure, when present. Its exact scale and normalization "
        "come from the upstream analysis and are not inferred here."
    )
    return definitions


def _directional_figure_copy(basename: str, howto: str, run: dict) -> str:
    """Name the recorded numerator in directional heatmap guidance."""
    if basename not in {"top_upregulated_heatmap", "top_downregulated_heatmap"}:
        return howto
    num, _den = _contrast_pair(run)
    if not num:
        return howto
    return howto.replace(
        "the numerator group", f"{html.escape(str(num))}, the numerator group"
    )


def _engine_name(run: dict) -> str:
    # Microarray always runs limma (the 09 sanity line confirms it). Imported results
    # are not fitted locally, so never label them as a BulkSeq Studio DESeq2 run.
    input_type = (run.get("input", {}) or {}).get("type")
    if input_type == "microarray":
        return "limma"
    if input_type == "deseq2_results":
        return "externally supplied results (no local DE model)"
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
                    sym = _sym_or_blank(r.get("symbol"))
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
    # The microarray ingest collapses probes to unique gene-level rows before
    # limma; calling the resulting 22,180-row table "probes" misstates its unit.
    unit = "genes/features" if inp.get("type") == "microarray" else "genes"
    up, down, tested, top_up, top_down = _de_headline_stats(project, float(alpha), lfc_t)
    total = up + down

    if num and den:
        lead = f"Comparing <b>{html.escape(str(num))}</b> against <b>{html.escape(str(den))}</b>"
        if organism:
            lead += f" in <i>{html.escape(str(organism))}</i>"
    else:
        lead = "In this comparison"
    if _is_external_results(run):
        thr = f"{html.escape(_adjusted_p_name(run))} &lt; {html.escape(str(alpha))}"
    else:
        thr = f"FDR &lt; {alpha}"
    fold = _fold_phrase(lfc_t)
    if fold:
        thr += f" and {fold}"

    if not res_has_de(project):
        return f"{lead}, differential-expression results are not available for this run."
    if total == 0:
        return (f"{lead}, <b>no</b> {unit} changed at the chosen thresholds ({thr}). "
                f"See the volcano and p-value histogram for why.")
    of_m = f" of {tested:,} tested" if tested else ""
    s = (f"{lead}, <b>{total:,}</b>{of_m} {unit} changed significantly ({thr}): "
         f"<b class='up'>{up:,}</b> were higher and <b class='down'>{down:,}</b> lower.")
    if top_up:
        s += f" The most statistically supported increases were {_fmt_genes(top_up)}."
    if top_down:
        s += f" The most statistically supported decreases were {_fmt_genes(top_down)}."
    return s


def _status_sentence(sanity_text: str) -> str:
    overall, checks = _parse_sanity(sanity_text)
    if not checks:
        return ""
    n = len(checks)
    non = [_pretty_check_name(c["name"]) for c in checks if c["status"] != "PASS"]
    if not non:
        return f"All {n} automated quality checks passed."
    has_fail = any(c["status"] == "FAIL" for c in checks)
    word = ("issue" if has_fail else "advisory note") + ("s" if len(non) != 1 else "")
    tail = "" if has_fail else " The run completed and the results are usable."
    return (f"{n - len(non)} of {n} checks passed; {len(non)} {word}: "
            f"{', '.join(html.escape(x) for x in non)}.{tail}")


# (header, [candidate CSV columns — first present wins], kind). The alternate names cover the
# g:Profiler (gost) enrichment route, whose CSV uses term_name / p_value / intersection_size /
# term_size instead of clusterProfiler's Description / p.adjust / Count / setSize; without them the
# GO table for a non-model organism rendered with no columns (an empty table).
_ORA_COLS = [("Description", ["Description", "term_name"], "desc"),
             ("Fold enrichment", ["FoldEnrichment"], "g3"),
             ("Genes", ["Count", "intersection_size"], "int"),
             ("p.adjust", ["p.adjust", "p_value"], "g2")]
_GSEA_COLS = [("Description", ["Description", "term_name"], "desc"), ("NES", ["NES"], "g3"),
              ("p.adjust", ["p.adjust", "p_value"], "g2"), ("Set size", ["setSize", "term_size"], "int")]

# The two enrichment routes correct for multiple testing by DIFFERENT methods, so one
# shared "p.adjust" header states the wrong one for whichever route it does not match.
# clusterProfiler's p.adjust is Benjamini-Hochberg (set explicitly, pAdjustMethod="BH").
# g:Profiler's gost() returns p_value ALREADY corrected, by its default g:SCS
# (Set Counts and Sizes) method -- a graph-aware correction, not BH. The header is
# derived from which column the CSV actually carries, so it cannot drift from the route.
_ORA_PVALUE_HEADERS = {"p.adjust": "p.adjust (BH)", "p_value": "p (g:SCS)"}


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


def _enrich_block(title: str, csv_path: Path, mode: str, top: int = 10,
                  empty_msg: str = "No terms passed the significance threshold.") -> str:
    rows = _enrich_rows(csv_path, top)
    if not rows:
        # CSV present but with no rows -> the analysis RAN and nothing passed the threshold; say so
        # instead of the block silently vanishing. CSV absent -> that analysis did not run for this
        # organism/mode -> omit it (never imply an analysis ran when it did not). empty_msg lets the
        # caller distinguish "ran, nothing significant" from "not run for this organism" (no OrgDb).
        if csv_path.exists():
            return (f"<div class='enr-block empty'><h3>{html.escape(title)}</h3>"
                    f"<p class='muted small'>{html.escape(empty_msg)}</p></div>")
        return ""
    # Resolve each logical column to the first candidate header actually in the CSV, so both the
    # clusterProfiler and g:Profiler column vocabularies render. spec entries are (header, key, kind).
    spec = []
    for header, keys, kind in (_GSEA_COLS if mode == "gsea" else _ORA_COLS):
        key = next((k for k in keys if k in rows[0]), None)
        if key is not None:
            if header == "p.adjust":
                header = _ORA_PVALUE_HEADERS.get(key, header)
            spec.append((header, key, kind))
    # No recognizable columns -> suppress the block entirely rather than emit an empty table.
    if not spec:
        return ""

    def fmt(kind: str, val: str) -> str:
        if kind == "desc":
            return f"<td class='desc'>{html.escape(val or '')}</td>"
        try:
            f = float(val)
        except (ValueError, TypeError):
            return f"<td class='num'>{html.escape(val or '')}</td>"
        txt = f"{int(round(f))}" if kind == "int" else (f"{f:.2g}" if kind == "g2" else f"{f:.3g}")
        return f"<td class='num'>{txt}</td>"

    head = "".join(f"<th scope='col' class='{'desc' if k == 'desc' else 'num'}'>{html.escape(h)}</th>"
                   for h, _, k in spec)
    body = "".join("<tr>" + "".join(fmt(k, r.get(key, "")) for _, key, k in spec) + "</tr>"
                   for r in rows)
    return (f"<div class='enr-block'><h3>{html.escape(title)}</h3>"
            f"<div class='tablewrap'><table class='data enr'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div></div>")


def _enrich_table_state(csv_path: Path, mode: str) -> str:
    """Classify an optional enrichment table without treating damage as a null result."""
    if not csv_path.exists():
        return "missing"
    try:
        with csv_path.open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh, strict=True)
            fieldnames = reader.fieldnames or []
            has_row = False
            for row in reader:
                has_row = has_row or any(
                    value is not None and str(value).strip() for value in row.values())
    except (OSError, csv.Error):
        return "malformed"
    if not fieldnames:
        # run_custom_enrichment.R deliberately creates a blank file when no result passes.
        return "empty"
    candidates = _GSEA_COLS if mode == "gsea" else _ORA_COLS
    recognized = any(key in fieldnames for _, keys, _ in candidates for key in keys)
    if not recognized:
        return "malformed"
    return "data" if has_row else "empty"


def _custom_source_evidence(project: Path) -> tuple[bool | None, list[str]]:
    """Return the recorded route state and configured custom-set sources, when available."""
    run_path = project / "results" / "reports" / "run_summary.json"
    if not run_path.exists():
        return None, []
    run = _load_json(run_path)
    if "gene_sets" not in run or not isinstance(run.get("gene_sets"), dict):
        return None, []
    gene_sets = run["gene_sets"]
    specs = (
        ("Configured GMT source", "custom_gene_sets"),
        ("Configured annotation-table source", "functional_annotation_table"),
        ("Configured ORA background source", "background_gene_list"),
    )
    route_keys = {"custom_gene_sets", "functional_annotation_table"}
    configured = False
    lines = []
    for label, key in specs:
        value = gene_sets.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{label}: {value.strip()}")
            configured = configured or key in route_keys
    return configured, lines


def _custom_enrich_block(title: str, csv_path: Path, mode: str, empty_msg: str) -> str:
    state = _enrich_table_state(csv_path, mode)
    if state == "data":
        return _enrich_block(title, csv_path, mode)
    if state == "empty":
        msg = empty_msg
    elif state == "missing":
        msg = (f"The {mode.upper()} result table is unavailable; do not interpret the missing "
               "artifact as an empty custom-enrichment result.")
    else:
        msg = (f"The {mode.upper()} result table could not be interpreted; review the custom "
               "enrichment log and source artifact before drawing conclusions.")
    return (f"<div class='enr-block empty'><h3>{html.escape(title)}</h3>"
            f"<p class='muted small'>{html.escape(msg)}</p></div>")


def _custom_enrichment_section(project: Path) -> str:
    enr = project / "results" / "enrichment"
    figs = project / "results" / "figures"
    summary_path = enr / "custom_enrichment_summary.txt"
    artifacts = (
        enr / "custom_ora.csv", enr / "custom_gsea.csv", summary_path,
        figs / "custom_enrichment_dotplot.png", figs / "custom_enrichment_dotplot.svg",
    )
    if not any(path.exists() for path in artifacts):
        return ""

    configured, source_lines = _custom_source_evidence(project)
    # A current run summary is authoritative: suppress stale optional artifacts when it explicitly
    # records that neither a GMT nor an annotation-table source was configured for this run.
    if configured is False:
        return ""

    summary_raw = _read(summary_path)
    evidence_prefixes = (
        "Custom gene sets (terms):", "Universe:", "Significant genes (ORA input):",
        "Custom GSEA ranking order:", "Custom GSEA exact-score ties:",
        "Custom GSEA duplicate canonical-ID collapse:", "Custom ORA terms:",
        "Custom GSEA sets:", "Custom enrichment failed:",
    )
    summary_lines = [line.strip() for line in summary_raw.splitlines()
                     if line.strip().startswith(evidence_prefixes)]
    if not summary_path.exists():
        summary_lines.append(
            "Custom enrichment reproducibility summary: unavailable; review "
            "results/enrichment/custom_enrichment_summary.txt before interpretation.")
    elif not summary_lines:
        summary_lines.append(
            "Custom enrichment reproducibility summary: present but no recognized set, source, "
            "result, or deterministic-ranking evidence could be read.")
    elif not all(any(line.startswith(prefix) for line in summary_lines) for prefix in (
            "Custom GSEA ranking order:", "Custom GSEA exact-score ties:",
            "Custom GSEA duplicate canonical-ID collapse:")):
        summary_lines.append(
            "Custom GSEA reproducibility evidence: the ranking, exact-tie, or duplicate-collapse "
            "record is incomplete in custom_enrichment_summary.txt.")
    evidence = source_lines + summary_lines
    evidence_html = ""
    if evidence:
        items = "".join(f"<li>{html.escape(line)}</li>" for line in evidence)
        evidence_html = ("<details class='howto enrichment-coverage' open>"
                         "<summary>Configured sources and reproducibility evidence</summary>"
                         f"<ul>{items}</ul></details>")

    figure = _fig(figs, "custom_enrichment_dotplot",
                  "Custom gene-set over-representation (ORA)")
    figure_html = f"<div class='panels'>{figure}</div>" if figure else ""
    ora = _custom_enrich_block(
        "Custom gene sets — over-representation (ORA)", enr / "custom_ora.csv", "ora",
        "No supplied custom gene set met the adjusted ORA criterion. This result is limited "
        "to the configured collection and tested-gene universe.",
    )
    gsea = _custom_enrich_block(
        "Custom gene sets — ranked-list enrichment (GSEA)", enr / "custom_gsea.csv", "gsea",
        "No supplied custom gene set met the adjusted GSEA criterion. This result is limited "
        "to the configured collection and ranked genes.",
    )
    return ("<section class='custom-enrichment' aria-labelledby='custom-enrichment-title'>"
            "<h3 id='custom-enrichment-title'>Custom gene-set enrichment</h3>"
            "<p class='muted small'>These analyses test only the gene sets supplied for this run; "
            "an empty result does not establish biological absence outside that collection.</p>"
            f"{evidence_html}{figure_html}{ora}{gsea}</section>")


def _enrichment_section(project: Path) -> str:
    enr = project / "results" / "enrichment"
    if not enr.exists():
        return ""
    figs = project / "results" / "figures"
    # A non-model organism without a Bioconductor OrgDb skips GO/disease enrichment but still writes
    # empty go_ora_*.csv; enrichment_summary.txt records the skip. Key the GO wording on it so an empty
    # GO block reads "not run for this organism" instead of the misleading "nothing passed the threshold"
    # (KEGG, which runs off a KEGG organism code, is unaffected and keeps the default wording).
    summ = enr / "enrichment_summary.txt"
    summary_raw = summ.read_text(encoding="utf-8", errors="replace") if summ.exists() else ""
    summary_txt = summary_raw.lower()
    go_skipped = "skip" in summary_txt and any(
        s in summary_txt for s in ("orgdb", "annotation database", "no bioconductor", "no orgdb"))
    go_msg = ("GO enrichment was not run for this organism — no annotation database is available."
              if go_skipped else "No terms passed the significance threshold.")
    evidence_prefixes = (
        "Eligible ID mapping keytypes:", "Identifier routing policy:",
        "Accepted ID mapping routes:",
        "Tested input IDs retained after mapping/exclusion:",
        "Significant input IDs retained after mapping/exclusion:",
        "Up-regulated input IDs retained after mapping/exclusion:",
        "Down-regulated input IDs retained after mapping/exclusion:",
        "Mapped tested-gene universe", "GO effective annotated ORA universes:",
        "DO effective annotated ORA universe:", "OrgDb annotation identity:",
        "KEGG identity verification:", "KEGG retrieval:",
        "KEGG effective resource universe:", "KEGG supported foreground:",
        "KEGG eligible hypotheses/gene sets:", "KEGG adjusted results:",
        "KEGG resource status:", "Unmapped input IDs excluded:",
        "Ambiguous input IDs excluded:", "One-to-many mappings observed:",
        "Cross-keytype discordance observed:", "Many-to-one Entrez groups collapsed",
        "Direction-conflict Entrez IDs excluded:",
        "Source IDs present in both up/down inputs:",
        "Foreground intersection (up/down Entrez)", "Mapping interpretation gate:",
        "Direction-conflict gate:", "GO/DO annotation-resource status:",
        "Universe policy:", "ORA parameters:", "ORA multiple-testing families:",
        "GSEA parameters:", "GSEA ranking order:", "GSEA exact-score ties:",
        "GSEA duplicate canonical-ID collapse:",
        "Mapping limitation:",
    )
    evidence_lines = [line.strip() for line in summary_raw.splitlines()
                      if line.strip().startswith(evidence_prefixes)]
    coverage = ""
    if evidence_lines:
        items = "".join(f"<li>{html.escape(line)}</li>" for line in evidence_lines)
        coverage = ("<details class='howto enrichment-coverage' open>"
                    "<summary>Identifier mapping coverage and limitations</summary>"
                    f"<ul>{items}</ul></details>")
    # The per-ontology GO dotplot trio (BP/MF/CC) is meaningful only on the OrgDb/clusterProfiler
    # route. On the g:Profiler route only GO:BP is queried and it is already shown as the main
    # enrichment dotplot (the MF/CC tiles are "not queried" placeholders); on a no-OrgDb route the
    # whole trio is empty. Suppress it in both cases so the report never shows redundant or
    # placeholder GO panels next to the real dotplot.
    is_gprofiler = any(s in summary_txt for s in ("gost", "g:profiler", "gprofiler"))
    if "kegg resource status: not_interpretable" in summary_txt:
        kegg_msg = ("KEGG is not interpretable for this run; review the resource-integrity "
                    "evidence above before drawing pathway conclusions.")
    elif "kegg resource status: not_run" in summary_txt:
        kegg_msg = "KEGG was not run because no KEGG organism code was configured."
    elif any(status in summary_txt for status in (
            "kegg resource status: pass", "kegg resource status: limited_annotation")):
        kegg_msg = ("No supported KEGG pathways met the adjusted criterion. This does not "
                    "establish that no pathway biology is present.")
    else:
        # Backward-compatible wording for reports produced before the resource
        # integrity audit existed; do not invent an audit status from old files.
        kegg_msg = "No terms passed the significance threshold."
    if is_gprofiler or go_skipped:
        go_trio = ""
    else:
        # Each _fig() returns "" when its figure is absent, so the row degrades gracefully to
        # whichever categories were rendered.
        go_trio = "".join(f for f in (
            _fig(figs, "enrichment_go_BP_dotplot", "GO Biological Process — over-representation"),
            _fig(figs, "enrichment_go_MF_dotplot", "GO Molecular Function — over-representation"),
            _fig(figs, "enrichment_go_CC_dotplot", "GO Cellular Component — over-representation"),
        ) if f)
    blocks = (f"<div class='panels'>{go_trio}</div>") if go_trio else ""
    # GO over-representation: combined when present, else the up / down splits.
    if _enrich_rows(enr / "go_ora_all.csv", 1):
        blocks += _enrich_block("GO terms — over-representation", enr / "go_ora_all.csv", "ora")
    else:
        blocks += _enrich_block("GO terms — over-represented among up-regulated genes", enr / "go_ora_up.csv", "ora", empty_msg=go_msg)
        blocks += _enrich_block("GO terms — over-represented among down-regulated genes", enr / "go_ora_down.csv", "ora", empty_msg=go_msg)
    blocks += _enrich_block("GO gene-set enrichment (GSEA)", enr / "gsea.csv", "gsea", empty_msg=go_msg)
    blocks += _enrich_block("KEGG pathways — over-representation", enr / "kegg_ora.csv", "ora", empty_msg=kegg_msg)
    blocks += _enrich_block("KEGG pathways — gene-set enrichment (GSEA)", enr / "kegg_gsea.csv", "gsea", empty_msg=kegg_msg)
    blocks += _custom_enrichment_section(project)
    if not blocks:
        if not summ.exists():
            return ""
        has_kegg_audit = "kegg resource status:" in summary_txt
        if go_skipped and has_kegg_audit:
            msg = ("GO enrichment was not run because no annotation database was available. "
                   f"{kegg_msg}")
        elif has_kegg_audit:
            msg = f"No GO terms met the adjusted criterion. {kegg_msg}"
        elif go_skipped:
            msg = ("Functional enrichment was not run for this organism — no annotation "
                   "database is available.")
        else:
            msg = "No GO terms or supported KEGG pathways met their adjusted criteria for this run."
        return (f"<section id='enrichment'><h2>Functional enrichment</h2>{coverage}"
                f"<p class='muted'>{msg}</p></section>")
    # The "over-represented among the changed genes" framing is only accurate if some category
    # actually returned terms; when every block is empty/not-run, soften it to avoid implying a result.
    any_real = any(_enrich_rows(enr / f, 1) for f in (
        "go_ora_all.csv", "go_ora_up.csv", "go_ora_down.csv", "gsea.csv", "kegg_ora.csv", "kegg_gsea.csv"))
    plain = ('<div class="plain"><span class="tag">In plain terms</span>'
             '<p class="finding" style="font-size:1rem">These are the biological themes and pathways '
             'over-represented among the changed genes — they point to <i>what</i> the changes affect. '
             'Read each as a hypothesis to check, not a settled conclusion.</p></div>') if any_real else ""
    return (f"<section id='enrichment'><h2>Functional enrichment</h2>{plain}{coverage}{blocks}"
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


def _pretty_check_name(name: str) -> str:
    """Return a route-neutral display label for an internal check identifier."""
    if name == "09_deseq2_qc":
        return "09 differential-expression QC"
    return name.replace("_", " ")


def _sanity_section(text: str) -> str:
    if not text.strip():
        return ""
    overall, checks = _parse_sanity(text)
    if not checks:
        return f"<section id='sanity'><h2>Sanity checks</h2><pre>{html.escape(text)}</pre></section>"
    rows = ""
    for c in checks:
        msgs = "".join(f"<li>{html.escape(m)}</li>" for m in c["messages"])
        pretty = _pretty_check_name(c["name"])
        rows += (f"<tr><td class='chk-status'>{_badge(c['status'])}</td>"
                 f"<td><div class='chk-name'>{html.escape(pretty)}</div>"
                 f"<ul class='chk-msgs'>{msgs}</ul></td></tr>")
    note = ("<p class='muted small'>A <b>WARNING</b> is advisory — the run completed and the "
            "outputs are usable; it flags something to keep in mind (for example a small-replicate "
            "diagnostic). Only a <b>FAIL</b> blocks a run.</p>")
    return (f"<section id='sanity'><div class='sec-head'><h2>Sanity checks</h2>"
            f"{_badge(overall) if overall else ''}</div>{note}"
            f"<div class='tablewrap'><table class='checks'>{rows}</table></div></section>")


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
        facts.append(("Analysis job window (approx.)", wall))
    if cumulative:
        facts.append(("Cumulative analysis-job time", cumulative))
    if conf.get("snakemake_cores") is not None:
        facts.append(("CPU workers", str(conf.get("snakemake_cores"))))
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

    scope_html = ""
    if t.get("timing_scope"):
        scope_html = (
            "<p class='muted small'>"
            + html.escape(str(t["timing_scope"]))
            + " The GUI elapsed timer is the authority for the complete end-to-end run.</p>"
        )

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
            f"{scope_html}<h3>Time by phase</h3>{bars_html}{steps_html}</section>")


def _versions_table(run: dict) -> str:
    sw = run.get("software_versions", {}) or {}
    rp = run.get("r_packages", {}) or {}
    if _is_external_results(run):
        sw = {key: value for key, value in sw.items()
              if key in {"snakemake", "python", "Rscript"}}
        local_model_packages = {"DESeq2", "limma", "apeglm", "ashr", "edgeR", "tximport"}
        rp = {key: value for key, value in rp.items() if key not in local_model_packages}
    if not sw and not rp:
        return ""

    def rows(d: dict) -> str:
        return "".join(
            f"<tr><td>{html.escape(str(k))}</td><td class='mono'>{html.escape(str(v))}</td></tr>"
            for k, v in d.items())

    vhead = "<thead><tr><th scope='col'>Name</th><th scope='col'>Version</th></tr></thead>"
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


def _sample_composition(project: Path, num, den, run: dict | None = None) -> str | None:
    """Return replicate counts from configured input.samples for a local two-group analysis.

    Returns None (card omitted) when the file or the 'condition' column is missing, or when the two
    contrast levels are not both present as conditions — microarray, imported-results, and
    covariate/multi-level designs do not map cleanly to a single two-group count, so they degrade
    silently rather than show a wrong or partial number.
    """
    run = run or {}
    if _is_external_results(run):
        return None
    if not (num and den):
        return None
    configured = str((run.get("input", {}) or {}).get("samples") or "config/samples.tsv").strip()
    tsv = Path(configured)
    if not tsv.is_absolute():
        tsv = project / tsv
    if not tsv.exists():
        return None
    counts: dict[str, int] = {}
    try:
        with tsv.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if not reader.fieldnames or "condition" not in reader.fieldnames:
                return None
            for row in reader:
                c = (row.get("condition") or "").strip()
                if c:
                    counts[c] = counts.get(c, 0) + 1
    except (OSError, csv.Error):
        return None
    n_num, n_den = counts.get(str(num)), counts.get(str(den))
    if not n_num or not n_den:
        return None
    return f"{n_den} {den} · {n_num} {num}"


def _meta_cards(run: dict, project: Path) -> str:
    ref = run.get("reference", {}) or {}
    de = run.get("deseq2", {}) or {}
    wf = run.get("workflow", {}) or {}
    timing = _load_json(project / "results" / "reports" / "timing_summary.json")

    micro = run.get("microarray", {}) or {}
    is_micro = (run.get("input", {}) or {}).get("type") == "microarray"
    is_external = _is_external_results(run)
    cards: list[tuple[str, str]] = []

    organism = _org_clean(ref)
    if organism:
        strain = ref.get("strain")
        cards.append(("Organism", organism + (f" ({strain})" if strain and strain != "None" else "")))

    # Contrast: numerator vs denominator when known (matches the headline), else the name.
    num, den = _contrast_pair(run)
    if num and den:
        cards.append(("Contrast", f"{num} vs {den}"))
    elif not is_external:
        contrasts = de.get("contrasts") or []
        if contrasts and isinstance(contrasts, list):
            cards.append(("Contrast", str(contrasts[0].get("name", "—"))))
    if not is_external and de.get("design_formula"):
        cards.append(("Design", str(de.get("design_formula"))))
    comp = _sample_composition(project, num, den, run)
    if comp:
        cards.append(("Sample composition", comp))

    alpha = de.get("alpha")
    lfc = de.get("lfc_threshold")
    if is_external:
        provenance = _external_provenance(run)
        filters: list[str] = []
        if alpha is not None:
            filters.append(f"{_adjusted_p_name(run)} < {alpha}")
        if lfc is not None:
            filters.append(f"|log2FC|≥{lfc}")
        cards.append(("Result route", "Externally supplied differential-expression table"))
        if filters:
            cards.append(("Result filter", " · ".join(filters)))
        cards.append(("Upstream DE method", str(provenance.get("upstream_method") or "unknown")))
        cards.append(("Upstream LFC shrinkage", str(provenance.get("lfc_shrinkage") or "unknown")))
        cards.append(("Upstream p-adjustment", str(provenance.get("p_adjustment_method") or "unknown")))
    else:
        de_engine = _engine_name(run)
        thresholds = ""
        if alpha is not None:
            thresholds += f" · α={alpha}"
        effect_semantics = _effect_size_semantics(run)
        if lfc is not None and not effect_semantics:
            thresholds += f" · |log2FC|≥{lfc}"
        cards.append(("DE method", f"{de_engine}{thresholds}"))
        if effect_semantics:
            cutoff = effect_semantics.get("configured_absolute_log2fc_cutoff")
            estimate = effect_semantics.get("threshold_estimate")
            shrinkage = effect_semantics.get("shrinkage") or {}
            cards.append(("Configured effect cutoff", f"absolute {estimate} >= {cutoff}"))
            cards.append((
                "Realized LFC shrinkage",
                f"{shrinkage.get('realized_method') or 'not recorded'}; "
                f"{shrinkage.get('role') or 'role not recorded'}",
            ))

    # Microarray runs have no aligner/quantifier; surface the GEO platform + series instead.
    if is_micro:
        platform = micro.get("platform")
        gse = micro.get("gse_accession")
        if platform:
            cards.append(("Platform", str(platform)))
        if gse:
            cards.append(("GEO series", str(gse)))
    elif not is_external:
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
        cards.append(("Analysis window", wall))

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


def _meta_analysis_link(project: Path) -> str:
    """Additive multi-study block for the MAIN report: a compact card row + a link to the dedicated
    cross-study report. Returns "" (no effect) on single-study / non-meta runs."""
    summary = _load_json(project / "results" / "reports" / "meta_analysis_summary.json")
    if not summary or summary.get("n_meta_sig") is None:
        return ""
    conc = summary.get("direction_concordance_pct")
    cards = [
        ("Studies combined", summary.get("n_studies", "—")),
        ("Convergent meta-DEGs", f"{summary.get('n_sig_up', 0)} up · {summary.get('n_sig_down', 0)} down"),
        ("Direction concordance", f"{conc}%" if conc is not None else "—"),
        ("Pooling", str(summary.get("pooling", "—"))),
    ]
    inner = "".join(
        f"<div class='card'><div class='card-k'>{html.escape(k)}</div>"
        f"<div class='card-v'>{html.escape(str(v))}</div></div>" for k, v in cards)
    has_report = (project / "results" / "reports" / "meta_analysis_report.html").exists()
    link = ("<p><a class='xlink' href='meta_analysis_report.html'>Open the cross-study "
            "meta-analysis report →</a></p>") if has_report else ""
    body = (f"<p class='muted'>This run combined multiple studies. The per-study DESeq2 results below "
            f"are the joint fit; the cross-study meta-analysis (per-study DESeq2 → inverse-normal "
            f"p-combination + effect-size pooling) is summarised here.</p>"
            f"<div class='cards'>{inner}</div>{link}")
    return section("Multi-study meta-analysis", body, sid="meta")


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
.xlink{display:inline-block;margin-top:.4rem;font-family:var(--sans);font-weight:600;color:var(--brand-blue)}
.xlink:hover{color:var(--brand-blue-deep);text-decoration:underline;text-underline-offset:2px}
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
.hero h1{font-family:var(--serif);font-size:clamp(1.9rem,4vw,2.5rem);line-height:1.1;margin:.35rem 0 .15rem;
  overflow-wrap:anywhere}
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
.term:focus-visible{outline:2px solid var(--brand-blue);outline-offset:2px;border-radius:3px}
.term .tip{position:absolute;left:0;top:calc(100% + 9px);z-index:60;width:max-content;max-width:min(320px,82vw);
  background:var(--tip-bg);color:var(--tip-text);font-family:var(--sans);font-weight:400;font-size:.8rem;
  line-height:1.5;text-align:left;padding:10px 12px;border-radius:10px;box-shadow:var(--sh2);
  display:none;pointer-events:none}
.term:hover .tip,.term:focus-visible .tip{display:block}
.legend .li:nth-last-child(-n+2) .tip{left:auto;right:0}
/* "How to read this" — elaboration only, never the primary result */
.howto{margin-top:.6rem}
.howto>summary{cursor:pointer;list-style:none;font-family:var(--sans);font-weight:600;font-size:.82rem;
  color:var(--brand-blue-deep);display:inline-flex;align-items:center;gap:6px}
.howto>summary::-webkit-details-marker{display:none}
.howto>summary::before{content:"?";display:inline-flex;align-items:center;justify-content:center;width:18px;
  height:18px;border-radius:50%;background:var(--brand-teal);color:#04363a;font-weight:800;font-size:.72rem}
.howto>summary:focus-visible{outline:2px solid var(--brand-blue);outline-offset:2px;border-radius:3px}
.howto p{font-family:var(--sans);font-size:.82rem;color:var(--muted);line-height:1.55;margin:.45rem 0 0;
  border-left:3px solid var(--plain-border);background:var(--plain-bg);padding:.5rem .7rem;border-radius:0 6px 6px 0}
.enrichment-coverage li{overflow-wrap:anywhere}
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
.cards{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));margin:22px 0 6px}
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
.panels{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr))}
figure.panel{margin:0;border:1px solid var(--border);border-radius:var(--r2);overflow:hidden;
  background:var(--surface);display:flex;flex-direction:column;box-shadow:var(--sh)}
.panels>.panel[data-panel-layout="dense"]{grid-column:1/-1;min-width:0;width:100%;max-width:100%}
.panels>.panel[data-panel-layout="dense"] .frame{min-width:0;max-width:100%}
.panel[data-panel-layout="dense"] .dense-figure-viewport{overflow-x:auto;overflow-y:hidden;overscroll-behavior-inline:contain}
.panel[data-panel-layout="dense"] .dense-figure-viewport:focus-visible{outline:2px solid var(--brand-blue);outline-offset:-2px}
.panel[data-panel-layout="dense"] .dense-figure-viewport>.figbtn,
.panel[data-panel-layout="dense"] .dense-figure-viewport>.figbtn>img{min-width:var(--dense-min-width);max-width:none}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0,0,0,0);white-space:nowrap;border:0}
.panel .frame{position:relative;background:#fff;border-bottom:1px solid var(--border)}
.panel .lab{position:absolute;top:8px;left:8px;z-index:2;font-family:var(--sans);font-weight:800;font-size:.78rem;
  color:var(--text);background:#fff;border:2px solid var(--border-strong);border-radius:5px;
  width:22px;height:22px;display:flex;align-items:center;justify-content:center;box-shadow:var(--sh)}
.panel .figbtn{border:0;background:none;padding:0;margin:0;width:100%;display:block;cursor:zoom-in}
.panel .figbtn:focus-visible{outline:2px solid var(--brand-blue);outline-offset:-2px}
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
/* Compact legends can wrap any term to the left edge. Anchor every tooltip to the
   current legend box instead of guessing from term order, then centre a width that
   is derived from the available container space. */
@media(max-width:800px){
  .legend{position:relative}
  .legend .term{position:static}
  .legend .li .term .tip{left:50%;right:auto;top:calc(100% + 9px);
    width:min(320px,calc(100% - 24px));max-width:none;transform:translateX(-50%)}
}
@media(max-width:560px){
  .legend .li .term .tip{top:auto;bottom:calc(100% + 9px)}
}
/* glossary */
.glossary dl{margin:0;display:grid;gap:12px 18px;grid-template-columns:1fr}
.gterm{font-family:var(--sans);font-weight:700;font-size:.9rem;color:var(--brand-blue-deep)}
.gdef{font-family:var(--sans);font-size:.86rem;color:var(--text);line-height:1.5;margin:.15rem 0 0}
@media(min-width:720px){.glossary dl{grid-template-columns:180px 1fr;align-items:baseline}
  .gterm{grid-column:1} .gdef{grid-column:2;margin:0}}
.lb{position:fixed;inset:0;width:100%;height:100%;max-width:none;max-height:none;margin:0;border:0;
  padding:52px 28px 28px;background:transparent;overflow:auto;color:#e8e8f5;cursor:zoom-out}
.lb::backdrop{background:rgba(12,13,26,.86)}
.lb[open]{display:block}
.lb-stage{min-width:100%;min-height:calc(100dvh - 80px);display:grid;place-items:center}
.lb.is-zoomed .lb-stage{display:block;min-height:0;place-items:start}
.lb img{display:block;max-width:100%;max-height:calc(100dvh - 80px);margin:auto;background:#fff;
  border-radius:10px;box-shadow:0 12px 48px rgba(0,0,0,.5);cursor:zoom-in}
.lb img:focus-visible{outline:3px solid #fff;outline-offset:3px}
.lb img.zoomed{max-width:none;max-height:none;width:170%;margin:0;cursor:zoom-out}
.lb .hint{position:fixed;top:14px;left:52px;right:52px;text-align:center;color:#e8e8f5;
  font-family:var(--sans);font-size:.78rem;opacity:.85;pointer-events:none}
.lb-close{position:fixed;top:9px;right:12px;z-index:1;width:36px;height:36px;border:1px solid rgba(255,255,255,.55);
  border-radius:999px;background:#fff;color:#14151b;font:700 1.2rem/1 var(--sans);cursor:pointer}
.lb-close:focus-visible{outline:3px solid #fff;outline-offset:3px}
.enr-block{margin:0 0 1.3rem}
.enr-block h3{margin:.2rem 0 .5rem}
table.enr th.desc,table.enr td.desc{white-space:normal;min-width:190px;max-width:460px;text-align:left;font-family:var(--sans)}
.de-split{display:grid;gap:20px;grid-template-columns:minmax(0,1fr)}
.de-split h3{display:flex;align-items:center;gap:8px;margin-top:0}
.pill{font-family:var(--sans);font-size:.66rem;font-weight:700;padding:.1rem .45rem;border-radius:999px}
/* up=red, down=blue — echoes the volcano/figure direction palette (#C0392B / #2C7BB6). */
.pill.up{color:var(--up-ink);background:var(--up-bg)} .pill.down{color:var(--down-ink);background:var(--down-bg)}
.tablewrap{overflow-x:auto;border:1px solid var(--border);border-radius:8px}
table.data{border-collapse:collapse;width:100%;font-family:var(--sans);font-size:.83rem}
table.data th,table.data td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
table.data thead th{background:var(--accent-tint);color:var(--accent-2);font-weight:600;position:sticky;top:0}
table.sortable thead th{cursor:pointer;user-select:none}
table.sortable thead th:not([data-sort])::after{content:" \\21C5";opacity:.35}
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
/* Print / Save-as-PDF: scientists attach the report to lab notebooks and manuscripts. Open every
   disclosure, let table cells wrap instead of clipping in the overflow box, keep figures with their
   captions, preserve the direction/status colours, and drop the on-screen chrome. */
@media print{
  header.top{position:static}
  .chipnav,.skip,.lb,.howto>summary::before{display:none!important}
  details{display:block!important} details>summary{display:none!important}
  .tablewrap{overflow:visible!important}
  table.data th,table.data td{white-space:normal!important}
  .panel[data-panel-layout="dense"] .dense-figure-viewport{overflow:visible!important;max-width:none!important}
  .panel[data-panel-layout="dense"] .dense-figure-viewport>.figbtn,
  .panel[data-panel-layout="dense"] .dense-figure-viewport>.figbtn>img{width:100%!important;min-width:0!important;max-width:100%!important}
  section,figure.panel,.enr-block,.hstat,.card,.stat,tr{break-inside:avoid}
  .dirbar,.pill,.badge,.plain,.hstat .v,table.data thead th{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  a[href]{color:inherit}
  body{background:#fff}
}
"""


def section(title: str, body: str, pre: bool = False, sid: str = "") -> str:
    if not body.strip():
        return ""
    inner = f"<pre>{html.escape(body)}</pre>" if pre else body
    ida = f" id='{sid}'" if sid else ""
    return f"<section{ida}><h2>{html.escape(title)}</h2>{inner}</section>"


_MODE_LABEL = {"microarray": "Microarray", "fastq": "RNA-seq",
               "count_matrix": "Count matrix", "deseq2_results": "Provided differential-expression results"}


def _hero_findings(run: dict, project: Path, sanity_text: str, name: str) -> str:
    # Hero + Key Findings: kicker, H1, subtitle, teal plain-language finding, 3 headline
    # stat chips + up/down mini-bar, status badge + sentence, and the "how to read" details.
    inp = run.get("input", {}) or {}
    de = run.get("deseq2", {}) or {}
    unit = "genes/features" if inp.get("type") == "microarray" else "genes"
    alpha = float(de.get("alpha") or 0.05)
    lfc_t = _lfc_threshold(run, sanity_text)
    up, down, tested, top_up, top_down = _de_headline_stats(project, alpha, lfc_t)
    total = up + down
    num, den = _contrast_pair(run)
    is_external = _is_external_results(run)

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
        up_label = f"Higher in {num_lbl}"
        down_label = f"Lower in {num_lbl}"
        if is_external and not (num and den):
            up_label = "Positive log2FC"
            down_label = "Negative log2FC"
        chips = (
            '<div class="headline-stats">'
            f'<div class="hstat"><div class="v">{total:,}</div>'
            f'<div class="k">{unit.capitalize()} changed</div>'
            '<div class="dirbar" aria-hidden="true">'
            f'<span class="up" style="width:{up_pct:.1f}%"></span>'
            f'<span class="down" style="width:{down_pct:.1f}%"></span></div></div>'
            f'<div class="hstat up"><div class="v">&#9650;&nbsp;{up:,}</div>'
            f'<div class="k">{up_label}</div></div>'
            f'<div class="hstat down"><div class="v">&#9660;&nbsp;{down:,}</div>'
            f'<div class="k">{down_label}</div></div></div>')

    overall, _ = _parse_sanity(sanity_text)
    status = _status_sentence(sanity_text)
    status_line = ""
    if overall or status:
        badge = _badge(overall) if overall else ""
        status_line = f'<div class="status-line">{badge}<span>{html.escape(status)}</span></div>'

    direction_context = (f"in {html.escape(str(num))} relative to {html.escape(str(den))}"
                         if num and den else "for the recorded comparison")
    howto = (
        '<details class="howto"><summary>How to read this report</summary>'
        '<p>Each section opens with a <b>teal box</b> explaining the finding in plain language; the '
        'tables and figures below carry the full numbers — nothing is simplified away. '
        '<span style="border-bottom:1px dotted var(--brand-teal);color:var(--plain-ink);font-weight:600">'
        'Dotted-underlined</span> terms show a definition on hover or keyboard focus, and every one is '
        'collected in the Glossary at the end. Direction is colour-coded throughout: '
        f'<b style="color:var(--up-ink)">&#9650; red = higher</b>, '
        f'<b style="color:var(--down-ink)">&#9660; blue = lower</b> {direction_context}.</p></details>')

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


_STRANDEDNESS_LABELS = {0: "unstranded", 1: "forward", 2: "reverse"}


def _realized_strandedness_text(run: dict) -> str | None:
    """Render only the validated realized record; never fall back to configuration."""
    provenance = run.get("strandedness")
    realized = provenance.get("realized") if isinstance(provenance, dict) else None
    if not isinstance(realized, dict):
        return None
    code = realized.get("code")
    label = realized.get("label")
    path = realized.get("path")
    if (isinstance(code, bool) or code not in _STRANDEDNESS_LABELS
            or label != _STRANDEDNESS_LABELS[code] or not isinstance(path, str)
            or not path.strip()):
        return None
    return f"{label} ({code}; realized from {path})"


def _effect_size_semantics(run: dict) -> dict | None:
    """Return the recorded raw-cutoff/shrinkage contract when it is complete."""
    semantics = run.get("effect_size_semantics")
    if not isinstance(semantics, dict):
        return None
    estimate = semantics.get("threshold_estimate")
    shrinkage = semantics.get("shrinkage")
    if not estimate or not isinstance(shrinkage, dict):
        return None
    return semantics


def _study_design_section(run: dict) -> str:
    de = run.get("deseq2", {}) or {}
    ref = run.get("reference", {}) or {}
    inp = run.get("input", {}) or {}
    is_micro = inp.get("type") == "microarray"
    is_uploaded_results = inp.get("type") == "deseq2_results"
    num, den = _contrast_pair(run)
    engine = _engine_name(run)
    design = de.get("design_formula")
    if is_uploaded_results:
        provenance = _external_provenance(run)
        direction = inp.get("deseq2_results_direction") or {}
        confirmed = isinstance(direction, dict) and direction.get("confirmed") is True
        confirmed_at = (str(direction.get("confirmed_at") or "").strip()
                        if isinstance(direction, dict) else "")
        semantics_confirmed = confirmed and bool(num and den)
        if semantics_confirmed:
            sentence = ("This report uses externally supplied differential-expression results. "
                        f"Positive log2 fold change means higher expression in "
                        f"<b>{html.escape(str(num))}</b> (the confirmed numerator) than "
                        f"<b>{html.escape(str(den))}</b> (the confirmed denominator).")
        else:
            sentence = ("This report uses externally supplied differential-expression results; "
                        "group labels are not interpreted because numerator/denominator semantics "
                        "were not confirmed.")
        confirmation = "confirmed" if semantics_confirmed else "not confirmed"
        if semantics_confirmed and confirmed_at:
            confirmation += f" at {html.escape(confirmed_at)}"
        columns = provenance.get("column_names")
        column_text = ", ".join(str(value) for value in columns) if isinstance(columns, list) else ""
        selected = (
            f"gene ID={provenance.get('gene_id_column') or 'not recorded'}; "
            f"log2 fold change={provenance.get('log2fc_column') or 'not recorded'}; "
            f"adjusted p-value={provenance.get('adjusted_p_column') or 'not recorded'}"
        )
        rows = [
            ("Original basename", provenance.get("original_basename") or "not recorded"),
            ("Import timestamp", provenance.get("imported_at") or "not recorded"),
            ("Project copy", provenance.get("project_copy") or inp.get("deseq2_results") or "not recorded"),
            ("Project-copy SHA-256", provenance.get("sha256") or "not recorded"),
            ("Project-copy byte size", provenance.get("byte_size") if provenance.get("byte_size") is not None else "not recorded"),
            ("Imported rows", provenance.get("row_count") if provenance.get("row_count") is not None else "not recorded"),
            ("Imported columns", column_text or "not recorded"),
            ("Selected columns", selected),
            ("Upstream differential-expression method", provenance.get("upstream_method") or "unknown"),
            ("Upstream LFC shrinkage", provenance.get("lfc_shrinkage") or "unknown"),
            ("Upstream p-adjustment method", provenance.get("p_adjustment_method") or "unknown"),
            ("Numerator/denominator semantics", confirmation),
            ("Local route behavior", "No read processing, alignment, count quantification, local DE model, or local LFC shrinkage was run"),
        ]
        body = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td class='mono'>{html.escape(str(v))}</td></tr>"
            for k, v in rows)
        details = ("<details class='howto'><summary>Imported-results provenance</summary>"
                   "<div class='tablewrap' style='margin-top:.5rem'><table class='data'>"
                   f"<tbody>{body}</tbody></table></div></details>")
        return section("Imported-results provenance", f"<p>{sentence}</p>{details}", sid="design")
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
    effect_semantics = _effect_size_semantics(run)
    if effect_semantics:
        cutoff = effect_semantics.get("configured_absolute_log2fc_cutoff")
        estimate = effect_semantics.get("threshold_estimate")
        shrinkage = effect_semantics.get("shrinkage") or {}
        add("Configured effect cutoff", f"absolute {estimate} >= {cutoff}")
        add(
            "Effect-cutoff scope",
            "up/down differential-expression gene sets and enrichment input sets derived from them",
        )
        add("Realized LFC shrinkage", shrinkage.get("realized_method") or "not recorded")
        add("Shrinkage role", shrinkage.get("role") or "not recorded")
    # DESeq2 and limma both control the false-discovery rate with Benjamini-Hochberg by default;
    # state it so the multiple-testing method is on the record, not only in the glossary.
    # Scoped to differential expression on purpose: the enrichment tables are corrected
    # per route (BH for clusterProfiler, g:SCS for g:Profiler) and say so in their own
    # column headers, so an unqualified label here would misstate one of them.
    add("Multiple-testing correction (differential expression)", "Benjamini-Hochberg (FDR)")
    if not is_micro:
        strandedness_text = _realized_strandedness_text(run)
        if strandedness_text:
            add("Realized strandedness", strandedness_text)
    custom = run.get("customized_parameters", {}) or {}
    for pkey, pv in custom.items():
        if isinstance(pv, dict) and not isinstance(pv.get("used"), (list, dict)):
            label = pkey
            if is_micro and pkey == "deseq2.reference_level.condition":
                label = "Comparison reference level"
            add(label, pv.get("used"))

    details = ""
    if rows:
        body = "".join(
            f"<tr><td>{html.escape(k)}</td><td class='mono'>{html.escape(v)}</td></tr>"
            for k, v in rows)
        details = ("<details class='howto'><summary>Full configuration</summary>"
                   "<div class='tablewrap' style='margin-top:.5rem'><table class='data'>"
                   f"<tbody>{body}</tbody></table></div></details>")
    return (f"<section id='design'><h2>Study design</h2><p>{sentence}</p>{details}</section>")


# Microarray (limma on intensities) computes no counts, no variance-stabilising transform, and no LFC
# shrinkage, so the count/DESeq2 wording in the shared figure tech captions is wrong on that route.
# Rewrite it to the intensity/limma equivalents (the figures themselves are already labelled correctly).
_MICRO_TECH_SUBS = (
    ("variance-stabilised counts", "log2 expression (array intensity)"),
    ("mean normalised counts (log)", "mean log2 expression"),
    ("shrunken log2 fold change", "log2 fold change (limma, unshrunken)"),
)

_MICRO_HOWTO_SUBS = (
    ("lowest-count genes", "genes with the lowest measured expression"),
    ("low-count genes", "genes with low measured expression"),
)


def _micro_tech(tech: str) -> str:
    for old, new in _MICRO_TECH_SUBS:
        tech = tech.replace(old, new)
    return tech


def _micro_howto(howto: str) -> str:
    for old, new in _MICRO_HOWTO_SUBS:
        howto = howto.replace(old, new)
    return howto


def _external_figure_copy(basename: str, tech: str, howto: str, run: dict) -> tuple[str, str]:
    """Describe source-supplied statistics without assigning a local model or method."""
    if basename == "pvalue_histogram":
        return (
            "Distribution of source-supplied raw p-values across genes in the project copy.",
            "This distribution describes the supplied table. A spike near zero can reflect upstream "
            "signal; irregular shapes can reflect upstream modelling or filtering. BulkSeq Studio "
            "did not fit that model.",
        )
    if basename == "volcano":
        p_name = html.escape(_adjusted_p_name(run))
        return (
            f"x: source-supplied log2 fold change; y: −log10 {p_name}. Dashed guides mark "
            "the report's adjusted-p and fold-change cut-offs.",
            "Every dot is a source-table gene. Left–right is the supplied effect direction and "
            "height reflects the supplied adjusted p-value; the upstream model is not inferred.",
        )
    if basename == "ma_plot":
        shrinkage = _recorded_method(run, "lfc_shrinkage")
        if shrinkage == "applied":
            effect = "source-supplied log2 fold change (upstream shrinkage recorded as applied)"
        elif shrinkage == "not_applied":
            effect = "source-supplied log2 fold change (upstream shrinkage recorded as not applied)"
        else:
            effect = "source-supplied log2 fold change (upstream shrinkage state unknown)"
        return (
            f"x: source-supplied mean-expression measure (log scale); y: {effect}.",
            "This plots supplied effect sizes against the supplied mean-expression measure. "
            "BulkSeq Studio did not fit the upstream model or alter the effect signs.",
        )
    return tech, howto


def _panel(
    figs: Path,
    basename: str,
    letter: str,
    cap_lead: str = "",
    is_micro: bool = False,
    run: dict | None = None,
) -> str:
    # One grouped, letter-chipped figure panel. Preserves the exact zoom contract:
    # button.figbtn > img, onclick bsqZoom(this) reads querySelector('img').
    src = _fig_src(figs, basename)
    if not src:
        return ""
    _grp, title, lead, tech, howto = FIG[basename]
    if is_micro:
        tech = _micro_tech(tech)
        howto = _micro_howto(howto)
    if run and _is_external_results(run):
        tech, howto = _external_figure_copy(basename, tech, howto, run)
    if run:
        howto = _directional_figure_copy(basename, howto, run)
    lead = cap_lead or lead
    alt = html.escape(title)
    panel_attrs = _panel_attrs(basename)
    frame_open = _panel_frame_open(basename, title)
    return (
        f'<figure {panel_attrs}>{frame_open}'
        f'<span class="lab" aria-hidden="true">{letter}</span>'
        f'<button class="figbtn" type="button" onclick="bsqZoom(this)" '
        f'aria-label="Open {alt} full size"><img alt="{alt}" src="{src}"/></button></div>'
        f'<figcaption><div class="cap-lead">{lead}</div>'
        f'<div class="cap-tech">{tech}</div>'
        f'<details class="howto"><summary>How to read this</summary><p>{howto}</p></details>'
        '</figcaption></figure>')


def _figure_groups(
    figs: Path,
    up: int,
    down: int,
    unit: str,
    is_micro: bool = False,
    run: dict | None = None,
) -> str:
    total = up + down
    dyn = {}
    if total > 0:
        dyn["volcano"] = f"{up:,} {unit} rose and {down:,} fell past the cut-off."
        dyn["top_upregulated_heatmap"] = (
            f"The {min(up, 50):,} most statistically supported increases, sample by sample."
        )
        dyn["top_downregulated_heatmap"] = (
            f"The {min(down, 50):,} most statistically supported decreases, sample by sample."
        )
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    idx = 0
    out: list[str] = []
    for gkey, gtitle, gsub in FIG_GROUPS:
        panels: list[str] = []
        for basename, meta in FIG.items():
            if meta[0] != gkey:
                continue
            if run and _is_external_results(run) and basename not in {
                "pvalue_histogram", "volcano", "ma_plot", "enrichment_dotplot",
                "enrichment_kegg_dotplot", "ppi_network",
            }:
                continue
            letter = letters[idx] if idx < len(letters) else str(idx + 1)
            p = _panel(
                figs, basename, letter, dyn.get(basename, ""), is_micro=is_micro, run=run)
            if not p:
                continue
            panels.append(p)
            idx += 1
        if not panels:
            continue
        out.append(f'<h3 class="figgroup">{gtitle}</h3>'
                   f'<p class="figgroup-sub">{gsub}</p>'
                   f'<div class="panels">{"".join(panels)}</div>')
    if not out:
        return ""
    intro = ('<p class="muted small">Click any panel to open it full size and zoom — figures '
             'embed as vector SVG where practical, so they stay sharp.</p>')
    return f'<section id="figures"><h2>Figures</h2>{intro}{"".join(out)}</section>'


def _de_section(
    project: Path,
    up: int,
    down: int,
    num,
    den,
    unit: str,
    run: dict | None = None,
) -> str:
    input_type = (run or {}).get("input", {}).get("type")
    base_mean_kind = "expression" if input_type in {"microarray", "deseq2_results"} else "count"
    split = _de_split(project, base_mean_kind=base_mean_kind)
    if not split:
        return ""
    total = up + down
    one = unit[:-1] if unit.endswith("s") else unit
    badge = f'<span class="badge muted">{total:,} {unit}</span>' if total else ""
    run = run or {}
    is_external = _is_external_results(run)
    gloss = _route_gloss(run)
    num_lbl = html.escape(str(num)) if num else "the treated group"
    den_lbl = html.escape(str(den)) if den else "the control group"
    if is_external and not (num and den):
        plain_finding = (
            f"Of the {unit} tested, <b>{up:,}</b> had <b class='up'>positive log2FC</b> and "
            f"<b>{down:,}</b> had <b class='down'>negative log2FC</b> in the supplied table. "
            "Group labels are not assigned because numerator/denominator semantics were not confirmed."
        )
        direction_legend = (
            '<span class="li"><span class="sw up"></span>&#9650; positive log2FC</span>'
            '<span class="li"><span class="sw down"></span>&#9660; negative log2FC</span>'
        )
    else:
        plain_finding = (
            f"Of the {unit} tested, <b>{up:,}</b> were clearly <b class='up'>higher</b> and "
            f"<b>{down:,}</b> clearly <b class='down'>lower</b> in {num_lbl} than in {den_lbl}. "
            "The biggest movers in each direction are listed below — useful as leads to follow up."
        )
        direction_legend = (
            f'<span class="li"><span class="sw up"></span>&#9650; higher in {num_lbl}</span>'
            f'<span class="li"><span class="sw down"></span>&#9660; lower in {num_lbl}</span>'
        )
    plain = (
        '<div class="plain"><span class="tag">In plain terms</span>'
        f'<p class="finding" style="font-size:1rem">{plain_finding}</p></div>')
    p_label = "FDR (padj)" if not is_external else f"{_adjusted_p_name(run)} (padj)"
    legend = (
        '<div class="legend" aria-label="How to read the columns">'
        f'{direction_legend}'
        f'<span class="li">{_term("log2fc", "log2 fold change", gloss["log2fc"])}</span>'
        f'<span class="li">{_term("padj", html.escape(p_label), gloss["padj"])}</span>'
        f'<span class="li">{_term("basemean", "baseMean", gloss["basemean"])}</span></div>')
    adjusted_explanation = (
        "<b>padj</b> is the source-supplied adjusted p-value"
        if is_external else "<b>padj</b> is confidence"
    )
    if is_external:
        mean_explanation = "<b>baseMean</b> is a source-supplied mean-expression measure"
    elif input_type == "microarray":
        mean_explanation = "<b>baseMean</b> is mean normalized log2 expression intensity"
    else:
        mean_explanation = "<b>baseMean</b> is the mean normalized read count"
    howto = (
        '<details class="howto"><summary>How to read this table</summary>'
        f'<p>Each row is one {one}. <b>log2FC</b> is the size and direction of the change '
        f'(red + = higher, blue &minus; = lower); {adjusted_explanation} (smaller = stronger, '
        f'and every {one} here is below &alpha;); {mean_explanation} — a big fold '
        'change on a very low baseMean is worth checking before trusting it. Click or press Enter on a header to sort. '
        'Full lists: <code>results/deseq2/upregulated_genes.csv</code> and '
        '<code>downregulated_genes.csv</code>.</p></details>')
    return (f'<section id="de"><div class="sec-head"><h2>Which genes changed</h2>{badge}</div>'
            f'{plain}{legend}{split}{howto}</section>')


def _goi_section(project: Path, run: dict | None = None) -> str:
    # Custom gene-of-interest list (config/gene_sets.custom_gene_list): focused heatmap,
    # per-gene expression, log2FC bar chart, and a DESeq2 slice table. Every piece is optional
    # (the rule only runs when a gene list is configured), so each embeds only if present and
    # the whole section vanishes via section()'s empty-body check when nothing exists.
    run = run or {}
    is_external = _is_external_results(run)
    figs = project / "results" / "figures"
    figure_specs = [("goi_log2fc", "Genes of interest — source-supplied log2 fold change")]
    if not is_external:
        figure_specs = [
            ("goi_heatmap", "Genes of interest — focused heatmap across samples"),
            ("goi_expression", "Genes of interest — per-gene expression across conditions"),
            ("goi_log2fc", "Genes of interest — log2 fold change"),
        ]
    figures = "".join(
        figure for basename, caption in figure_specs
        if (figure := _fig(figs, basename, caption)))
    fig_html = f"<div class='panels'>{figures}</div>" if figures else ""
    result_label = "differential-expression" if is_external else "DESeq2"
    table = _de_table(project / "results" / "genes_of_interest" / "goi_deseq2_results.csv", top=25,
                      empty_msg=f"No genes-of-interest {result_label} results available.")
    table_html = "" if table.startswith("<p class='muted small'>No genes-of-interest") else (
        f"<h3>Genes of interest — {result_label} results</h3>{table}"
        f"<p class='muted small'>Full table: "
        f"<code>results/genes_of_interest/goi_deseq2_results.csv</code>.</p>")
    body = fig_html + table_html
    return body


def _glossary_section(run: dict | None = None) -> str:
    run = run or {}
    definitions = _route_gloss(run)
    order = list(GLOSS_ORDER)
    if _is_external_results(run):
        order[0] = (f"{_adjusted_p_name(run)} (padj)", "padj")
    items = "".join(
        f'<div class="gterm">{html.escape(label)}</div>'
        f'<div class="gdef">{html.escape(definitions[key])}</div>'
        for label, key in order if key in definitions)
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
    is_micro = inp.get("type") == "microarray"
    unit = "genes/features" if is_micro else "genes"
    alpha = float(de.get("alpha") or 0.05)
    lfc_t = _lfc_threshold(run, sanity)
    up, down, _tested, _tu, _td = _de_headline_stats(project, alpha, lfc_t)
    num, den = _contrast_pair(run)

    hero_findings = _hero_findings(run, project, sanity, name)
    meta_cards = _meta_cards(run, project)
    meta_link = "" if _is_external_results(run) else _meta_analysis_link(project)
    study = _study_design_section(run)
    figures = _figure_groups(figs, up, down, unit, is_micro=is_micro, run=run)
    de_html = _de_section(project, up, down, num, den, unit, run=run)
    goi_html = section("Genes of interest", _goi_section(project, run), sid="goi")
    enrichment = _enrichment_section(project)
    runtime = _timing_section(timing)
    sanity_html = _sanity_section(sanity)
    versions = _versions_table(run)
    glossary = _glossary_section(run)

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
{meta_link}
{study}
{figures}
{de_html}
{goi_html}
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
<dialog id="bsq-lb" class="lb" aria-label="Figure viewer">
<button id="bsq-lb-close" class="lb-close" type="button" autofocus aria-label="Close figure viewer">&times;</button>
<span class="hint">Click image to zoom · click background or press Esc to close</span>
<div class="lb-stage"><img id="bsq-lb-img" alt="" tabindex="0"></div></dialog>
<script>
function bsqResetZoom(){{var lb=document.getElementById('bsq-lb');var li=document.getElementById('bsq-lb-img');li.classList.remove('zoomed');lb.classList.remove('is-zoomed');lb.scrollLeft=0;lb.scrollTop=0;}}
function bsqFinishClose(){{var lb=document.getElementById('bsq-lb');if(lb.open)return;var li=document.getElementById('bsq-lb-img');var trigger=window._bsqTrig;window._bsqTrig=null;bsqResetZoom();li.removeAttribute('src');if(trigger&&trigger.isConnected)trigger.focus();}}
function bsqZoom(btn){{var img=btn.querySelector('img');var lb=document.getElementById('bsq-lb');var li=document.getElementById('bsq-lb-img');if(!img||!lb||typeof lb.showModal!=='function')return;bsqResetZoom();li.src=img.src;li.alt=img.alt||'';li.setAttribute('aria-label',img.alt||'Figure');window._bsqTrig=btn;lb.showModal();document.getElementById('bsq-lb-close').focus();}}
function bsqClose(){{var lb=document.getElementById('bsq-lb');if(lb.open)lb.close();bsqFinishClose();}}
(function(){{var lb=document.getElementById('bsq-lb');var li=document.getElementById('bsq-lb-img');var close=document.getElementById('bsq-lb-close');
close.addEventListener('click',bsqClose);
lb.addEventListener('click',function(e){{if(e.target===lb)bsqClose();}});
li.addEventListener('click',function(e){{var zoomed=this.classList.toggle('zoomed');lb.classList.toggle('is-zoomed',zoomed);lb.scrollLeft=0;lb.scrollTop=0;e.stopPropagation();}});
lb.addEventListener('keydown',function(e){{if(e.key!=='Tab')return;var items=[close,li];var first=items[0],last=items[items.length-1];if(e.shiftKey&&(document.activeElement===first||!lb.contains(document.activeElement))){{e.preventDefault();last.focus();}}else if(!e.shiftKey&&(document.activeElement===last||!lb.contains(document.activeElement))){{e.preventDefault();first.focus();}}}});
lb.addEventListener('close',bsqFinishClose);
}})();
// Sortable tables: click a header to sort; numeric columns sort numerically. Third
// click restores the original order. Purely client-side, no dependencies.
(function(){{
  function cellVal(cell){{if(!cell)return '';var v=cell.getAttribute('data-sort-value');return v!==null?v:(cell.textContent||'');}}
  function cmp(a,b){{var x=parseFloat(a),y=parseFloat(b);
    if(!isNaN(x)&&!isNaN(y))return x-y; return a.localeCompare(b);}}
  document.querySelectorAll('table.sortable').forEach(function(tbl){{
    var tb=tbl.tBodies[0]; if(!tb)return;
    var orig=Array.prototype.slice.call(tb.rows);
    tbl.querySelectorAll('thead th').forEach(function(th,ci){{
      th.tabIndex=0; th.setAttribute('role','button'); th.setAttribute('aria-sort','none');
      th.title='Sort by '+(th.textContent||'').trim();
      function doSort(){{
        var st=th.getAttribute('data-sort'); var next=st==='asc'?'desc':(st==='desc'?'none':'asc');
        tbl.querySelectorAll('thead th').forEach(function(o){{o.removeAttribute('data-sort');o.setAttribute('aria-sort','none');}});
        var rows=Array.prototype.slice.call(tb.rows);
        if(next==='none'){{orig.forEach(function(r){{tb.appendChild(r);}});return;}}
        rows.sort(function(r1,r2){{
          var c=cmp(cellVal(r1.cells[ci]),cellVal(r2.cells[ci]));
          return next==='asc'?c:-c;}});
        rows.forEach(function(r){{tb.appendChild(r);}});
        th.setAttribute('data-sort',next);
        th.setAttribute('aria-sort',next==='asc'?'ascending':'descending');}}
      th.addEventListener('click',doSort);
      th.addEventListener('keydown',function(e){{if(e.key==='Enter'||e.key===' '){{e.preventDefault();doSort();}}}});
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
