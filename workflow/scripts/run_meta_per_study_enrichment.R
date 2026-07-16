# Optional per-study functional enrichment for a multi-study meta-analysis (opt-in:
# workflow.per_study_enrichment). For each study S in the per-study manifest, runs GO ORA
# (clusterProfiler::enrichGO) separately on that study's up- and down-regulated gene lists
# over a per-study universe (all tested gene_ids), writes go_ora_up.csv / go_ora_down.csv +
# a dotplot, and records the counts in results/meta/per_study/enrichment_manifest.json.
# Best-effort: any per-study error becomes a `note` on that study; a missing/unmapped OrgDb
# skips ALL studies with a placeholder manifest. The manifest is ALWAYS written; exit 0.

local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

# ggplot2/svglite/scales/RColorBrewer must load BEFORE figure_style.R: palette_spec()
# calls brewer.pal at source time.
suppressMessages({
  library(ggplot2)
  library(svglite)
  library(scales)
  library(RColorBrewer)
  library(jsonlite)
})
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")

in_manifest  <- snakemake@input[["manifest"]]
out_manifest <- snakemake@output[["manifest"]]
orgdb_name <- tryCatch(snakemake@params[["orgdb"]],  error = function(e) NULL)
keytype    <- tryCatch(snakemake@params[["keytype"]], error = function(e) "ENSEMBL")
kegg_org   <- tryCatch(snakemake@params[["kegg"]],   error = function(e) NULL)
ont        <- { v <- tryCatch(snakemake@params[["ont"]], error = function(e) "BP"); if (is.null(v) || !nzchar(v)) "BP" else v }
alpha      <- { v <- suppressWarnings(as.numeric(tryCatch(snakemake@params[["alpha"]], error = function(e) 0.05))); if (length(v) != 1 || is.na(v)) 0.05 else v }
style      <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()

per_dir <- dirname(out_manifest)                 # results/meta/per_study
dir.create(per_dir, recursive = TRUE, showWarnings = FALSE)

# --- style (enrichment figure group, else global) ------------------------------------------------
gp <- getp_for(style, "enrichment")
base_size <- { v <- suppressWarnings(as.numeric(gp("base_font_size", 12))); if (length(v) != 1 || is.na(v)) 12 else v }
pal_spec  <- palette_spec(as.character(gp("palette", "Blue-Red")))
base_family <- resolve_font(as.character(gp("font_family", "")))
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = isTRUE(as.logical(gp("label_bold", FALSE))),
                                title_bold = isTRUE(as.logical(gp("title_bold", FALSE))))
fig_dpi   <- { v <- suppressWarnings(as.numeric(gp("dpi", 300))); if (is.na(v)) 300 else as.integer(v) }
fig_w     <- { v <- suppressWarnings(as.numeric(gp("width_in", 7))); if (is.na(v)) 7 else v }
fig_h     <- { v <- suppressWarnings(as.numeric(gp("height_in", 6))); if (is.na(v)) 6 else v }
save_gg   <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)
label_wrap <- { v <- suppressWarnings(as.integer(gp("enrich_label_wrap", 40))); if (is.na(v)) 40L else v }
show_cat   <- { v <- suppressWarnings(as.integer(gp("enrich_show_category", 15))); if (is.na(v)) 15L else v }

nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

# Strip a trailing version suffix ONLY from Ensembl ids (mirrors run_enrichment.R). Skipped for
# SYMBOL keytype so legitimate LOC-symbols pass through; enrichGO(keyType="ENSEMBL") matches
# nothing against versioned ids, so the strip prevents a green-but-empty ORA.
strip_version <- function(id) {
  if (identical(keytype, "SYMBOL")) return(id)
  v <- grepl("^ENS", id)
  id[v] <- sub("\\.\\d+$", "", id[v])
  id
}
read_ids <- function(path) {
  if (!file.exists(path)) return(character(0))
  df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
  unique(strip_version(df$gene_id))
}
# 0-row enrichResult already yields a header-only frame; hand-build a header only when NULL.
empty_go <- data.frame(ID = character(0), Description = character(0), GeneRatio = character(0),
                       BgRatio = character(0), pvalue = numeric(0), p.adjust = numeric(0),
                       qvalue = numeric(0), geneID = character(0), Count = integer(0))
placeholder_fig <- function(msg, png_path, svg_path) {
  save_gg(ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void(),
          png_path, svg_path)
}

# Read the input manifest as a LIST of lists (simplifyDataFrame=FALSE): write_json(auto_unbox)
# otherwise simplifies studies into a data.frame with list-columns and the loop iterates columns.
mani <- tryCatch(jsonlite::fromJSON(in_manifest, simplifyDataFrame = FALSE, simplifyVector = FALSE),
                 error = function(e) NULL)
studies_in <- tryCatch(mani$studies, error = function(e) NULL)
if (is.null(studies_in)) studies_in <- list()

write_manifest <- function(entries, note = NULL) {
  obj <- list(n_studies = length(entries), ontology = ont, studies = entries)
  if (!is.null(note)) obj$note <- note
  write_json(obj, out_manifest, auto_unbox = TRUE, pretty = TRUE, null = "null")
}
finish <- function() { sink(type = "message"); close(log_con); quit(save = "no", status = 0) }

# --- OrgDb gate: skip ALL studies cleanly if the package is not installed / cannot be loaded ------
has_orgdb <- !is.null(orgdb_name) && nzchar(orgdb_name)
orgdb <- NULL
if (has_orgdb) {
  orgdb <- tryCatch({
    suppressMessages({
      library(clusterProfiler)
      library(orgdb_name, character.only = TRUE)
    })
    get(orgdb_name)
  }, error = function(e) { message("OrgDb unavailable (", orgdb_name, "): ", conditionMessage(e)); NULL })
}
if (!has_orgdb || is.null(orgdb)) {
  # Every study is listed with a note so the report/GUI can explain the skip.
  entries <- lapply(studies_in, function(S) {
    s <- tryCatch(S$study, error = function(e) NA_character_)
    list(study = s, n_up_terms = 0, n_down_terms = 0, dotplot = NA_character_,
         up_csv = NA_character_, down_csv = NA_character_,
         note = "organism not mapped (no Bioconductor OrgDb for this organism); GO ORA skipped")
  })
  write_manifest(entries, note = "organism not mapped: no Bioconductor OrgDb available")
  finish()
}

# ORA on one gene-id vector over `universe`. keyType is the params keytype (ENSEMBL/SYMBOL/...),
# passed to enrichGO directly (no bitr route). readable=TRUE renders geneID as symbols. Params
# mirror run_enrichment.R. Returns the enrichResult (or NULL); writes CSV (header even when empty).
run_ora <- function(genes, universe, csv_path) {
  genes <- unique(genes[!is.na(genes)])
  ego <- if (length(genes) >= 1) tryCatch(
    enrichGO(gene = genes, universe = universe, OrgDb = orgdb, keyType = keytype,
             ont = ont, pAdjustMethod = "BH", pvalueCutoff = alpha, qvalueCutoff = 0.20,
             minGSSize = 10, maxGSSize = 500, readable = TRUE),
    error = function(e) { message("enrichGO failed: ", conditionMessage(e)); NULL }) else NULL
  if (is.null(ego)) write.csv(empty_go, csv_path, row.names = FALSE)
  else write.csv(as.data.frame(ego), csv_path, row.names = FALSE)  # 0-row -> header-only frame
  ego
}

# enrichplot dotplot through the project palette (p.adjust on the sequential ramp, reversed so the
# most significant term is darkest; long labels wrapped; no embedded title). Mirrors the sibling
# make_enrichment_figures.R themed_dotplot.
have_ep <- requireNamespace("enrichplot", quietly = TRUE)
if (have_ep) suppressMessages(library(enrichplot))
themed_dotplot <- function(x, n) {
  p <- enrichplot::dotplot(x, showCategory = n,
                           label_format = function(lbl) scales::label_wrap(label_wrap)(lbl))
  suppressWarnings(
    p + scale_color_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse") +
        scale_fill_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse")
  ) + labs(title = NULL) + style_theme(theme_bw)
}

entries <- list()
for (S in studies_in) {
  s <- tryCatch(as.character(S$study), error = function(e) NA_character_)
  ent <- tryCatch({
    if (is.na(s) || !nzchar(s)) stop("manifest study has no name")
    sdir  <- file.path(per_dir, s)
    tdir  <- file.path(sdir, "tables")
    edir  <- file.path(sdir, "enrichment")
    dir.create(edir, recursive = TRUE, showWarnings = FALSE)
    up_csv   <- file.path(edir, "go_ora_up.csv")
    down_csv <- file.path(edir, "go_ora_down.csv")
    dot_png  <- file.path(edir, "go_dotplot.png")
    dot_svg  <- file.path(edir, "go_dotplot.svg")

    de_path <- file.path(tdir, "de_results.csv")
    if (!file.exists(de_path)) stop("de_results.csv missing for study ", s)
    universe <- read_ids(de_path)
    up_ids   <- read_ids(file.path(tdir, "upregulated.csv"))
    down_ids <- read_ids(file.path(tdir, "downregulated.csv"))

    ego_up   <- run_ora(up_ids,   universe, up_csv)
    ego_down <- run_ora(down_ids, universe, down_csv)
    n_up   <- nrows(ego_up)
    n_down <- nrows(ego_down)

    # Dotplot of the up ORA; fall back to down; placeholder when both empty (file always exists).
    dot_obj <- if (n_up > 0) ego_up else if (n_down > 0) ego_down else NULL
    dot_note <- if (n_up == 0 && n_down > 0) "Down-regulated genes (no up-regulated GO terms)" else NULL
    if (have_ep && !is.null(dot_obj)) {
      ok <- tryCatch({
        save_gg(themed_dotplot(dot_obj, show_cat) + labs(caption = dot_note), dot_png, dot_svg); TRUE
      }, error = function(e) { message("dotplot failed for ", s, ": ", conditionMessage(e)); FALSE })
      if (!ok) placeholder_fig("GO dotplot could not be generated", dot_png, dot_svg)
    } else {
      placeholder_fig(if (!have_ep) "enrichplot unavailable" else "No GO terms passed the cutoff",
                      dot_png, dot_svg)
    }

    # Surface this study's enrichment in its own index.html (written earlier by the figures rule),
    # else the dotplot + go_ora_*.csv are orphaned on disk with no link from any report the user
    # opens. Wrapped in <!--ENR--> markers so a rerun replaces rather than duplicates the section.
    idx <- file.path(sdir, "index.html")
    if (file.exists(idx)) {
      enr_html <- sprintf(paste0(
        '<!--ENR--><h2>Functional enrichment (GO %s ORA)</h2>',
        '<p>%d up-regulated · %d down-regulated GO terms (FDR&lt;%.3g)%s</p>',
        '<figure><img src="enrichment/go_dotplot.png" alt="GO ORA dotplot">',
        '<figcaption>GO over-representation (up-regulated genes; down-regulated if no up terms)</figcaption></figure>',
        '<ul><li><a href="enrichment/go_ora_up.csv">go_ora_up.csv</a></li>',
        '<li><a href="enrichment/go_ora_down.csv">go_ora_down.csv</a></li></ul><!--/ENR-->'),
        ont, n_up, n_down, alpha, if (!is.null(dot_note)) paste0(" — ", dot_note) else "")
      html <- paste(readLines(idx, warn = FALSE), collapse = "\n")
      html <- sub("(?s)<!--ENR-->.*?<!--/ENR-->", "", html, perl = TRUE)
      html <- sub("</body>", paste0(enr_html, "</body>"), html, fixed = TRUE)
      writeLines(html, idx)
    }

    rel <- function(p) file.path("results/meta/per_study", s, "enrichment", basename(p))
    list(study = s, n_up_terms = n_up, n_down_terms = n_down,
         dotplot = rel(dot_png), up_csv = rel(up_csv), down_csv = rel(down_csv), note = NULL)
  }, error = function(e) {
    message("per-study enrichment failed for ", s, ": ", conditionMessage(e))
    list(study = s, n_up_terms = 0, n_down_terms = 0, dotplot = NA_character_,
         up_csv = NA_character_, down_csv = NA_character_,
         note = paste("enrichment failed:", conditionMessage(e)))
  })
  entries[[length(entries) + 1]] <- ent
}

write_manifest(entries)
finish()
