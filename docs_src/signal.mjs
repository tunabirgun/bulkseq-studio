import { readFileSync } from 'node:fs';
const MODEL = JSON.parse(readFileSync(new URL('./model-examples.json', import.meta.url), 'utf8'));

const PLOT = { left: 86, right: 684, top: 38, bottom: 390 };
const DEFAULT_ALPHA = .05;
const DEFAULT_CUTOFF = 1;
const MIN_ALPHA = .001;
const MAX_CUTOFF = 3;

function fixture() {
  return MODEL.models.deseq2.filter(row => Number.isFinite(row.log2fc) && Number.isFinite(row.padj) && row.padj > 0);
}

function dimensions(rows) {
  const x = Math.ceil((Math.max(MAX_CUTOFF, ...rows.map(row => Math.abs(row.log2fc))) + .35) * 2) / 2;
  const y = Math.ceil((Math.max(-Math.log10(MIN_ALPHA), ...rows.map(row => -Math.log10(row.padj))) + .2) * 2) / 2;
  return { x, y };
}

function xAt(value, domain) {
  return PLOT.left + ((value + domain.x) / (domain.x * 2)) * (PLOT.right - PLOT.left);
}

function yAt(padj, domain) {
  return PLOT.bottom + Math.log10(padj) / domain.y * (PLOT.bottom - PLOT.top);
}

function evaluate(row, alpha = DEFAULT_ALPHA, cutoff = DEFAULT_CUTOFF) {
  const belowP = row.padj < alpha;
  const selected = belowP && Math.abs(row.log2fc) >= cutoff;
  const higher = selected && row.log2fc > 0;
  const lower = selected && row.log2fc < 0;
  return { belowP, higher, lower, selected };
}

function selectionSummary(rows) {
  return rows.reduce((summary, row) => {
    const state = evaluate(row);
    summary.below += Number(state.belowP);
    summary.selected += Number(state.selected);
    summary.higher += Number(state.higher);
    summary.lower += Number(state.lower);
    return summary;
  }, { below: 0, selected: 0, higher: 0, lower: 0 });
}

function grid(domain) {
  const xLimit = Math.floor(domain.x);
  const xTicks = Array.from({ length: xLimit * 2 + 1 }, (_, index) => index - xLimit);
  const rawStep = domain.y / 6;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 2.5, 5, 10].find(value => value * magnitude >= rawStep) * magnitude;
  const yTicks = Array.from({ length: Math.floor(domain.y / step) + 1 }, (_, index) => index * step);
  return `<g class="parameter-grid" aria-hidden="true">
    ${xTicks.map(value => `<path d="M${xAt(value, domain).toFixed(2)} ${PLOT.top}V${PLOT.bottom}"/><text x="${xAt(value, domain).toFixed(2)}" y="${PLOT.bottom + 22}" text-anchor="middle">${value}</text>`).join('')}
    ${yTicks.map(value => `<path d="M${PLOT.left} ${PLOT.bottom - value / domain.y * (PLOT.bottom - PLOT.top)}H${PLOT.right}"/><text x="${PLOT.left - 12}" y="${PLOT.bottom - value / domain.y * (PLOT.bottom - PLOT.top) + 4}" text-anchor="end">${value}</text>`).join('')}
    <path class="parameter-axis" d="M${PLOT.left} ${PLOT.top}V${PLOT.bottom}H${PLOT.right}"/>
  </g>`;
}

export function renderSignal() {
  const rows = fixture();
  const domain = dimensions(rows);
  const summary = selectionSummary(rows);
  const pLine = yAt(DEFAULT_ALPHA, domain);
  const leftCut = xAt(-DEFAULT_CUTOFF, domain);
  const rightCut = xAt(DEFAULT_CUTOFF, domain);
  const points = rows.map(row => {
    const state = evaluate(row);
    const classes = ['parameter-point'];
    if (state.higher) classes.push('is-higher', 'is-selected');
    else if (state.lower) classes.push('is-lower', 'is-selected');
    else if (state.belowP) classes.push('is-below');
    const fill = state.higher ? '#1c4ed8' : state.lower ? '#d45a3e' : '#101010';
    return `<circle class="${classes.join(' ')}" data-parameter-point data-id="${row.id}" data-log2fc="${row.log2fc}" data-padj="${row.padj}" cx="${xAt(row.log2fc, domain).toFixed(2)}" cy="${yAt(row.padj, domain).toFixed(2)}" r="${state.selected ? 3.2 : 2}" fill="${fill}"/>`;
  }).join('');
  return `<section class="parameter-lab" data-parameter-lab aria-labelledby="parameter-title">
    <header class="parameter-header"><p class="eyebrow">Precomputed synthetic DESeq2 run</p><h2 id="parameter-title">Volcano plot</h2></header>
    <div class="parameter-controls" aria-label="Selection controls">
      <label class="parameter-control" for="p-cutoff"><span>Adjusted p-value cutoff</span><output data-alpha-output>0.05</output><input id="p-cutoff" type="range" min="0.001" max="0.1" step="0.001" value="0.05" disabled></label>
      <label class="parameter-control" for="fc-cutoff"><span>Minimum |log2 fold change|</span><output data-fc-output>1.00</output><input id="fc-cutoff" type="range" min="0" max="3" step="0.25" value="1" disabled></label>
      <label class="parameter-control" for="comparison-view"><span>Comparison view</span><select id="comparison-view" disabled><option value="b-a">B vs A</option><option value="a-b">A vs B</option></select></label>
      <button type="button" id="parameter-reset" disabled>Reset</button>
    </div>
    <div class="figure-view-controls"><button type="button" data-volcano-size aria-pressed="false" aria-controls="volcano-figure" disabled>Enlarge figure</button><p data-volcano-size-note>Full figure shown. Tap a point to inspect values.</p></div>
    <div class="parameter-plot">
      <div id="volcano-figure" class="parameter-scroll is-fit" role="region" aria-label="Volcano figure">
      <p class="compact-axis-caption">Vertical axis: −log₁₀(adjusted p)</p>
      <svg class="parameter-canvas" data-domain-x="${domain.x}" data-domain-y="${domain.y}" data-plot-left="${PLOT.left}" data-plot-right="${PLOT.right}" data-plot-top="${PLOT.top}" data-plot-bottom="${PLOT.bottom}" viewBox="0 0 720 480" role="img" aria-label="Volcano plot of fixed synthetic results with adjusted p-value and fold-change cutoff guides" tabindex="0" aria-describedby="parameter-keyboard-help">
        <title>Fixed synthetic results</title><desc data-parameter-svg-description>A DESeq2 volcano plot from simulated counts. It represents a reproducible synthetic model fit, not a biological study.</desc>
        ${grid(domain)}
        <g class="parameter-selection" aria-hidden="true"><rect data-selection-left x="${PLOT.left}" y="${PLOT.top}" width="${leftCut - PLOT.left}" height="${pLine - PLOT.top}"/><rect data-selection-right x="${rightCut}" y="${PLOT.top}" width="${PLOT.right - rightCut}" height="${pLine - PLOT.top}"/></g>
        <g class="parameter-thresholds" aria-hidden="true"><line data-p-line x1="${PLOT.left}" x2="${PLOT.right}" y1="${pLine}" y2="${pLine}"/><line data-left-cut x1="${leftCut}" x2="${leftCut}" y1="${PLOT.top}" y2="${PLOT.bottom}"/><line data-right-cut x1="${rightCut}" x2="${rightCut}" y1="${PLOT.top}" y2="${PLOT.bottom}"/></g>
        <g class="parameter-points">${points}</g>
        <g class="parameter-tooltip" data-parameter-tooltip aria-hidden="true" style="display:none"><rect width="320" height="104" rx="2"/><text x="10" y="22" data-tooltip-id>Example</text><text x="10" y="46" data-tooltip-effect>displayed log2FC</text><text x="10" y="70" data-tooltip-padj>adjusted p-value</text><text x="10" y="94" data-tooltip-state>Selection state</text></g>
        <g class="parameter-axis-labels" aria-hidden="true"><text x="${(PLOT.left + PLOT.right) / 2}" y="450" text-anchor="middle" data-x-axis-label>LOG2 FOLD CHANGE (B VS A)</text><text data-y-axis-label transform="translate(38 ${(PLOT.top + PLOT.bottom) / 2}) rotate(-90)" text-anchor="middle">−log₁₀(adjusted p)</text></g>
      </svg>
      </div>
      <p class="parameter-note">Cutoffs change selection; the model is not rerun.</p>
    </div>
    <dl class="parameter-stats" aria-live="polite"><div><dt>Below adjusted p cutoff</dt><dd data-below-count>${summary.below}</dd><small data-below-rule>padj &lt; 0.05</small></div><div><dt>Passing both</dt><dd data-selected-count>${summary.selected}</dd><small data-fc-rule>|log2FC| ≥ 1.00</small></div><div><dt>Higher / lower</dt><dd><span data-higher-count>${summary.higher}</span> / <span data-lower-count>${summary.lower}</span></dd><small>in the displayed direction</small></div></dl>
    <div class="parameter-legend"><span><i class="parameter-swatch parameter-swatch--higher"></i>Higher after both screens</span><span><i class="parameter-swatch parameter-swatch--lower"></i>Lower after both screens</span><span><i class="parameter-swatch"></i>Not selected</span></div>
    <p class="sr-only" id="parameter-keyboard-help">Focus the plot, then use arrow keys to inspect synthetic examples one at a time. Home and End jump to the first and last examples.</p><p class="lesson-readout" data-parameter-status role="status" aria-live="polite">Focus the figure and use arrow keys, or tap a point, to inspect values.</p>
    <details class="parameter-details"><summary>What this explains</summary><p>Adjusted p-values account for multiple testing; they are not per-feature false probabilities.</p><p>Changing <a href="design.html#thresholds">DESeq2’s alpha setting</a> can reoptimize independent filtering during a run. This fixed example does not reproduce that process. Its fold-change slider is a post hoc screen, not a DESeq2 hypothesis test using <code>lfcThreshold</code>.</p><p>The comparison switch mirrors only this synthetic example. BulkSeq Studio preserves the <a href="imported-results.html#direction">comparison direction recorded in imported results</a>.</p></details>
  </section>`;
}
