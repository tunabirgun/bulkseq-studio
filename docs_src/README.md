# BulkSeq Studio documentation source

`docs_src/` builds the static manual published from `docs/` on GitHub Pages. The generated site supports ordinary file navigation and hosting beneath a repository subpath. Reading and section navigation work without JavaScript; search, glossary previews, clipboard controls, the mobile drawer and the figure viewer enhance the static pages.

## Build and preview

Node 20 or newer. No packages and no remote assets are required.

```sh
node docs_src/build.mjs
node docs_src/serve.mjs
```

`build.mjs` writes the documentation pages, `docs/assets/search-index.js` and `docs/.nojekyll`. The stylesheet, scripts, logo and screenshots under `docs/assets/` are maintained by hand and are not generated; the build never deletes them. `serve.mjs` opens the built site at <http://127.0.0.1:4173/>. Set `PORT` to choose another port and `BASE_PATH` to a repository name to preview subpath hosting, then open the path the server prints. The preview server binds to the local machine only.

Rebuild whenever content, the documented version or the build script changes, and commit `docs/` together with `docs_src/`: `tests/test_docs_site.py` fails when the committed pages differ from a fresh build.

## Edit the manual

Page content lives in `docs_src/content.mjs`. Each page declares its slug, title, summary and HTML body; the `layout` table at the end of the same file assigns every page to one of the four sections — Tutorial, How-to guides, Reference and Explanation — with an optional part heading for its group in the section rail, and fixes the order of the rails, the site map and the previous and next links. A page missing from that table, or a table entry naming a page that does not exist, fails the build rather than disappearing from navigation. The build derives the section navigation, heading anchors, the contents rail and the search index from those entries. Styling and interactions live in `docs/assets/site.css` and `docs/assets/site.js`.

The header shows the documented release, set once in `docs_src/site-config.mjs`. It must match `APP_VERSION` in `app/constants.py`, which `tests/test_docs_site.py` checks: update it in the same change as the version bump and rebuild. The bundled application logo also serves as the favicon. The Theme button names the theme currently showing and switches to the other one; with no saved choice the page follows the operating system, and a switched choice is saved locally when browser storage is available. Theme initialisation runs before the stylesheet to avoid a mismatched first paint; without JavaScript the operating-system preference still applies.

Keep heading identifiers stable when other pages link to them. Declare glossary definitions in the exported glossary object and use `data-term` on a glossary link. Retain a real link to the glossary so it remains usable without scripting.

## Screenshots

Place reviewed image files under `docs/assets/images/` and add them with `imageFigure(file, title, alt, caption)`, which writes a `figure` with a relative `src`; the viewer discovers it automatically. Capture from the application itself — `scripts/capture_gui_matrix.py` shows the technique: the native platform plugin, the light theme, a temporary synthetic project and `QWidget.grab()`, never a desktop grab. Describe what the capture actually shows, and inspect the PNG's chunks for textual, time or generator metadata before committing. A figure whose screenshot is not yet available is written as a labelled placeholder instead, so that a page never implies a capture it does not have.

## Verification

Run the build, run `tests/test_docs_site.py`, and check internal page and anchor links, wide and narrow layouts, search with matching and empty results, keyboard shortcuts, dialog dismissal and focus return, glossary previews, figure zoom, code copying, a repository subpath and a browser session with JavaScript disabled.

A layout check does not establish that a described application behaviour matches the release. Recheck commands, defaults, options and output descriptions against the application revision being documented before publication.
