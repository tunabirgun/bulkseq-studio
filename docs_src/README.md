# BulkSeq Studio documentation source

`docs_src/` builds the product website and handbook published from `docs/` on GitHub Pages. The generated site supports ordinary file navigation and hosting beneath a repository subpath. Reading and section navigation work without JavaScript; search, inline definitions, clipboard controls, the mobile drawer and the figure viewer enhance the static pages.

## Build and preview

Node 20 or newer. No packages and no remote assets are required.

```sh
node docs_src/build.mjs
node docs_src/serve.mjs
```

`build.mjs` writes the documentation pages, `docs/assets/search-index.js`, `docs/assets/explorer-core.js` and `docs/.nojekyll`. Other stylesheets, scripts, fonts, the logo and screenshots under `docs/assets/` are maintained assets; the build never deletes them. `serve.mjs` opens the built site at <http://127.0.0.1:4173/>. Set `PORT` to choose another port and `BASE_PATH` to a repository name to preview subpath hosting, then open the path the server prints. The preview server binds to the local machine only.

Rebuild whenever content, the documented version or the build script changes, and commit `docs/` together with `docs_src/`: `tests/test_docs_site.py` fails when the committed pages differ from a fresh build.

## Edit the manual

The homepage uses a compact introduction, one interactive volcano plot and links to Input, Science, Output, Export and Understanding. The five pages lead with practical guidance and place technical detail in expandable sections. Output adds two focused views, a heatmap and a cached STRING network; Science includes a pooled-effect forest plot. The earlier multi-lesson interface remains retired. Its validated heatmap values are reused without loading the old interface. Downloads point to the official release record.

`docs_src/home.mjs` renders the homepage. `docs_src/sections.mjs` supplies the primary input, output, export and interpretation content; `docs_src/science.mjs` supplies methods and the release-pinned tool table. `tool-versions.json` records the documented release, source lock and package keys; `tests/test_docs_versions.py` compares the rows with the workflow lock. Update this snapshot with the release it describes.

The volcano is built by `signal.mjs`, styled by `docs/assets/parameters.css` and animated by `docs/assets/signal.js`. Its DESeq2 values were fitted to simulated counts and saved in `model-examples.json`. The adjusted-p cutoff and absolute fold-change screen change selection, guides and highlighting; they do not refit the model or move genes. The comparison stays B versus A. Reduced-motion mode updates immediately. Run the separate phases in [reproduction/README.md](reproduction/README.md) only when the scientific fixtures change.

Typography uses system sans-serif body text, Production Type’s Newsreader headings and IBM Plex Mono for data and code. The site uses monochrome light and dark appearances with color reserved for plot marks. The header has text-only Light / Dark controls; appearance follows the device until a visitor chooses, then remembers that choice locally. `appearance.js` applies the preference before styles load; without JavaScript, CSS follows the device. The shared footer is the author link and research-use statement. Font licenses remain in `docs/assets/fonts/`.

The remaining handbook content lives in `content.mjs`; its layout table orders the menu, search and adjacent links. Heading identifiers remain stable. The build checks that every page has a layout entry. The header version from `site-config.mjs` must match the application release declaration.

Definitions live in the exported `glossary` object and are presented inline by `site.js`, once per term per page, on hover, keyboard focus or tap. Aliases share the same definition and occurrence count. Links, navigation, code, form controls and SVG text are excluded from automatic annotation. There is no standalone glossary page or appended terms section. Text remains readable without JavaScript; the interactive features require it.

`explorers.mjs` renders the default figure states and embeds only the fixture data used on that page. `explorer-core.cjs` supplies shared rendering and graph statistics; the build copies it to `docs/assets/explorer-core.js`. `docs/assets/explorers.js` binds controls and keyboard interaction. The homepage loads none of these explorer assets. All three figures work without external requests; default figures remain readable without JavaScript.

Heatmap rows retain adjusted-p order rather than clustering. Color limits change only their displayed colors in this example. The forest uses saved metafor DL/z fits to synthetic study estimates; its leave-one-out identifiers are one-based. The network preserves every mapped node and its fixed position, filters only cached edges, and includes isolates in component counts. Network positions and crossings carry no biological meaning. Each figure has a reset; reduced-motion preferences disable transitions.

## Example reports

The homepage and Output link to `examples/standard/results/reports/results_report.html` and `examples/meta/results/reports/meta_analysis_report.html`; Export also offers the accompanying ZIP files. Science links to the meta example. These are outputs of the application’s report renderers, with explicit synthetic-data scope. Their complete linked files remain inside each example project, so the downloaded bundle can be read offline.

The standard example reuses saved synthetic DESeq2 results; its normalized mean counts are independently checked against DESeq2. The meta example runs the application’s combination implementation on constructed study-level estimates and retains both complete testing families. Neither example claims a completed biological study or full workflow validation. Follow phases 6–8 in `reproduction/README.md` to regenerate them, then rebuild the website and verify links, sorting, figure zoom and package contents. The website build does not rerun these calculations.

## Screenshots

Place reviewed image files under `docs/assets/images/` and add them with `imageFigure(file, title, alt, caption)`, which writes a `figure` with a relative `src`; the viewer discovers it automatically. Capture from the application itself — `scripts/capture_gui_matrix.py` shows the technique: the native platform plugin, the light theme, a temporary synthetic project and `QWidget.grab()`, never a desktop grab. Describe what the capture actually shows, and inspect the PNG's chunks for textual, time or generator metadata before committing. A figure whose screenshot is not yet available is written as a labelled placeholder instead, so that a page never implies a capture it does not have.

## Verification

Run the build and `tests/test_docs_site.py`, `tests/test_docs_theme.py`, `tests/test_docs_parameters.py` and `tests/test_docs_learning.py`. Check both volcano controls and reset, inspectable values, label clearance, internal page and anchor links, wide and narrow layouts, search with matching and empty results, keyboard shortcuts, dialog dismissal and focus return, inline definitions, figure zoom, code copying, a repository subpath and a browser session with JavaScript disabled.

A layout check does not establish that a described application behaviour matches the release. Recheck commands, defaults, options and output descriptions against the application revision being documented before publication.
