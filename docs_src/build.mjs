import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { documentedVersion } from './site-config.mjs';
import * as content from './content.mjs';
const { pages, glossary } = content;

const out = fileURLToPath(new URL('../docs/', import.meta.url));
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const plain = value => value.replace(/<[^>]*>/g, ' ').replace(/&(?:nbsp|amp|lt|gt|quot|#39);/g, ' ').replace(/\s+/g, ' ').trim();
const href = page => `${page.slug}.html`;
const home = pages.find(page => page.section === 'Home');
// The four sections are the top-level destinations; Home is the hub, reached by the wordmark.
const sections = [...new Set(pages.filter(page => page.section !== 'Home').map(page => page.section))];
const inSection = section => pages.filter(page => page.section === section);
const link = (page, current) => `<li><a href="${href(page)}"${page.slug === current ? ' aria-current="page"' : ''}>${escape(page.title)}</a></li>`;

/** The pages of one section, split by their part heading; a section without parts stays flat.
    The rail heads each group itself; the site map heads the section and labels the parts under it. */
function rail(section, current, labelled = false) {
  const members = inSection(section);
  const parts = [...new Set(members.map(page => page.part).filter(Boolean))];
  const head = text => labelled ? `<p class="nav-part">${escape(text)}</p>` : `<h2>${escape(text)}</h2>`;
  if (!parts.length) return `<section class="nav-group">${labelled ? '' : head(section)}<ul>${members.map(page => link(page, current)).join('')}</ul></section>`;
  return parts.map(part => `<section class="nav-group">${head(part)}<ul>${members.filter(page => page.part === part).map(page => link(page, current)).join('')}</ul></section>`).join('');
}

const siteMap = current => `<ul class="map-home">${link(home, current)}</ul>${sections.map(section => `<section class="map-block"><h2>${escape(section)}</h2>${rail(section, current, true)}</section>`).join('')}`;
const sectionNav = current => `<ul>${sections.map(section => `<li><a href="${href(inSection(section)[0])}"${section === current ? ' aria-current="true"' : ''}>${escape(section)}</a></li>`).join('')}</ul>`;

const search = [];
await mkdir(out, { recursive: true });
for (const page of pages) {
  const headings = [], used = new Set();
  const body = page.body.replace(/<button([^>]*data-term="([^"]+)"[^>]*)>([\s\S]*?)<\/button>/g, (_, attrs, term, label) => `<a${attrs} href="glossary.html#${term.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}">${label}</a>`).replace(/<table([\s\S]*?)<\/table>/g, table => `<div class="table-wrap" role="region" tabindex="0" aria-label="Reference table">${table}</div>`).replace(/<h([23])([^>]*)>([\s\S]*?)<\/h\1>/g, (_, level, attrs, title) => {
    let id = attrs.match(/\bid="([^"]+)"/)?.[1];
    if (!id) {
      const base = plain(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section';
      id = base; let suffix = 2;
      while (used.has(id)) id = `${base}-${suffix++}`;
      attrs += ` id="${id}"`;
    }
    used.add(id); headings.push({ level, id, title: plain(title) });
    // A numbered step keeps its number upright and sets the step's name in italic.
    const step = title.match(/^(\s*(?:Step\s+)?\d+\.\s+)([\s\S]+)$/);
    const shown = step ? `${step[1]}<em>${step[2]}</em>` : title;
    return `<h${level}${attrs}>${shown}<a class="heading-link" href="#${id}" aria-label="Link to ${escape(plain(title))}">#</a></h${level}>`;
  });
  // A callout must say what kind it is: a warning rendered as a routine note is the defect this prevents.
  const untyped = (body.match(/<aside class="callout(?! callout-(check|warning|caution|note|new)")/g) || []).length;
  if (untyped) throw new Error(`${page.slug}: ${untyped} callout(s) without a kind`);
  search.push({title: page.title, url: href(page), summary: page.summary, section: page.section, text: plain(page.body)});
  const toc = headings.map(h => `<li class="toc-level-${h.level}"><a href="#${h.id}">${escape(h.title)}</a></li>`).join('');
  const hub = page.section === 'Home';
  const neighbours = pages.filter(other => other.section !== 'Home');
  const position = neighbours.indexOf(page);
  const adjacent = hub ? '' : [neighbours[position - 1], neighbours[position + 1]].map((p, i) => p ? `<a href="${href(p)}"><span>${i ? 'Next' : 'Previous'}</span><strong>${escape(p.title)} ${i ? '→' : '←'}</strong></a>` : '<span></span>').join('');
  await writeFile(`${out}/${href(page)}`, `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><link rel="icon" href="assets/bulkseq_logo.svg" type="image/svg+xml"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(hub ? page.title : `${page.title} · BulkSeq Studio documentation`)}</title><meta name="description" content="${escape(page.summary)}"><script src="assets/theme.js"></script><link rel="stylesheet" href="assets/site.css"><script src="assets/search-index.js" defer></script><script src="assets/site.js" defer></script></head>
<body data-page="${escape(page.slug)}"${hub ? ' class="is-hub"' : ''}><a class="skip-link" href="#main">Skip to content</a><header class="masthead"><div class="masthead-inner"><a class="wordmark" href="index.html"><img src="assets/bulkseq_logo.svg" alt="" width="34" height="34"><span class="brand-text">BulkSeq Studio<span class="brand-meta">Documentation · v${escape(documentedVersion)}</span></span></a><nav class="section-nav" aria-label="Documentation sections">${sectionNav(page.section)}</nav><div class="header-actions"><button class="search-trigger js-only" data-open-search>Search<kbd>Ctrl K</kbd></button><button id="theme-trigger" class="js-only" type="button"><span class="theme-name">Light theme</span><span class="visually-hidden"> — switch to dark</span></button><button class="menu-trigger js-only" data-open-menu>Menu</button></div></div></header>
<div class="layout">${hub ? '' : `<aside class="section-rail"><nav aria-label="${escape(page.section)} pages">${rail(page.section, page.slug)}</nav></aside>`}<main id="main" tabindex="-1"><header class="article-header"><p class="eyebrow">${escape(hub ? `Version ${documentedVersion}` : page.section)}</p><h1>${escape(page.title)}</h1><p class="lead">${escape(page.summary)}</p></header>${toc && !hub ? `<details class="mobile-toc"><summary>On this page</summary><ul>${toc}</ul></details>` : ''}<article>${body}</article>${adjacent ? `<nav class="adjacent" aria-label="Adjacent pages">${adjacent}</nav>` : ''}<footer class="article-footer">BulkSeq Studio documentation · Reproducible bulk RNA-seq and microarray analysis.</footer></main>${hub || !toc ? '' : `<aside class="toc-rail"><nav aria-label="On this page"><h2>On this page</h2><ul>${toc}</ul></nav><a class="back-top" href="#main">Back to top ↑</a></aside>`}</div>
<dialog id="search-dialog" aria-labelledby="search-title"><div class="dialog-heading"><h2 id="search-title">Search the documentation</h2><button data-close-dialog aria-label="Close search">Close</button></div><label for="search-input">Search pages, methods and outputs</label><input id="search-input" type="search" autocomplete="off" placeholder="For example, contrasts or filtering"><p id="search-status" role="status">Type to search every page.</p><ul id="search-results"></ul></dialog>
<dialog id="menu-dialog" aria-labelledby="menu-title"><div class="dialog-heading"><h2 id="menu-title">All pages</h2><button data-close-dialog>Close</button></div><nav aria-label="All documentation pages">${siteMap(page.slug)}</nav></dialog>
<dialog id="image-dialog" aria-labelledby="image-title"><div class="dialog-heading"><h2 id="image-title">Figure viewer</h2><button data-close-dialog>Close</button></div><div class="viewer-controls"><button data-zoom="out" aria-label="Zoom out">−</button><output id="zoom-level">100%</output><button data-zoom="in" aria-label="Zoom in">+</button><button data-zoom="reset">Fit / reset</button></div><div class="image-stage" tabindex="0" aria-label="Figure; scroll to pan when zoomed"></div><p class="viewer-caption"></p></dialog>
<div id="term-tooltip" role="tooltip" hidden></div><div id="copy-status" class="visually-hidden" role="status"></div></body></html>`);
}
await writeFile(`${out}/assets/search-index.js`, `window.manualSearch = ${JSON.stringify(search)};\nwindow.manualGlossary = ${JSON.stringify(glossary)};\nwindow.walkthroughRoutes = ${JSON.stringify(content.walkthroughRoutes || [])};\n`);
await writeFile(`${out}/.nojekyll`, '');
console.log(`Built ${pages.length} documentation pages.`);
