import { mkdir, writeFile, readFile } from 'node:fs/promises';
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

const themeButton = id => `<button id="${id}" class="theme-toggle js-only" type="button" title="Switch to the dark theme"><svg class="theme-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.4A8.6 8.6 0 0 1 9.6 4a8.6 8.6 0 1 0 10.4 10.4Z"/></svg><svg class="theme-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/></svg><span class="theme-name">Light theme</span><span class="visually-hidden"> — switch to dark</span></button>`;
const sidebar = `<aside class="site-sidebar" aria-label="Product navigation"><a class="sidebar-brand" href="index.html" aria-label="BulkSeq Studio home"><span>BulkSeq Studio</span></a><nav class="side-nav" aria-label="Website sections"><ul>${[['overview','Overview'],['workflow','Workflow'],['outputs','Results'],['where-to-start','Handbook'],['download','Download']].map(([id,label])=>`<li><a href="#${id}"><span class="side-label">${label}</span></a></li>`).join('')}</ul></nav><div class="sidebar-bottom"><button class="sidebar-search js-only" data-open-search>Search handbook <span aria-hidden="true">↗</span></button><div class="sidebar-utilities"><p class="sidebar-version">v${escape(documentedVersion)}</p>${themeButton('theme-trigger-sidebar')}</div></div></aside>`;
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
    return `<h${level}${attrs}>${shown}${page.section === 'Home' ? '' : `<a class="heading-link" href="#${id}" aria-label="Link to ${escape(plain(title))}">#</a>`}</h${level}>`;
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
<html lang="en"><head><meta charset="utf-8"><link rel="icon" href="assets/bulkseq_logo.svg" type="image/svg+xml"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(hub ? page.title : `${page.title} · BulkSeq Studio documentation`)}</title><meta name="description" content="${escape(page.summary)}"><script src="assets/theme.js"></script><link rel="preload" href="assets/fonts/manrope-latin.woff2" as="font" type="font/woff2" crossorigin><link rel="preload" href="assets/fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin><link rel="stylesheet" href="assets/fonts.css"><link rel="stylesheet" href="assets/site.css">${hub ? '<link rel="stylesheet" href="assets/parameters.css"><link rel="stylesheet" href="assets/learning.css">' : ''}<script src="assets/search-index.js" defer></script><script src="assets/site.js" defer></script><script src="assets/home.js" defer></script>${hub ? '<script src="assets/signal.js" defer></script><script src="assets/learning-core.js" defer></script><script src="assets/learning.js" defer></script>' : ''}</head>
<body data-page="${escape(page.slug)}"${hub ? ' class="is-hub"' : ''}><a class="skip-link" href="#main">Skip to content</a><div class="reading-progress" aria-hidden="true"></div><header class="masthead"><div class="masthead-inner"><a class="wordmark" href="index.html"><span class="brand-text">BulkSeq Studio<span class="brand-meta">v${escape(documentedVersion)}</span></span></a><nav class="section-nav" aria-label="${hub ? 'Website sections' : 'Documentation sections'}">${hub ? '<ul><li><a href="#overview">Overview</a></li><li><a href="#workflow">Workflow</a></li><li><a href="#where-to-start">Handbook</a></li></ul>' : sectionNav(page.section)}</nav><div class="header-actions"><button class="search-trigger js-only" data-open-search aria-label="Search the documentation" title="Search (Ctrl K)"><span class="search-icon" aria-hidden="true"></span></button>${themeButton('theme-trigger')}<a class="header-download" href="${hub ? '#download' : 'index.html#download'}">Download <span aria-hidden="true">↗</span></a><button class="menu-trigger js-only" data-open-menu>Menu</button></div></div></header>${hub ? sidebar : ''}
<div class="layout">${hub ? '' : `<aside class="section-rail"><nav aria-label="${escape(page.section)} pages">${rail(page.section, page.slug)}</nav></aside>`}<main id="main" tabindex="-1">${hub ? '' : `<header class="article-header"><p class="eyebrow">${escape(hub ? `Version ${documentedVersion}` : page.section)}</p><h1>${escape(page.title)}</h1><p class="lead">${escape(page.summary)}</p></header>`}${toc && !hub ? `<details class="mobile-toc"><summary>On this page</summary><ul>${toc}</ul></details>` : ''}<article>${body}</article>${adjacent ? `<nav class="adjacent" aria-label="Adjacent pages">${adjacent}</nav>` : ''}</main>${hub || !toc ? '' : `<aside class="toc-rail"><nav aria-label="On this page"><h2>On this page</h2><ul>${toc}</ul></nav><a class="back-top" href="#main">Back to top ↑</a></aside>`}</div>
<dialog id="search-dialog" aria-labelledby="search-title"><div class="dialog-heading"><h2 id="search-title">Search the documentation</h2><button data-close-dialog aria-label="Close search">Close</button></div><label for="search-input">Search pages, methods and outputs</label><input id="search-input" type="search" autocomplete="off" placeholder="For example, contrasts or filtering"><p id="search-status" role="status">Type to search every page.</p><ul id="search-results"></ul></dialog>
<dialog id="menu-dialog" aria-labelledby="menu-title"><div class="dialog-heading"><h2 id="menu-title">All pages</h2><button data-close-dialog>Close</button></div><nav aria-label="All documentation pages">${hub ? '<div class="mobile-site-links"><a href="index.html#overview">Overview</a><a href="index.html#workflow">Your workflow</a><a href="index.html#outputs">Explore results</a><a href="index.html#where-to-start">Handbook</a><a href="index.html#download">Download</a></div><details><summary>Full handbook</summary>' : ''}${siteMap(page.slug)}${hub ? '</details>' : ''}</nav></dialog>
<dialog id="image-dialog" aria-labelledby="image-title"><div class="dialog-heading"><h2 id="image-title">Figure viewer</h2><button data-close-dialog>Close</button></div><div class="viewer-controls"><button data-zoom="out" aria-label="Zoom out">−</button><output id="zoom-level">100%</output><button data-zoom="in" aria-label="Zoom in">+</button><button data-zoom="reset">Fit / reset</button></div><div class="image-stage" tabindex="0" aria-label="Figure; scroll to pan when zoomed"></div><p class="viewer-caption"></p></dialog>
<div id="term-tooltip" role="tooltip" hidden></div><div id="copy-status" class="visually-hidden" role="status"></div></body></html>`);
}
await writeFile(`${out}/assets/search-index.js`, `window.manualSearch = ${JSON.stringify(search)};\nwindow.manualGlossary = ${JSON.stringify(glossary)};\nwindow.walkthroughRoutes = ${JSON.stringify(content.walkthroughRoutes || [])};\n`);
await writeFile(`${out}/assets/learning-core.js`, await readFile(new URL('./learning-core.cjs', import.meta.url), 'utf8'));
await writeFile(`${out}/.nojekyll`, '');
console.log(`Built ${pages.length} documentation pages.`);
