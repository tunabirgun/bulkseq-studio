# Vendored JavaScript for the interactive PPI viewer

Pinned, offline copies (no CDN at runtime). Downloaded from unpkg.com.

| File | Library | Version |
|------|---------|---------|
| cytoscape.min.js | cytoscape | 3.30.2 |
| layout-base.js | layout-base | 2.0.1 |
| cose-base.js | cose-base | 2.2.0 |
| cytoscape-fcose.js | cytoscape-fcose | 2.2.0 |
| cytoscape-layout-utilities.js | cytoscape-layout-utilities | 1.1.1 |
| cytoscape-svg.js | cytoscape-svg | 0.4.0 |

These ship automatically in the PyInstaller build (the spec's `collect()` walks
`app/assets/` recursively) and are resolved at runtime via `app_root()` (which
returns `sys._MEIPASS` when frozen). `cytoscape-layout-utilities` registers itself on the
global `cytoscape`; fcose's `packComponents` option uses it and is a no-op without it. To update, re-download the pinned version
from `https://unpkg.com/<pkg>@<version>/...` and update this table.
