#!/usr/bin/env python3
"""Build the cross-study HTML report from recorded results and the methods ledger."""
from __future__ import annotations

import argparse
import csv
import html
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_html_report as R  # shared CSS, LOGO_SVG, _fig, _load_json, _badge, section, URLs

METHOD = "combined_concordance_and_pooled_full_family_bh_v1"


def _num(v, digits=1):
    try:
        f = float(v)
        if not math.isfinite(f):
            return "—"
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
    th = "".join(f"<th scope='col'>{html.escape(h)}</th>" for h in head)
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


def _hero(summary: dict, meta_status: str, call_alpha: str | None) -> str:
    k = summary
    studies = _num(k.get("n_studies"))
    cards = [
        ("Studies combined", studies),
        ("Retained genes", _num(k.get("n_shared_genes"))),
        ("Combined FDR + matching sign", f"{_num(k.get('n_sig_up'))} up · {_num(k.get('n_sig_down'))} down"),
        ("Opposite or zero signs", _num(k.get("n_discordant"))),
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
    alpha = f"the recorded {call_alpha} threshold" if call_alpha else "a threshold not recorded with this result"
    noun = "gene" if n_sig == 1 else "genes"
    verb = "has" if n_sig == 1 else "have"
    lead = (f"Across {studies} studies, {_num(n_sig)} {noun} {verb} combined-p FDR below {alpha} "
            "and matching nonzero effect signs. This criterion does not require each study to "
            "be individually significant and does not guarantee independent replication. "
            + (f"The reported {_num(k.get('n_discordant'))} discordant rows include opposite and zero signs. "
               if k.get("n_discordant") else "")
            + (f"Matching signs occur in {_num(conc)}% of retained shared rows."
               if conc is not None else ""))
    badge = R._badge(meta_status) if meta_status else ""
    return (f"<section id='findings' class='hero'><div class='eyebrow'>Multi-study meta-analysis {badge}</div>"
            f"<p class='lead'>{html.escape(lead)}</p><div class='cards'>{inner}</div></section>")


def _fig_row(figs: Path, items: list[tuple[str, str, str, list[tuple[str, Path]]]],
             reports: Path) -> str:
    blocks = []
    for base, title, description, sources in items:
        panel = R._fig(figs, base, title)
        if not panel:
            continue
        links = " · ".join(
            f"<a href='{html.escape(Path('..', '..', *path.relative_to(reports.parent.parent).parts).as_posix(), quote=True)}'>{html.escape(label)}</a>"
            for label, path in sources if path.is_file())
        source_note = f"<p>Complete source data: {links}.</p>" if links else "<p>Source data unavailable in this result folder.</p>"
        panel = panel.replace("</figcaption>", f"<p>{html.escape(description)}</p>{source_note}</figcaption>")
        blocks.append(panel)
    blocks = [b for b in blocks if b]
    return f"<div class='panels'>{''.join(blocks)}</div>"


def _corrected_ledger(ledger: dict) -> bool:
    return R._corrected_meta_ledger(ledger)


def _recorded_text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _call_alpha(ledger: dict) -> str | None:
    value = R._mapping(ledger.get("execution")).get("combined_alpha")
    return str(value) if type(value) in (int, float) and math.isfinite(value) and 0 < value <= 1 else None


def _study_scope(ledger: dict) -> str:
    execution = R._mapping(ledger.get("execution"))
    factor = _recorded_text(execution.get("contrast_factor"))
    numerator = _recorded_text(execution.get("numerator"))
    denominator = _recorded_text(execution.get("denominator"))
    formula = _recorded_text(execution.get("per_study_formula"))
    if factor and numerator and denominator:
        comparison = f"{html.escape(numerator)} versus {html.escape(denominator)} ({html.escape(factor)})"
        direction = (f"Positive log2 fold change means higher expression in {html.escape(numerator)} "
                     f"than {html.escape(denominator)}.")
    else:
        comparison = "Comparison orientation not recorded"
        missing = ", ".join(name for name, value in (("factor", factor), ("numerator", numerator),
                                                        ("denominator", denominator)) if not value)
        direction = f"The {html.escape(missing)} {'was' if ',' not in missing else 'were'} not recorded with this result; positive-effect direction cannot be established here."
    studies = R._mapping(ledger.get("studies"))
    inputs = studies.get("input") if isinstance(studies.get("input"), list) else None
    input_by_study = {row["study"]: row for item in (inputs or [])
                      if (row := R._mapping(item)) and isinstance(row.get("study"), str)}
    included = studies.get("included") if isinstance(studies.get("included"), list) else None
    excluded = studies.get("excluded") if isinstance(studies.get("excluded"), list) else None

    def arm_counts(study: str) -> str:
        row = input_by_study.get(study, {})
        first, second = row.get("numerator_samples"), row.get("denominator_samples")
        if type(first) is int and type(second) is int and first >= 0 and second >= 0:
            return (f"{first} {html.escape(numerator or 'numerator')}, "
                    f"{second} {html.escape(denominator or 'denominator')}")
        return "arm counts not recorded"

    if included is None:
        included_text = "Included studies were not recorded."
    elif not included:
        included_text = "No per-study fits were included."
    else:
        parts = []
        for item in included:
            row = R._mapping(item)
            study = _recorded_text(row.get("study"))
            if not study:
                continue
            post_filter = row.get("post_filter_rows")
            count = (f"; {post_filter} post-filter gene rows" if type(post_filter) is int and post_filter >= 0
                     else "; post-filter row count not recorded")
            parts.append(f"{html.escape(study)} ({arm_counts(study)}{count})")
        included_text = "; ".join(parts) if parts else "Included study records were unreadable."
    if excluded is None:
        excluded_text = "Excluded studies were not recorded."
    elif not excluded:
        excluded_text = "No excluded studies were recorded."
    else:
        parts = []
        for item in excluded:
            row = R._mapping(item)
            study = _recorded_text(row.get("study"))
            if not study:
                continue
            reason = _recorded_text(row.get("reason")) or "reason not recorded"
            parts.append(f"{html.escape(study)} ({arm_counts(study)}; {html.escape(reason)})")
        excluded_text = "; ".join(parts) if parts else "Excluded study records were unreadable."
    if ledger.get("design_supported") is False:
        fit = (f"The proposed per-study formula was <code>{html.escape(formula)}</code>. "
               if formula else "The proposed per-study formula was not recorded. ")
        fit += "The requested meta design was unsupported, so no per-study fit was performed."
    else:
        fit = (f"The recorded per-study formula was <code>{html.escape(formula)}</code>."
               if formula else "The executed per-study formula was not recorded.")
    return ("<section id='comparison' class='hero'>"
            f"<h1>Cross-study analysis: {comparison}</h1><p>{direction} {fit}</p>"
            f"<p><strong>Included studies:</strong> {included_text}</p>"
            f"<p><strong>Excluded studies:</strong> {excluded_text}</p>"
            "<p>These counts establish metadata eligibility for the selected comparison; they do not establish study independence, biological comparability, or post-run validity.</p></section>")


def _field_dictionary(summary: dict, ledger: dict) -> str:
    alpha = _call_alpha(ledger) or "not recorded with this result"
    lfc = html.escape(str(summary.get("per_study_lfc_threshold", "not recorded")))
    pooled = ("BH adjusted across every estimable pooled-effect test, including opposite-sign and neutral rows."
              if _corrected_ledger(ledger) else
              "The corrected adjustment is not recorded for this result; recompute before interpreting this value.")
    fields = [
        ("combined_padj", "Combined-p FDR from the inverse-normal p-value test, adjusted among matching-sign genes; a small value alone does not prove replication."),
        ("meta_sig", f"Combined-p FDR below the run threshold ({alpha}) with matching nonzero per-study effect signs; individual studies need not reach FDR."),
        ("common_direction", "Up or down means all retained effects have that sign; discordant includes opposite-sign and zero-effect rows."),
        ("n_studies_sig", f"Number of included studies with per-study adjusted p-value below the run threshold ({alpha}); this count does not apply the fold-change threshold."),
        ("rem_log2FC; rem_ci_lo/hi", "Pooled effect and its 95% confidence interval from the common-effect fit at two studies or DerSimonian–Laird random-effects fit at three or more."),
        ("rem_padj", pooled),
        ("tau2; I2; QEp", "Between-study heterogeneity fields are deliberately omitted for the two-study common-effect fit; omission is a workflow reporting policy."),
    ]
    definitions = "".join(f"<dt><code>{html.escape(name)}</code></dt><dd>{description}</dd>"
                          for name, description in fields)
    return ("<details id='field-guide'><summary>How to read the result fields</summary>"
            f"<p>The display-derived per-study up/down summary also requires the figure-setting absolute log2 fold change ≥ {lfc}; a non-significant study does not establish no effect.</p>"
            f"<dl>{definitions}</dl></details>")


def _method_note(ledger: dict) -> str:
    if not _corrected_ledger(ledger):
        return ("<section class='hero' role='alert'><h2>Legacy pooled-effect adjustment requires recomputation</h2>"
                "<p>This result lacks the recognized corrected method marker. Recompute the meta-analysis before "
                "interpreting <code>rem_padj</code>; this report does not relabel old values.</p></section>")
    ledger = R._mapping(ledger)
    genes = R._mapping(ledger.get("genes"))
    direction = R._mapping(genes.get("direction"))
    families = ledger["families"]
    combined = families["combined"]
    pooled = families["pooled"]
    combined_size = combined.get("size", "unknown")
    pooled_size = pooled.get("size", "unknown")
    return ("<details id='methods'><summary>Testing families and retained rows</summary>"
            f"<p>Combined-p BH: {html.escape(str(combined_size))} matching-sign {'test' if combined_size == 1 else 'tests'} "
            f"(<code>{html.escape(str(combined.get('id', 'unknown')))}</code>). Pooled-effect BH: "
            f"{html.escape(str(pooled_size))} estimable {'test' if pooled_size == 1 else 'tests'} across all directions "
            f"(<code>{html.escape(str(pooled.get('id', 'unknown')))}</code>). The pooled family includes "
            "opposite-sign and neutral rows; pooled significance does not decide the combined-p call.</p>"
            f"<p>Post-filter identifier intersection: {_num(genes.get('identifier_intersection'))}; "
            f"excluded for missing statistics (union): {_num(genes.get('exclusion_union'))}; "
            f"complete-case retained: {_num(genes.get('complete_case_retained'))}; pooled-fit "
            f"failures: {_num(genes.get('pooled_fit_failures'))}. Retained directions: "
            f"{_num(direction.get('up'))} up, {_num(direction.get('down'))} down, "
            f"{_num(direction.get('opposite_sign'))} opposite sign, {_num(direction.get('neutral'))} neutral. "
            "Missing-statistic reasons may overlap, so their counts must not be added.</p></details>")


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


def _per_study_reports(project: Path) -> str:
    # Link-out cards to each study's self-contained results/meta/per_study/<S>/index.html.
    # Never base64-embed these — the per-study figure sets can total tens of MB. Report lives
    # at results/reports/meta_analysis_report.html, so a manifest path (project-relative, e.g.
    # results/meta/per_study/<S>/index.html) needs "../../" prepended to resolve on disk.
    manifest = R._mapping(R._load_json(project / "results" / "meta" / "per_study" / "manifest.json"))
    studies = manifest.get("studies")
    if not isinstance(studies, list) or not studies:
        return "<p class='muted'>Per-study report pages were not available.</p>"
    cards = []
    for item in studies:
        s = R._mapping(item)
        if not s:
            continue
        sid = html.escape(str(s.get("study", "—")))
        href = html.escape("../../" + str(s.get("index", "")).replace("\\", "/"))
        stats = (f"{_num(s.get('n_tested'))} tested · {_num(s.get('n_up'))} up · "
                 f"{_num(s.get('n_down'))} down")
        cards.append(
            "<div class='card'>"
            f"<div class='card-k'>{sid}</div>"
            f"<div class='card-v'>{html.escape(stats)}</div>"
            f"<div style='margin-top:6px'><a href='{href}' target='_blank' rel='noopener'>"
            "Open study report ↗</a></div>"
            "</div>")
    return f"<div class='cards'>{''.join(cards)}</div>" if cards else "<p class='muted'>Per-study report pages were not available.</p>"


def _figure_items(project: Path, summary: dict, rows: list[dict[str, str]], ledger: dict):
    meta = project / "results" / "meta"
    main = meta / "meta_analysis_results.csv"
    studies = [key.removeprefix("study_").removesuffix("_log2FC")
               for key in (rows[0] if rows else {}) if key.startswith("study_") and key.endswith("_log2FC")]
    per_study = [(f"{study} full per-study table", meta / f"per_study_{study}.csv") for study in studies]
    sig = [row for row in rows if row.get("meta_sig", "").lower() == "true"]
    combined = [row for row in rows if _finite(row.get("combined_padj")) and _finite(row.get("rem_log2FC"))]
    hetero = [row for row in rows if _finite(row.get("I2")) and _finite(row.get("rem_log2FC"))]
    hist = [row for row in rows if _finite(row.get("combined_pvalue"))]
    forest = summary.get("n_forest_displayed")
    heatmap = summary.get("n_heatmap_displayed")
    alpha = str(summary.get("alpha", "unknown"))
    run_alpha = _call_alpha(ledger)
    try:
        threshold = float(alpha)
    except ValueError:
        threshold = None
    study_padj = [key for key in (rows[0] if rows else {})
                  if key.startswith("study_") and key.endswith("_padj")]
    per_sig = [any(_finite(row.get(key)) and float(row[key]) < threshold
                   for key in study_padj) if threshold is not None else False for row in rows]
    combined_sig = [row.get("meta_sig", "").lower() == "true" for row in rows]
    bar_counts = (sum(meta_call and not study_call for meta_call, study_call in zip(combined_sig, per_sig)),
                  sum(meta_call and study_call for meta_call, study_call in zip(combined_sig, per_sig)),
                  sum(not meta_call and study_call for meta_call, study_call in zip(combined_sig, per_sig)))
    setting_note = (f" The figure-setting alpha {alpha} differs from the recorded result-call alpha {run_alpha}; these bar classes reflect the figure setting."
                    if run_alpha and threshold is not None and threshold != float(run_alpha) else
                    " The original result-call alpha was not recorded with this result."
                    if not run_alpha else "")
    gain_detail = (f"{sum(bar_counts)} of {len(rows)} retained rows appear in the bars: {bar_counts[0]} combined only, {bar_counts[1]} both, {bar_counts[2]} per-study only; {len(rows) - sum(bar_counts)} meet neither criterion. This figure's per-study comparison uses its figure-setting FDR below {alpha} only; the separate per-study up/down summary also applies the fold-change threshold.{setting_note}"
                   if threshold is not None else
                   "The recorded per-study alpha is unavailable, so this legacy report cannot independently reconstruct the bar categories; inspect the source table and recompute before interpretation.")
    scatter_counts = []
    for i, first in enumerate(studies):
        for second in studies[i + 1:]:
            count = sum(_finite(row.get(f"study_{first}_log2FC")) and
                        _finite(row.get(f"study_{second}_log2FC")) for row in rows)
            scatter_counts.append(f"{first} versus {second}: {count}")
    enrichment_head, enrichment_rows = _csv_rows(meta / "meta_enrichment_plotted.csv")
    cluster_index = enrichment_head.index("Cluster") if "Cluster" in enrichment_head else None
    clusters = len({row[cluster_index] for row in enrichment_rows}) if cluster_index is not None else 0
    return [
        ("meta_volcano", "Combined evidence and pooled effect",
         f"{len(combined)} rows with a finite pooled log2 fold change and combined-p FDR are plotted; x is the pooled effect, y is −log10 combined-p FDR (or |combined Z| when that display option is selected). These are different tests. Rows without combined-p FDR are omitted from this axis.",
         [("all meta results", main)]),
        ("meta_forest", "Per-study and pooled effects",
         f"{forest if isinstance(forest, int) else 'A configured number of'} leading combined-FDR, matching-sign genes are selected; squares show per-study log2 fold changes and 95% intervals, diamonds the pooled estimates. The two-study common-effect fit assumes one underlying effect. Individual study intervals do not establish replication.",
         [("all meta results", main), *per_study]),
        ("meta_concordance_scatter", "Pairwise effect agreement",
         f"Finite per-study log2 fold changes are compared in {len(scatter_counts)} pairwise panels ({'; '.join(scatter_counts)} plotted points). Axes identify the study pair. Spearman correlation and matching signs describe the plotted rows, not the significance of either study.",
         [("all meta results", main)]),
        ("meta_convergent_heatmap", "Effects for combined-FDR, matching-sign genes",
         f"{heatmap if isinstance(heatmap, int) else 'A configured number of'} genes selected by combined-p FDR are plotted; columns are studies and colour represents signed per-study log2 fold change. The colour limit may clip extremes in the default display.",
         [("all meta results", main), ("full selected-gene table", meta / "meta_convergent_genes.csv")]),
        ("meta_enrichment_dotplot", "Cross-study GO enrichment",
         f"{len(enrichment_rows)} displayed term-by-set rows across {clusters} gene sets are recorded in the plotted-data table. The x axis names gene sets, the y axis names GO terms; dot size encodes gene ratio and fill encodes adjusted p-value. Inspect the complete enrichment table and accepted identifier mapping before comparing sets.",
         [("plotted rows", meta / "meta_enrichment_plotted.csv"),
          ("complete enrichment table", meta / "meta_enrichment_ora.csv"),
          ("identifier mapping", meta / "meta_enrichment_mapping.tsv")]),
        ("meta_heterogeneity", "Reported heterogeneity by pooled effect",
         f"{len(hetero)} rows with reported I² and finite pooled effect are plotted; x is absolute pooled log2 fold change and y is I². For two studies, the workflow uses a common-effect fit and deliberately omits heterogeneity fields; this is a reporting policy, not mathematical inestimability.",
         [("all meta results", main)]),
        ("meta_integration_gain", "Combined and per-study call overlap",
         f"{gain_detail} A combined-only call is not a replication claim.",
         [("all meta results", main)]),
        ("meta_combined_p_hist", "Combined-p distribution",
         f"{len(hist)} finite inverse-normal combined p-values are shown in 50 bins. This diagnostic includes all retained rows regardless of matching sign; the histogram does not show the adjusted testing family.",
         [("all meta results", main)]),
    ]


def _finite(value: str | None) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def build(project: Path) -> str:
    reports = project / "results" / "reports"
    figs = project / "results" / "figures"
    meta = project / "results" / "meta"
    name = project.resolve().name
    run = R._mapping(R._load_json(reports / "run_summary.json"))
    summary = R._mapping(R._load_json(reports / "meta_analysis_summary.json"))
    ledger = R._mapping(R._load_json(meta / "meta_eligibility.json"))
    result_head, result_rows = _csv_rows(meta / "meta_analysis_results.csv")
    result_dicts = [dict(zip(result_head, row)) for row in result_rows]
    meta_check = R.read_check(project / "checks" / "17_meta_analysis_qc.json")
    check_messages = meta_check["messages"]
    first_message = check_messages[0]
    status = meta_check["status"]
    check_note = ("<p role='alert'>Meta check evidence is unavailable or malformed; review the stored check record before relying on this report.</p>"
                  if not meta_check["valid"] else "")
    app_version = run.get("app_version") or ""
    ver_chip = f"<span class='ver'>v{html.escape(str(app_version))}</span>" if app_version else ""

    empty = not summary or (summary.get("n_shared_genes", 0) or 0) == 0 or (summary.get("n_meta_sig") is None)
    if empty:
        msg = (first_message.get("message", "") if meta_check["valid"] else
               "Meta check evidence is unavailable or malformed; inspect the stored check record.")
        genes = R._mapping(ledger.get("genes"))
        loss = ""
        if genes.get("identifier_intersection") == 0:
            loss = "No gene identifiers remain shared after per-study filtering."
        elif genes.get("complete_case_retained") == 0 and genes.get("identifier_intersection", 0) > 0:
            loss = "Shared identifiers exist, but no complete-case meta-analysis rows were retained; inspect the recorded missing-statistic losses."
        if loss and (not msg or not meta_check["valid"]):
            msg = f"{loss} {msg}".strip()
        body = (f"<section class='hero'><div class='eyebrow'>Multi-study meta-analysis {R._badge(status or 'FAIL')}</div>"
                f"<p class='lead'>The meta-analysis did not produce a shared-gene result.</p>"
                f"<p class='muted'>{html.escape(msg or 'No cross-study result was recorded; inspect the meta check and eligibility ledger.')}</p></section>")
    else:
        figures = R.section("Comparative figures", _fig_row(
            figs, _figure_items(project, summary, result_dicts, ledger), reports), sid="figures")
        ch, cr = _csv_rows(meta / "meta_convergent_genes.csv", limit=40)
        if not _corrected_ledger(ledger) and "rem_padj" in ch:
            index = ch.index("rem_padj")
            ch = ch[:index] + ch[index + 1:]
            cr = [row[:index] + row[index + 1:] for row in cr]
        shown_head = [f"n_studies_sig / {summary.get('n_studies', 'k')} (run FDR < {_call_alpha(ledger) or 'not recorded'})"
                      if column == "n_studies_sig" else column for column in ch]
        conv = R.section("Combined-FDR, matching-sign genes (first 40)",
                         "<p>The study count uses per-study adjusted p-values only; the per-study up/down summary also applies the configured absolute fold-change threshold. A non-significant study is not evidence of no effect.</p>"
                         + _table(shown_head, cr, italic_col=1 if ch and ch[1] == "gene_symbol" else None,
                                num_cols={i for i, h in enumerate(ch) if any(t in h for t in
                                          ("log2FC", "padj", "pvalue", "I2", "QEp", "tau2", "n_studies"))}),
                         sid="convergent")
        sh, sr = _csv_rows(meta / "meta_study_summary.csv")
        perstudy = R.section("Per-study differential expression",
                             _table(sh, sr, num_cols={i for i in range(1, len(sh))}), sid="per-study")
        perstudy_reports = R.section("Per-study results", _per_study_reports(project), sid="per-study-reports")
        enr = R.section("Cross-study functional enrichment (shared vs distinct)",
                        _enrichment_table(meta / "meta_enrichment_ora.csv"), sid="enrichment")
        methods = _method_note(ledger)
        body = (_study_scope(ledger) + check_note + (methods if not _corrected_ledger(ledger) else "") +
                _hero(summary, status, _call_alpha(ledger)) + (methods if _corrected_ledger(ledger) else "") +
                _field_dictionary(summary, ledger) + figures + conv + perstudy + perstudy_reports + enr)

    if empty:
        body = (_study_scope(ledger) + check_note + _method_note(ledger) +
                body + _field_dictionary(summary, ledger))

    main_report = reports / "results_report.html"
    main_report_note = ("The main <a href='results_report.html'>results report</a> describes the separately recorded joint fit."
                        if main_report.is_file() else
                        "The main results report is unavailable for this stored result; joint-fit details cannot be read here.")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BulkSeq Studio meta-analysis — {html.escape(name)}</title><style>{R.CSS}
main{{padding:26px clamp(16px,3vw,32px)}}
.tw{{max-width:100%;overflow-x:auto}}
.tw table{{min-width:max-content}}
main>details{{margin:18px 0;padding:14px 18px;border:1px solid var(--border);border-radius:var(--r2);background:var(--surface)}}
main>details>summary{{cursor:pointer;font-family:var(--sans);font-weight:600;color:#000}}
#field-guide dl{{display:grid;grid-template-columns:minmax(12rem,1fr) minmax(0,3fr);gap:8px 14px}}
#field-guide dt{{font-weight:600}}#field-guide dd{{margin:0}}
@media(max-width:650px){{#field-guide dl{{grid-template-columns:minmax(0,1fr)}}#field-guide dd{{margin-bottom:8px}}}}
</style></head>
<body>
<header class="top"><div class="brand">{R.LOGO_SVG}
<span class="wordmark">BulkSeq Studio</span>{ver_chip}<span class="tag">meta-analysis</span></div></header>
<main>
{body}
<p class='muted' style='margin-top:2rem'>{main_report_note} This page describes separate per-study DESeq2 fits and their cross-study combination. Joint and per-study fits can use different formulas; inspect their own execution records before interpreting either. A non-significant result does not establish no effect.</p>
</main>
<footer><div class="flinks">
<a href="{R.REPO_URL}" target="_blank" rel="noopener">GitHub repository ↗</a>
<a href="{R.DOCS_URL}" target="_blank" rel="noopener">Documentation ↗</a></div>
<p>Generated by BulkSeq Studio{f' v{html.escape(str(app_version))}' if app_version else ''} ·
per-study DESeq2 → inverse-normal combined p-values and effect-size pooling. Figures are embedded; source-data links refer to saved project tables.</p>
</footer>
{R.figure_dialog()}
<script>
{R.sortable_table_script()}
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
