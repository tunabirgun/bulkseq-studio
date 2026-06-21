// Interactive PPI viewer (cytoscape.js). Driven from Python via runJavaScript:
// PPI.render(payload), PPI.setLayout/ColorBy/SizeBy/Confidence/Labels/Theme,
// PPI.exportImage(fmt). All vendored offline; no network access.

if (window.cytoscapeFcose) { cytoscape.use(window.cytoscapeFcose); }
if (window.cytoscapeSvg) { cytoscape.use(window.cytoscapeSvg); }

let cy = null;
const BUDGET = 300;           // node display budget; above this we prune by degree
const state = {
  colorBy: "log2FoldChange",  // log2FoldChange | module
  sizeBy: "degree",           // degree | meanExpr | neglog10padj
  labels: true,
  layout: "fcose",            // current layout name (kept in sync with the Qt combo)
  floor: 0,                   // confidence (edge weight) floor
  theme: { bg: "#ffffff", text: "#1a1a1a", edge: "#c7c7c7", muted: "#8a8a8a" },
};
let RANGE = { lfc: 3, deg: [1, 10], meanExpr: [0, 1], neglp: [0, 1] };

function mix(a, b, t) { return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t]; }
function rgb(c) { return "rgb(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," + Math.round(c[2]) + ")"; }

// Diverging blue - grey - red around 0 (log2FC); grey for missing.
function divergingColor(v) {
  if (v === null || v === undefined || isNaN(v)) return "#bdbdbd";
  const m = RANGE.lfc || 1e-6;
  const t = Math.max(-1, Math.min(1, v / m));
  const grey = [189,189,189], blue = [33,102,172], red = [178,24,43];
  return rgb(t < 0 ? mix(grey, blue, -t) : mix(grey, red, t));
}
const MODPAL = ["#4e79a7","#f28e2b","#e15759","#76b7b2","#59a14f",
                "#edc948","#b07aa1","#ff9da7","#9c755f","#bab0ac"];
function moduleColor(m) {
  if (m === null || m === undefined || isNaN(m)) return "#bdbdbd";
  return MODPAL[((m % MODPAL.length) + MODPAL.length) % MODPAL.length];
}
function nodeColor(ele) {
  const d = ele.data();
  return state.colorBy === "module" ? moduleColor(d.module) : divergingColor(d.log2FoldChange);
}
function nodeSize(ele) {
  const d = ele.data();
  let rng = RANGE.deg, v = d.degree;
  if (state.sizeBy === "meanExpr") { rng = RANGE.meanExpr; v = d.meanExpr; }
  else if (state.sizeBy === "neglog10padj") { rng = RANGE.neglp; v = (d.padj != null && d.padj > 0) ? -Math.log10(d.padj) : null; }
  if (v === null || v === undefined || isNaN(v)) return 16;
  const lo = rng[0], hi = rng[1];
  const t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
  return 14 + Math.max(0, Math.min(1, t)) * 38;  // 14..52 px
}

function computeRanges(nodes) {
  let lfcMax = 1e-6, dMin = Infinity, dMax = -Infinity, mMin = Infinity, mMax = -Infinity, pMin = Infinity, pMax = -Infinity;
  nodes.forEach(function (n) {
    const d = n.data;
    if (d.log2FoldChange != null && !isNaN(d.log2FoldChange)) lfcMax = Math.max(lfcMax, Math.abs(d.log2FoldChange));
    if (d.degree != null) { dMin = Math.min(dMin, d.degree); dMax = Math.max(dMax, d.degree); }
    if (d.meanExpr != null) { mMin = Math.min(mMin, d.meanExpr); mMax = Math.max(mMax, d.meanExpr); }
    if (d.padj != null && d.padj > 0) { const v = -Math.log10(d.padj); pMin = Math.min(pMin, v); pMax = Math.max(pMax, v); }
  });
  RANGE = {
    lfc: lfcMax,
    deg: [isFinite(dMin) ? dMin : 1, isFinite(dMax) ? dMax : 10],
    meanExpr: [isFinite(mMin) ? mMin : 0, isFinite(mMax) ? mMax : 1],
    neglp: [isFinite(pMin) ? pMin : 0, isFinite(pMax) ? pMax : 1],
  };
}

// Labels are display-only auto-hidden above 220 nodes; this does NOT mutate the
// user's labels preference, so a later small graph shows them again.
function labelText(e) {
  return (state.labels && cy && cy.nodes().length <= 220) ? e.data("symbol") : "";
}
function styleArrayFor(theme) {
  return [
    { selector: "node", style: {
        "background-color": nodeColor, "width": nodeSize, "height": nodeSize,
        "border-width": 1, "border-color": "rgba(0,0,0,0.45)",
        "label": labelText,
        "font-size": 9, "color": theme.text, "text-valign": "center",
        "text-halign": "center", "text-outline-width": 2,
        "text-outline-color": theme.bg, "min-zoomed-font-size": 7 } },
    { selector: "edge", style: {
        "width": function (e) { return 0.5 + (e.data("weight") || 0.4) * 3.5; },
        "line-color": theme.edge, "curve-style": "haystack",
        "opacity": 0.65 } },
    { selector: "node.faded", style: { "opacity": 0.12 } },
    { selector: "edge.faded", style: { "opacity": 0.05 } },
    { selector: "node.hl", style: { "border-width": 3, "border-color": "#1a73e8" } },
  ];
}
function styleArray() { return styleArrayFor(state.theme); }

function pruneToBudget(elements) {
  const nodes = elements.nodes || [];
  if (nodes.length <= BUDGET) return elements;
  const keep = nodes.slice().sort(function (a, b) { return (b.data.degree || 0) - (a.data.degree || 0); })
                    .slice(0, BUDGET);
  const ids = {}; keep.forEach(function (n) { ids[n.data.id] = true; });
  const edges = (elements.edges || []).filter(function (e) { return ids[e.data.source] && ids[e.data.target]; });
  return { nodes: keep, edges: edges };
}

function layoutOpts(name, n) {
  if (name === "circle") return { name: "circle", animate: false };
  if (name === "concentric") return { name: "concentric", animate: false,
        concentric: function (e) { return e.data("degree") || 1; }, levelWidth: function () { return 2; } };
  if (name === "grid") return { name: "grid", animate: false };
  // fcose draft (skips the slow spectral init) keeps it sub-second. The seeded RNG
  // (reset in relayout) reduces run-to-run drift; the canonical reproducible network
  // figure is the static results/figures/ppi_network.png (igraph, set.seed).
  return { name: "fcose", quality: "draft", animate: false, randomize: true,
           nodeRepulsion: 6000, idealEdgeLength: 70, packComponents: true };
}

// fcose draws random initial positions from Math.random (some of it asynchronously),
// so install a seeded PRNG that is reset before each layout and never restored. This
// makes a re-render and the exported figure reproducible, per the project's
// "every figure regenerable" rule; a viewer needs no true randomness.
var __rngSeed = 42;
Math.random = function () {
  __rngSeed = (__rngSeed + 0x6D2B79F5) | 0;
  var t = Math.imul(__rngSeed ^ (__rngSeed >>> 15), 1 | __rngSeed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
function relayout(name) {
  if (!cy) return;
  if (name) state.layout = name;
  __rngSeed = 42;  // reset the seeded RNG each layout to reduce run-to-run drift
  var lay = cy.layout(layoutOpts(state.layout, cy.nodes().length));
  lay.one("layoutstop", function () { cy.fit(undefined, 40); });
  lay.run();
}

const tip = function () { return document.getElementById("tip"); };
function fmt(v, dp) {
  if (v === null || v === undefined || isNaN(v)) return "n/a";
  return (Math.abs(v) >= 1000 || (Math.abs(v) < 0.001 && v !== 0)) ? v.toExponential(2) : v.toFixed(dp);
}
function showTip(evt) {
  const d = evt.target.data(), t = tip();
  t.innerHTML =
    '<div class="sym">' + (d.symbol || d.id) + '</div>' +
    '<div class="row"><span class="k">log2FC:</span> ' + fmt(d.log2FoldChange, 2) + '</div>' +
    '<div class="row"><span class="k">padj:</span> ' + fmt(d.padj, 3) + '</div>' +
    '<div class="row"><span class="k">mean expr:</span> ' + fmt(d.meanExpr, 2) + '</div>' +
    '<div class="row"><span class="k">degree:</span> ' + (d.degree != null ? d.degree : "n/a") +
    '  <span class="k">module:</span> ' + (d.module != null ? d.module : "n/a") + '</div>';
  t.style.display = "block";
  moveTip(evt);
}
function moveTip(evt) {
  const t = tip(), p = evt.renderedPosition || { x: 0, y: 0 };
  t.style.left = (p.x + 14) + "px";
  t.style.top = (p.y + 14) + "px";
}
function hideTip() { tip().style.display = "none"; }

function bindInteractions() {
  cy.on("mouseover", "node", showTip);
  cy.on("mousemove", "node", moveTip);
  cy.on("mouseout", "node", hideTip);
  cy.on("tap", "node", function (evt) {
    const n = evt.target, nb = n.closedNeighborhood();
    cy.elements().addClass("faded"); nb.removeClass("faded"); n.addClass("hl");
  });
  cy.on("tap", function (evt) { if (evt.target === cy) { cy.elements().removeClass("faded"); cy.nodes().removeClass("hl"); } });
}

window.PPI = {
  render: function (payload) {
    const data = (typeof payload === "string") ? JSON.parse(payload) : payload;
    const empty = document.getElementById("empty");
    let elements = (data && data.elements) ? data.elements : { nodes: [], edges: [] };
    document.body.style.background = state.theme.bg;
    if (!elements.nodes || elements.nodes.length === 0) {
      if (cy) { cy.destroy(); cy = null; }
      empty.style.display = "block";
      return JSON.stringify({ nodes: 0, edges: 0 });
    }
    empty.style.display = "none";
    elements = pruneToBudget(elements);
    computeRanges(elements.nodes);
    if (cy) cy.destroy();
    cy = cytoscape({
      container: document.getElementById("cy"),
      elements: elements, style: styleArray(),
      wheelSensitivity: 0.2, layout: { name: "preset" },
    });
    bindInteractions();
    relayout(state.layout);
    return JSON.stringify({ nodes: cy.nodes().length, edges: cy.edges().length });
  },
  setLayout: function (name) { relayout(name); },
  setColorBy: function (f) { state.colorBy = f; if (cy) cy.style(styleArray()); },
  setSizeBy: function (f) { state.sizeBy = f; if (cy) cy.style(styleArray()); },
  setLabels: function (on) { state.labels = !!on; if (cy) cy.style(styleArray()); },
  setConfidence: function (floor) {
    state.floor = floor; if (!cy) return;
    cy.edges().forEach(function (e) { e.style("display", (e.data("weight") || 0) >= floor ? "element" : "none"); });
  },
  setTheme: function (t) {
    if (t) state.theme = t;
    document.body.style.background = state.theme.bg;
    if (cy) cy.style(styleArray());
  },
  exportImage: function (fmt, bg) {
    if (!cy) return "";
    // Export always uses a light style (dark labels) so text stays legible; the
    // canvas background is the user's choice: white, or transparent (omit bg).
    var transparent = (bg === "transparent");
    cy.style(styleArrayFor({ bg: "#ffffff", text: "#1a1a1a", edge: "#c7c7c7", muted: "#8a8a8a" }));
    var opts = { full: true };
    if (!transparent) opts.bg = "#ffffff";
    var out;
    if (fmt === "svg") { out = cy.svg(opts); }
    else { opts.scale = 2; out = cy.png(opts); }
    cy.style(styleArray());  // restore the live theme
    return out;
  },
  stats: function () { return cy ? JSON.stringify({ nodes: cy.nodes().length, edges: cy.edges().length }) : "{}"; },
  version: function () { return cytoscape.version; },
};
