import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { documentedVersion } from './site-config.mjs';
import * as content from './content.mjs';
const { pages, glossary } = content;

const out = fileURLToPath(new URL('../docs/', import.meta.url));
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const plain = value => value.replace(/<[^>]*>/g, ' ').replace(/&(?:nbsp|amp|lt|gt|quot|#39);/g, ' ').replace(/\s+/g, ' ').trim();
const href = page => `${page.slug}.html`;
const groups = [...new Set(pages.map(page => page.group))];
const nav = current => groups.map(group => `<section class="nav-group"><h2>${escape(group)}</h2><ul>${pages.filter(page => page.group === group).map(page => `<li><a href="${href(page)}"${page.slug === current ? ' aria-current="page"' : ''}>${escape(page.title)}</a></li>`).join('')}</ul></section>`).join('');
const search = [];
await mkdir(out, { recursive: true });
for (const [index, page] of pages.entries()) {
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
    return `<h${level}${attrs}>${title}<a class="heading-link" href="#${id}" aria-label="Link to ${escape(plain(title))}">#</a></h${level}>`;
  });
  search.push({title: page.title, url: href(page), summary: page.summary, text: plain(page.body)});
  const toc = headings.map(h => `<li class="toc-level-${h.level}"><a href="#${h.id}">${escape(h.title)}</a></li>`).join('');
  const adjacent = [pages[index - 1], pages[index + 1]].map((p, i) => p ? `<a href="${href(p)}"><span>${i ? 'Next chapter' : 'Previous chapter'}</span><strong>${escape(p.title)} ${i ? '→' : '←'}</strong></a>` : '<span></span>').join('');
  await writeFile(`${out}/${href(page)}`, `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><link rel="icon" href="assets/bulkseq_logo.svg" type="image/svg+xml"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(page.title)} · BulkSeq Studio documentation</title><meta name="description" content="${escape(page.summary)}"><script src="assets/theme.js"></script><link rel="stylesheet" href="assets/site.css"><script src="assets/search-index.js" defer></script><script src="assets/site.js" defer></script></head>
<body data-page="${escape(page.slug)}"><a class="skip-link" href="#main">Skip to content</a><header class="masthead"><div class="masthead-inner"><a class="wordmark" href="index.html"><img src="assets/bulkseq_logo.svg" alt="" width="42" height="42"><span class="brand-text">BulkSeq Studio<span class="brand-meta">Documentation · v${escape(documentedVersion)}</span></span></a><div class="header-actions"><button class="search-trigger js-only" data-open-search>Search the manual <kbd>Ctrl K</kbd></button><div class="theme-control js-only"><button id="theme-trigger" aria-expanded="false" aria-controls="theme-options">Theme</button><div id="theme-options" hidden role="group" aria-label="Colour theme">${["Light", "Dark", "System"].map(name => `<button type="button" data-theme-choice="${name.toLowerCase()}" aria-pressed="false">${name}</button>`).join("")}</div></div><button class="menu-trigger js-only" data-open-menu>Chapters</button></div></div></header>
<div class="layout"><aside class="chapter-rail"><nav aria-label="Chapters">${nav(page.slug)}</nav><p class="rail-note">A practical reference for bulk RNA-seq and microarray analysis.</p></aside><main id="main" tabindex="-1"><header class="article-header"><p class="eyebrow">${escape(page.group)}</p><h1>${escape(page.title)}</h1><p class="lead">${escape(page.summary)}</p></header><details class="mobile-toc"><summary>On this page</summary><ul>${toc}</ul></details><article>${body}</article><nav class="adjacent" aria-label="Adjacent chapters">${adjacent}</nav><footer class="article-footer">BulkSeq Studio documentation · Read, configure, analyse, interpret.</footer></main><aside class="toc-rail"><nav aria-label="On this page"><h2>On this page</h2><ul>${toc}</ul></nav><a class="back-top" href="#main">Back to top ↑</a></aside></div>
<dialog id="search-dialog" aria-labelledby="search-title"><div class="dialog-heading"><h2 id="search-title">Search the manual</h2><button data-close-dialog aria-label="Close search">Close</button></div><label for="search-input">Search chapters, methods and outputs</label><input id="search-input" type="search" autocomplete="off" placeholder="For example, contrasts or filtering"><p id="search-status" role="status">Type to search the complete manual.</p><ul id="search-results"></ul></dialog>
<dialog id="menu-dialog" aria-labelledby="menu-title"><div class="dialog-heading"><h2 id="menu-title">Chapters</h2><button data-close-dialog>Close</button></div><nav aria-label="Mobile chapters">${nav(page.slug)}</nav></dialog>
<dialog id="image-dialog" aria-labelledby="image-title"><div class="dialog-heading"><h2 id="image-title">Figure viewer</h2><button data-close-dialog>Close</button></div><div class="viewer-controls"><button data-zoom="out" aria-label="Zoom out">−</button><output id="zoom-level">100%</output><button data-zoom="in" aria-label="Zoom in">+</button><button data-zoom="reset">Fit / reset</button></div><div class="image-stage" tabindex="0" aria-label="Figure; scroll to pan when zoomed"></div><p class="viewer-caption"></p></dialog>
<div id="term-tooltip" role="tooltip" hidden></div><div id="copy-status" class="visually-hidden" role="status"></div></body></html>`);
}
await writeFile(`${out}/assets/search-index.js`, `window.manualSearch = ${JSON.stringify(search)};\nwindow.manualGlossary = ${JSON.stringify(glossary)};\nwindow.walkthroughRoutes = ${JSON.stringify(content.walkthroughRoutes || [])};\n`);
await writeFile(`${out}/.nojekyll`, '');
console.log(`Built ${pages.length} documentation pages.`);
