from __future__ import annotations

import csv
import html
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"


def _text(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def _assert_gsea_title_contract(source: str) -> None:
    assert "GSEA_GENE_SET_ID <- 1L" in source
    assert "gsea_selected_title <- function" in source
    assert 'all(c("Description", "ID") %in% names(tab))' in source
    assert 'sprintf("%s (%s)", description, identifier)' in source
    assert source.count("geneSetID = GSEA_GENE_SET_ID") == 2
    assert "title = go_gsea_title" in source
    assert "title = kegg_gsea_title" in source
    assert "have_ep && !is.null(go_gsea_title)" in source
    assert "have_ep && !is.null(kegg_gsea_title)" in source
    assert "selected geneSetID=1 row lacks a nonblank Description or ID" in source


def _assert_svg_title_matches_csv(svg_path: Path, csv_path: Path) -> str:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    description = (row.get("Description") or "").strip()
    identifier = (row.get("ID") or "").strip()
    assert description and identifier
    expected = f"{description} ({identifier})"
    rendered_text = " ".join("".join(node.itertext()).strip() for node in ET.parse(svg_path).iter())
    assert expected in html.unescape(rendered_text)
    return expected


def _assert_set_overlap_opaque_contract(source: str) -> None:
    assert "save_gg <- make_save_gg" in source
    assert "save_gg(p, out[[\"png\"]], out[[\"svg\"]])" in source
    assert "save_gg(dp, out[[\"png\"]], out[[\"svg\"]], w = fig_w, h = ov_h)" in source
    assert 'plot.background = element_rect(fill = "white"' in source
    assert "ggsave(" not in source


def _assert_external_enrichment_label_contract(source: str) -> None:
    assert source.count('node_label = "none"') >= 3
    assert source.count("set.seed(42)") >= 2
    assert "external_network_label_layout <- function" in source
    assert "external_network_labels <- function" in source
    assert 'lab$side[x_order[seq_len(left_count)]] <- "left"' in source
    assert 'lab$label_x <- ifelse(lab$side == "left", x_range[1] - gap, x_range[2] + gap)' in source
    assert 'xend = label_x, yend = label_y' in source
    assert 'p$layers <- append(list(leader_layer), p$layers)' in source
    assert 'label = display_label, hjust = hjust' in source
    assert 'fill = "white"' in source
    assert 'legend.position = "bottom"' in source
    assert 'barwidth = grid::unit(3.0, "in")' in source
    assert 'coord_equal(xlim = layout$xlim, ylim = layout$ylim' in source
    assert 'stop("Enrichment-network label anchors must remain outside the graph hull")' in source
    assert "outlined_network_nodes <- function" in source
    assert "shape = 21" in source
    assert 'colour = "grey35"' in source
    assert "network_w <- max(fig_w, 11)" in source
    assert "network_h <- max(fig_h, 8.5)" in source


def _assert_diagnostic_annotation_contract(core: str, corr: str) -> None:
    assert 'label = sprintf("raw p = %.3g", alpha_thr)' in core
    assert "number_labels <- matrix(" in corr
    assert 'number_labels[is.finite(cm) & abs(cm - max_corr)' in corr
    assert "display_numbers = if (show_num) number_labels else FALSE" in corr


def _assert_ppi_geometry_contract(source: str) -> None:
    assert "deterministic component-wise crossing-optimised circular shelf" in source
    assert "make_component_layout <- function" in source
    assert "component_geometry_clear <- function" in source
    assert "validate_exported_ppi_svg <- function" in source
    assert 'validate_exported_ppi_svg(out[["svg"]])' in source
    assert 'stop("exported PPI SVG contains overlapping node disks")' in source
    assert "contacts non-owner node" in source
    assert "unrelated edges" in source
    assert "has only %.2f visible report pixels" in source
    assert "PPI_EDGE_BAND_CUTS[PPI_EDGE_BAND_CUTS > score_thr" in source
    assert 'name = "STRING combined score"' in source
    assert "no node labels in the static topology panel" in source
    assert "Static view emphasizes topology" in source
    assert "interactive PPI view" in source
    assert "results/networks/ppi_hub_genes.csv" in source
    assert "hub_id" not in source and "hub_key" not in source
    assert "node_plot$degree_band[match(vertex_order, node_plot$id)]" in source
    assert "nodes_df$degree_band[match(vertex_order, nodes_df$id)]" not in source
    assert "PPI_NODE_CLEARANCE <- 0.55 * max(PPI_NODE_LAYOUT_RADIUS)" in source
    assert "PPI_EDGE_NODE_CLEARANCE <- 0.65 * max(PPI_NODE_LAYOUT_RADIUS)" in source
    assert "PPI_EDGE_EDGE_CLEARANCE <- 0.90 * max(PPI_NODE_LAYOUT_RADIUS)" in source
    assert "ggrepel" not in source
    assert "label_left" not in source and "label_right" not in source


def _assert_ppi_bounded_static_layout_contract(source: str) -> None:
    assert "PPI_CIRCLE_SCORE_PAIR_BUDGET <- 250000" in source
    assert "PPI_CIRCLE_SCORE_EVALUATION_BUDGET <- 96L" in source
    assert "PPI_DENSE_STATIC_PLACEHOLDER" in source
    assert "Dense PPI topology cannot be represented as a crossing-free static view." in source
    assert "interactive PPI view, results/networks/ppi_hub_genes.csv, and the network tables" in source
    assert "outerplanar_edge_bound <- if (length(ids) <= 1L) 0L else 2L * length(ids) - 3L" in source
    assert "nrow(component_edges) > outerplanar_edge_bound" in source
    assert "crossing-free static-layout search exhausted its deterministic %d-evaluation budget" in source
    assert "edge_pair_count > PPI_CIRCLE_SCORE_PAIR_BUDGET" in source
    assert "state$layout_method <- \"static topology unavailable\"" in source
    assert "check_status <- \"WARNING\"" in source
    assert source.index('igraph::write_graph(g, out[["graphml"]]') < source.index("fig_ok <- tryCatch({")


def _parse_r_colour(colour: str) -> tuple[float, float, float]:
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", colour):
        return tuple(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    grey = re.fullmatch(r"gr[ae]y(\d{1,3})", colour, flags=re.IGNORECASE)
    if grey:
        value = int(grey.group(1)) / 100
        return value, value, value
    raise AssertionError(f"unsupported test colour: {colour}")


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _effective_contrast_against_white(colour: str, alpha: float) -> float:
    foreground = _parse_r_colour(colour)
    composite = tuple(alpha * channel + (1 - alpha) for channel in foreground)
    return 1.05 / (_relative_luminance(composite) + 0.05)


def _assert_ppi_edge_contrast_contract(source: str) -> float:
    colour_match = re.search(r'^PPI_EDGE_COLOUR <- "([^"]+)"$', source, re.MULTILINE)
    alpha_match = re.search(r"^PPI_EDGE_ALPHA <- ([0-9.]+)$", source, re.MULTILINE)
    minimum_match = re.search(r"^PPI_EDGE_MIN_CONTRAST <- ([0-9.]+)$", source, re.MULTILINE)
    assert colour_match and alpha_match and minimum_match
    assert "edge_contrast_against_white <- function" in source
    assert "PPI_EDGE_EFFECTIVE_CONTRAST < PPI_EDGE_MIN_CONTRAST" in source
    assert "colour = PPI_EDGE_COLOUR, alpha = PPI_EDGE_ALPHA" in source
    ratio = _effective_contrast_against_white(
        colour_match.group(1), float(alpha_match.group(1))
    )
    assert ratio >= float(minimum_match.group(1)), f"PPI edge contrast is only {ratio:.2f}:1"
    return ratio


def _assert_volcano_label_geometry_contract(source: str) -> None:
    assert "volcano_add_ranked_key <- function" in source
    assert "grid::grobWidth(g)" in source
    assert "grid::grobHeight(g)" in source
    assert 'sprintf("%02d  %s  (%+.2f)"' in source
    assert "required_height_in > panel_h_in" in source
    assert "em_height_in <- font_points / 72" in source
    assert "minimum_data_fraction <- 0.34" in source
    assert "required_panel_w_in <- sum(reserved_key_in) /" in source
    assert "canvas_w <- max(canvas_w, required_canvas_w_in)" in source
    assert "data_panel_in < minimum_data_fraction * panel_w_in" in source
    assert 'stop("Volcano ranked keys require a wider figure canvas")' in source
    assert 'stop("Volcano ranked keys require a taller figure canvas")' in source
    assert 'shape = 17, size = marker_size_mm + 0.65' in source
    assert 'shape = 21, fill = "white"' in source
    assert "p_vol <- volcano_add_ranked_key(" in source
    assert 'attr(keyed_plot, "volcano_canvas_width") <- canvas_w' in source
    assert 'attr(p_vol, "volcano_canvas_width", exact = TRUE)' in source
    assert "label_size = 4" in source
    geometry = source[
        source.index("# ---- Volcano ranked-key geometry") :
        source.index("# ---- End volcano-label geometry")
    ]
    assert "geom_segment" not in geometry
    assert "leader" not in geometry.lower()
    assert "volcano_add_collision_free_labels" not in source
    assert "volcano_add_outer_corridor_labels" not in source
    assert "volcano_add_bounded_repel_labels" not in source


def _svg_text_boxes(
    svg: str, expected_labels: set[str]
) -> dict[str, tuple[float, float, float, float]]:
    root = ET.fromstring(svg)
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "text":
            continue
        label = html.unescape("".join(element.itertext())).strip()
        if label not in expected_labels:
            continue
        assert label not in boxes, f"duplicate SVG label: {label}"
        x = float(element.attrib["x"])
        y = float(element.attrib["y"])
        width = float(element.attrib["textLength"].removesuffix("px"))
        font_match = re.search(r"font-size:\s*([0-9.]+)px", element.attrib.get("style", ""))
        assert font_match, f"missing font size for {label}"
        font_size = float(font_match.group(1))
        anchor = element.attrib.get("text-anchor", "start")
        if anchor == "middle":
            left = x - width / 2
        elif anchor == "end":
            left = x - width
        else:
            left = x
        # Italic end glyphs can extend beyond textLength; include a small em
        # allowance so the automated gate agrees with the rendered appearance.
        italic_pad = 0.08 * font_size if "font-style: italic" in element.attrib.get("style", "") else 0
        boxes[label] = (
            left - italic_pad,
            y - 0.82 * font_size,
            left + width + italic_pad,
            y + 0.24 * font_size,
        )
    assert boxes.keys() == expected_labels, (
        f"expected {sorted(expected_labels)}, found {sorted(boxes)}"
    )
    return boxes


def _assert_svg_labels_do_not_collide(svg: str, expected_labels: set[str]) -> None:
    root = ET.fromstring(svg)
    view_box = [float(value) for value in root.attrib["viewBox"].split()]
    canvas_left, canvas_top, canvas_width, canvas_height = view_box
    canvas_right = canvas_left + canvas_width
    canvas_bottom = canvas_top + canvas_height
    boxes = _svg_text_boxes(svg, expected_labels)
    for label, (left, top, right, bottom) in boxes.items():
        assert left >= canvas_left and right <= canvas_right, f"{label} leaves the SVG horizontally"
        assert top >= canvas_top and bottom <= canvas_bottom, f"{label} leaves the SVG vertically"
    labels = sorted(boxes)
    for index, first in enumerate(labels):
        left_a, top_a, right_a, bottom_a = boxes[first]
        for second in labels[index + 1 :]:
            left_b, top_b, right_b, bottom_b = boxes[second]
            overlap_x = min(right_a, right_b) - max(left_a, left_b)
            overlap_y = min(bottom_a, bottom_b) - max(top_a, top_b)
            assert not (overlap_x > 0.1 and overlap_y > 0.1), (
                f"SVG labels overlap: {first!r} and {second!r} "
                f"({overlap_x:.2f} x {overlap_y:.2f}px)"
            )


def _assert_svg_volcano_key(
    svg: str,
    *,
    left_rows: set[str],
    right_rows: set[str],
    minimum_embedded_font_px: float = 8.0,
    embedded_width_px: float = 760.0,
) -> None:
    expected_rows = left_rows | right_rows
    assert not left_rows & right_rows
    _assert_svg_labels_do_not_collide(svg, expected_rows)
    boxes = _svg_text_boxes(svg, expected_rows)
    root = ET.fromstring(svg)
    view_box = [float(value) for value in root.attrib["viewBox"].split()]
    view_width = view_box[2]
    dashed_verticals: list[float] = []
    solid_lines: list[ET.Element] = []
    row_font_sizes: dict[str, float] = {}
    text_values: set[str] = set()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        style = element.attrib.get("style", "").lower()
        if tag == "line":
            x1 = float(element.attrib["x1"])
            x2 = float(element.attrib["x2"])
            if "stroke-dasharray" in style:
                if abs(x1 - x2) < 1e-7:
                    dashed_verticals.append(x1)
            elif "stroke:" in style:
                solid_lines.append(element)
        if tag != "text":
            continue
        value = html.unescape("".join(element.itertext())).strip()
        text_values.add(value)
        if value in expected_rows:
            match = re.search(r"font-size:\s*([0-9.]+)px", style)
            assert match, f"missing key font size for {value}"
            row_font_sizes[value] = float(match.group(1))
    assert len(dashed_verticals) == 2, "expected two dashed fold-change thresholds"
    zero_x = sum(dashed_verticals) / 2
    for row in left_rows:
        assert boxes[row][2] < zero_x, f"down key crossed zero: {row}"
    for row in right_rows:
        assert boxes[row][0] > zero_x, f"up key crossed zero: {row}"
    assert not solid_lines, "volcano ranked key must not contain association leaders"
    assert {"Down key", "Up key"}.issubset(text_values)
    assert any("ranked by adjusted p-value" in value.lower() for value in text_values)
    for row, font_size in row_font_sizes.items():
        embedded_size = font_size * embedded_width_px / view_width
        assert embedded_size >= minimum_embedded_font_px, (
            f"volcano key row is only {embedded_size:.2f}px at embed: {row}"
        )

def _assert_directional_go_scope_contract(source: str) -> None:
    assert "combined_n <- nrows(ego_all)" in source
    assert "up_n <- nrows(obj$ego_up)" in source
    assert "down_n <- nrows(obj$ego_down)" in source
    assert "go_plot_obj <- obj$ego_up" in source
    assert "go_plot_obj <- obj$ego_down" in source
    assert "if (up_n >= down_n)" in source
    assert "make_cnet(go_plot_obj" in source
    assert "make_emap(go_plot_obj" in source
    assert source.count("themed_dotplot(go_plot_obj") == 2
    assert "Up-regulated ORA selected (separate BH family)" in source
    assert "Down-regulated ORA selected (separate BH family)" in source
    assert "Gene-concept network displays %d of %d selected terms" in source
    assert "min(cnet_cat, nrows(go_plot_obj))" in source
    assert "Adjusted-significant terms - combined: %d; down-regulated: %d" in source
    assert "Adjusted-significant terms - combined: %d; up-regulated: %d" in source
    assert source.count("labs(caption = go_scope_caption)") == 3
    assert "labs(caption = cnet_caption)" in source
    assert "No GO Biological Process terms met the adjusted criterion in the combined, up-regulated, or down-regulated ORAs" in source
    assert "No combined-foreground GO %s terms met the adjusted criterion" in source


def _assert_sample_distance_label_contract(source: str) -> None:
    assert "sample_distance_label_layout <- function" in source
    assert "grid::grobWidth(g)" in source
    assert "grid::grobHeight(g)" in source
    assert "candidate_angles <- c(0, 45, 90)" in source
    assert "projected_widths + gap_in <= cell_spacing_in" in source
    assert "fit_sample_distance_heatmap <- function" in source
    assert "dist_min_cell_width_pt <- max(18, 1.6 * base_size)" in source
    assert "cellwidth = cell_width_pt, cellheight = cell_height_pt" in source
    assert "finalize_heatmap_gtable(ph$gtable, min_w = 0, min_h = 0)" in source
    assert "w = dist_render$dim[1], h = dist_render$dim[2]" in source
    assert "angle_col = angle_col" in source
    assert "angle_col = 45" not in source


def _assert_correlation_geometry_contract(source: str) -> None:
    assert "measure_correlation_text <- function" in source
    assert "correlation_minimum_cell_size <- function" in source
    assert "correlation_label_layout <- function" in source
    assert "fit_correlation_heatmap <- function" in source
    assert "candidate_angles <- c(0, 45, 90)" in source
    assert "projected + as.numeric(gap_pt) <= as.numeric(cell_width_pt)" in source
    assert "font_floor <- max(20, 1.8 * as.numeric(fontsize))" in source
    assert "cellwidth = cell_width_pt, cellheight = cell_height_pt" in source
    assert "finalize_heatmap_gtable(ph$gtable, min_w = 0, min_h = 0)" in source
    assert "width = corr_render$dim[1]" in source
    assert "height = corr_render$dim[2]" in source
    assert "angle_col = 45" not in source


def _assert_enrichment_placeholder_wrap_contract(source: str) -> None:
    assert "wrap_placeholder_message <- function" in source
    assert "available_width_in <- max(0.5" in source
    assert "grid::grobWidth(grid::textGrob(x, gp = text_gp))" in source
    assert 'paste(collapse = "\\n")' in source
    assert "label = wrapped" in source
    assert "hjust = 0.5, vjust = 0.5, lineheight = 1.15" in source
    assert "save_gg(p, png_path, svg_path, w = w, h = h)" in source


def _assert_sample_distance_smooth_legend_contract(source: str) -> None:
    assert "smooth_continuous_legend <- function" in source
    assert 'gtable$layout$name == "legend"' in source
    assert 'inherits(child, "rect")' in source
    assert "length(child$gp$fill) > 1L" in source
    assert "height = sum(source_rect$height)" in source
    assert "fill_colors <- rep_len(" in source
    assert "length(source_rect$height)" in source
    assert "grid::linearGradient(" in source
    assert "stops = seq(0, 1, length.out = length(fill_colors))" in source
    assert "ph$gtable <- smooth_continuous_legend(ph$gtable)" in source
    assert source.count("smooth_continuous_legend <- function") == 1
    assert source.count("smooth_continuous_legend(") == 1


def _assert_seam_free_continuous_svg(svg: str) -> None:
    assert svg.count("<linearGradient") == 1
    assert len(re.findall(r"fill: url\(#pat-", svg)) == 1
    rects = re.findall(r"<rect\b[^>]*/>", svg)
    subpoint_rects = []
    for rect in rects:
        height = re.search(r"height=['\"]([0-9.]+)['\"]", rect)
        if height and float(height.group(1)) < 1:
            subpoint_rects.append(rect)
    assert not subpoint_rects


def test_contrast_colour_mapping_is_semantic_and_order_invariant() -> None:
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript is unavailable on this host")
    style_path = (SCRIPTS / "figure_style.R").as_posix().replace("'", "\\'")
    expr = f"""
source('{style_path}')
p <- c('#2C7BB6','#C0392B','#2E7D32','#B26A00','#6A1B9A')
cfg <- list(numerator='treated', denominator='control')
a <- contrast_color_map(c('treated','control','batch'), cfg, p)
b <- contrast_color_map(c('batch','control','treated'), cfg, p)
stopifnot(identical(a[sort(names(a))], b[sort(names(b))]))
stopifnot(a[['control']] == p[1], a[['treated']] == p[2])
"""
    result = subprocess.run(
        [rscript, "-e", expr], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr


def test_all_group_annotation_call_sites_use_contrast_mapping() -> None:
    style = _text("figure_style.R")
    core = _text("make_figures.R")
    corr = _text("sample_correlation.R")
    assert "ans[denominator] <- discrete[1]" in style
    assert "ans[numerator] <- discrete[2]" in style
    assert core.count("contrast_color_map(") >= 4
    assert "scale_colour_manual(values = pca_disc" in core
    assert "ann_cols <- contrast_color_map" in core
    assert core.count("hm_ann_cols <- contrast_color_map") == 2
    assert "ann_colmap <- contrast_color_map" in corr


def test_placeholder_and_ppi_devices_are_explicitly_opaque() -> None:
    style = _text("figure_style.R")
    ppi = _text("build_string_network.R")
    corr = _text("sample_correlation.R")
    enrich = _text("make_enrichment_figures.R")
    overlap = _text("run_set_overlap.R")
    assert style.count('bg = "white"') >= 2
    assert ppi.count('bg = "white"') >= 4
    assert corr.count('bg = "white"') >= 4
    assert enrich.count('bg = "white"') >= 2
    _assert_set_overlap_opaque_contract(overlap)
    assert 'plot.background = element_rect(fill = "white"' in ppi


def test_set_overlap_opaque_contract_rejects_a_transparent_raw_device() -> None:
    overlap = _text("run_set_overlap.R")
    broken = overlap.replace(
        "save_gg <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)",
        "save_gg <- function(...) ggsave(..., bg = NA)",
        1,
    )
    assert broken != overlap
    with pytest.raises(AssertionError):
        _assert_set_overlap_opaque_contract(broken)


def test_extrema_and_dense_layouts_reserve_visible_space() -> None:
    style = _text("figure_style.R")
    core = _text("make_figures.R")
    enrich = _text("make_enrichment_figures.R")
    ppi = _text("build_string_network.R")
    assert "diag(mat_display) <- NA_real_" in core
    assert "off-diagonal\\nEuclidean distance" in core
    assert "cap_labels <- lab[lab$capped" in core
    assert "lab$padj_rank <- seq_len(nrow(lab))" in core
    assert core.count("heatmap_cell_w_fill(") == 2
    # Check each heatmap role, not a global helper-call count: sample-distance
    # legitimately measures a probe and its final gtable, while the two DEG
    # paths each finalize their own fixed-cell gtable exactly once.
    assert "finalize_heatmap_gtable(ph$gtable, min_w = 0, min_h = 0)" in core
    assert re.search(
        r"measured\s*<-\s*finalize_heatmap_gtable\(\s*ph\$gtable,\s*"
        r"min_w\s*=\s*as\.numeric\(min_dim\[1\]\),\s*"
        r"min_h\s*=\s*as\.numeric\(min_dim\[2\]\)\s*\)",
        core,
    )
    assert re.search(
        r"hm_render\s*<-\s*finalize_heatmap_gtable\(ph2\$gtable,\s*"
        r"hm_min_dim\[1\],\s*hm_min_dim\[2\]\)",
        core,
    )
    assert re.search(
        r"hm_render\s*<-\s*finalize_heatmap_gtable\(ph\$gtable,\s*"
        r"hm_min_dim\[1\],\s*hm_min_dim\[2\]\)",
        core,
    )
    assert "stack_heatmap_legends <- function" in style
    assert 'gtable::gtable_add_padding' in style
    assert "scale_shape_manual" not in core
    assert enrich.count("expansion(mult = c(0.03, 0.16))") >= 2
    _assert_external_enrichment_label_contract(enrich)
    assert 'coord_equal(clip = "off")' in ppi
    assert "plot.margin = margin(18, 24, 18, 18)" in ppi
    _assert_ppi_geometry_contract(ppi)
    _assert_volcano_label_geometry_contract(core)


def test_ppi_edges_meet_the_declared_effective_contrast() -> None:
    ratio = _assert_ppi_edge_contrast_contract(_text("build_string_network.R"))
    assert ratio >= 3


def test_ppi_edge_contrast_gate_rejects_the_previous_style() -> None:
    ppi = _text("build_string_network.R")
    broken = re.sub(
        r'^PPI_EDGE_COLOUR <- "[^"]+"$',
        'PPI_EDGE_COLOUR <- "grey65"',
        ppi,
        count=1,
        flags=re.MULTILINE,
    )
    broken = re.sub(
        r"^PPI_EDGE_ALPHA <- [0-9.]+$",
        "PPI_EDGE_ALPHA <- 0.48",
        broken,
        count=1,
        flags=re.MULTILINE,
    )
    assert broken != ppi
    assert _effective_contrast_against_white("grey65", 0.48) == pytest.approx(1.48, abs=0.01)
    with pytest.raises(AssertionError):
        _assert_ppi_edge_contrast_contract(broken)


def test_svg_volcano_key_gate_rejects_a_clipped_full_row() -> None:
    row = "01  lncRNA:CR43883  (-2.36)"
    broken_svg = f"""<svg viewBox='0 0 432 360' xmlns='http://www.w3.org/2000/svg'>
<line x1='180' y1='330' x2='180' y2='20' style='stroke: #999999; stroke-dasharray: 4,4;' />
<line x1='250' y1='330' x2='250' y2='20' style='stroke: #999999; stroke-dasharray: 4,4;' />
<text x='-4' y='80' style='font-size: 12px;' textLength='142px'>{row}</text>
<text x='12' y='45' style='font-size: 12px;' textLength='50px'>Down key</text>
<text x='340' y='45' style='font-size: 12px;' textLength='35px'>Up key</text>
<text x='20' y='345' style='font-size: 10px;' textLength='250px'>Keys are ranked by adjusted p-value.</text>
</svg>"""
    with pytest.raises(AssertionError, match="leaves the SVG horizontally"):
        _assert_svg_volcano_key(
            broken_svg, left_rows={row}, right_rows=set()
        )


def test_svg_volcano_key_gate_rejects_an_association_leader() -> None:
    row = "01  gene_A  (-2.00)"
    broken_svg = f"""<svg viewBox='0 0 432 360' xmlns='http://www.w3.org/2000/svg'>
<line x1='180' y1='330' x2='180' y2='20' style='stroke: #999999; stroke-dasharray: 4,4;' />
<line x1='250' y1='330' x2='250' y2='20' style='stroke: #999999; stroke-dasharray: 4,4;' />
<line x1='140' y1='200' x2='80' y2='80' style='stroke: #737373; stroke-width: 1.4;' />
<text x='20' y='80' style='font-size: 12px;' textLength='100px'>{row}</text>
<text x='20' y='45' style='font-size: 12px;' textLength='50px'>Down key</text>
<text x='340' y='45' style='font-size: 12px;' textLength='35px'>Up key</text>
<text x='20' y='345' style='font-size: 10px;' textLength='250px'>Keys are ranked by adjusted p-value.</text>
</svg>"""
    with pytest.raises(AssertionError, match="must not contain association leaders"):
        _assert_svg_volcano_key(
            broken_svg, left_rows={row}, right_rows=set()
        )


def test_volcano_ranked_key_contract_rejects_an_inverted_capacity_gate() -> None:
    core = _text("make_figures.R")
    broken = core.replace(
        "required_height_in > panel_h_in",
        "required_height_in < panel_h_in",
        1,
    )
    assert broken != core
    with pytest.raises(AssertionError):
        _assert_volcano_label_geometry_contract(broken)


def test_volcano_ranked_key_contract_rejects_fixed_width_only_layout() -> None:
    core = _text("make_figures.R")
    broken = core.replace(
        "canvas_w <- max(canvas_w, required_canvas_w_in)",
        "canvas_w <- canvas_w",
        1,
    )
    assert broken != core
    with pytest.raises(AssertionError):
        _assert_volcano_label_geometry_contract(broken)


def test_volcano_dense_capped_and_regular_key_renders_without_clipping(
    tmp_path: Path,
) -> None:
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript is unavailable on this host")
    package_probe = subprocess.run(
        [
            rscript,
            "-e",
            "quit(status=ifelse(requireNamespace('ggplot2',quietly=TRUE)&&requireNamespace('svglite',quietly=TRUE),0,77))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if package_probe.returncode == 77:
        pytest.skip("ggplot2 or svglite is unavailable in the R environment")
    assert package_probe.returncode == 0, package_probe.stderr

    core = _text("make_figures.R")
    start = core.index("# ---- Volcano ranked-key geometry")
    end = core.index("# ---- End volcano-label geometry", start)
    helper = core[start:end]
    svg_path = tmp_path / "dense-volcano-key.svg"
    r_path = svg_path.as_posix().replace("'", "\\'")
    capped_left = [f"leftg_{index:02d}" for index in range(1, 7)]
    capped_right = [f"rightg_{index:02d}" for index in range(1, 7)]
    regular = ["left_A", "left_B", "right_A", "right_B"]
    all_labels = capped_left + capped_right + regular
    effects = [
        *[-0.35 - 0.15 * index for index in range(6)],
        *[0.35 + 0.15 * index for index in range(6)],
        -0.55,
        -1.20,
        0.55,
        1.20,
    ]
    r_labels = ",".join(f"'{label}'" for label in all_labels)
    script = f"""
suppressPackageStartupMessages(library(ggplot2))
{helper}
labels <- data.frame(
  log2FoldChange=c(seq(-0.35,-1.10,by=-0.15),seq(0.35,1.10,by=0.15),-0.55,-1.20,0.55,1.20),
  y_plot=c(rep(10,12),8.8,8.1,8.8,8.1),
  capped=c(rep(TRUE,12),rep(FALSE,4)),
  direction=c(rep('Down',6),rep('Up',6),'Down','Down','Up','Up'),
  label=c({r_labels}), padj_rank=seq_len(16), stringsAsFactors=FALSE
)
p <- ggplot(labels,aes(log2FoldChange,y_plot)) + geom_point() +
  geom_vline(xintercept=c(-1,1),linetype='dashed',colour='grey60') +
  scale_colour_manual(values=c(Down='#2C7BB6',`n.s.`='#CCCCCC',Up='#C0392B')) +
  theme_bw()
p <- volcano_add_ranked_key(
  p, labels, xm=4, ytop=16, canvas_w=6, canvas_h=5,
  label_size=4, marker_size_mm=1.4
)
svglite::svglite('{r_path}',width=6,height=5,bg='white')
print(p)
grDevices::dev.off()
"""
    script_path = tmp_path / "dense-volcano-key.R"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [rscript, "--vanilla", str(script_path)], capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert svg_path.is_file(), result.stdout + result.stderr
    rows = {
        label: f"{rank:02d}  {label}  ({effect:+.2f})"
        for rank, (label, effect) in enumerate(zip(all_labels, effects), start=1)
    }
    _assert_svg_volcano_key(
        svg_path.read_text(encoding="utf-8"),
        left_rows={rows[label] for label in capped_left + regular[:2]},
        right_rows={rows[label] for label in capped_right + regular[2:]},
    )

    adaptive_svg_path = tmp_path / "adaptive-width-volcano-key.svg"
    adaptive_r_path = adaptive_svg_path.as_posix().replace("'", "\\'")
    long_labels = [f"{label}_with_a_measured_long_identifier" for label in all_labels]
    r_long_labels = ",".join(f"'{label}'" for label in long_labels)
    adaptive_script = f"""
suppressPackageStartupMessages(library(ggplot2))
{helper}
labels <- data.frame(
  log2FoldChange=c(seq(-0.35,-1.10,by=-0.15),seq(0.35,1.10,by=0.15),-0.55,-1.20,0.55,1.20),
  y_plot=c(rep(10,12),8.8,8.1,8.8,8.1),
  capped=c(rep(TRUE,12),rep(FALSE,4)),
  direction=c(rep('Down',6),rep('Up',6),'Down','Down','Up','Up'),
  label=c({r_long_labels}), padj_rank=seq_len(16), stringsAsFactors=FALSE
)
p <- ggplot(labels,aes(log2FoldChange,y_plot)) + geom_point() +
  geom_vline(xintercept=c(-1,1),linetype='dashed',colour='grey60') +
  scale_colour_manual(values=c(Down='#2C7BB6',`n.s.`='#CCCCCC',Up='#C0392B')) +
  theme_bw()
p <- volcano_add_ranked_key(
  p, labels, xm=4, ytop=16, canvas_w=6, canvas_h=5,
  label_size=4, marker_size_mm=1.4
)
adaptive_width <- attr(p, 'volcano_canvas_width', exact=TRUE)
stopifnot(length(adaptive_width) == 1L, is.finite(adaptive_width), adaptive_width > 6)
svglite::svglite('{adaptive_r_path}',width=adaptive_width,height=5,bg='white')
print(p)
grDevices::dev.off()
"""
    adaptive_script_path = tmp_path / "adaptive-width-volcano-key.R"
    adaptive_script_path.write_text(adaptive_script, encoding="utf-8")
    adaptive = subprocess.run(
        [rscript, "--vanilla", str(adaptive_script_path)], capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert adaptive.returncode == 0, adaptive.stderr
    assert adaptive_svg_path.is_file(), adaptive.stdout + adaptive.stderr
    adaptive_rows = {
        label: f"{rank:02d}  {label}  ({effect:+.2f})"
        for rank, (label, effect) in enumerate(zip(long_labels, effects), start=1)
    }
    _assert_svg_volcano_key(
        adaptive_svg_path.read_text(encoding="utf-8"),
        left_rows={adaptive_rows[label] for label in long_labels[:6] + long_labels[12:14]},
        right_rows={adaptive_rows[label] for label in long_labels[6:12] + long_labels[14:]},
    )

    capacity_script = f"""
suppressPackageStartupMessages(library(ggplot2))
{helper}
labels <- data.frame(
  log2FoldChange=rep(c(-1,1),each=10), y_plot=rep(5,20),
  capped=rep(TRUE,20), direction=rep(c('Down','Up'),each=10),
  label=sprintf('dense_%02d',seq_len(20)), padj_rank=seq_len(20)
)
p <- ggplot(labels,aes(log2FoldChange,y_plot)) + geom_point() +
  scale_colour_manual(values=c(Down='#2C7BB6',Up='#C0392B'))
volcano_add_ranked_key(p,labels,xm=4,ytop=8,canvas_w=6,canvas_h=1,label_size=4)
"""
    capacity_script_path = tmp_path / "capacity-volcano-key.R"
    capacity_script_path.write_text(capacity_script, encoding="utf-8")
    capacity = subprocess.run(
        [rscript, "--vanilla", str(capacity_script_path)], capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert capacity.returncode != 0
    assert "require a taller figure canvas" in capacity.stderr

def test_sample_distance_labels_adapt_to_rendered_geometry() -> None:
    _assert_sample_distance_label_contract(_text("make_figures.R"))


def test_volcano_threshold_guide_stays_inside_the_data_panel() -> None:
    source = _text("make_figures.R")
    assert 'annotate("segment", x = -xm, xend = xm,' in source
    assert 'geom_hline(yintercept = -log10(alpha_thr)' not in source


def test_sample_distance_label_contract_rejects_the_fixed_45_degree_layout() -> None:
    core = _text("make_figures.R")
    broken = core.replace(
        "cellwidth = cell_width_pt, cellheight = cell_height_pt",
        "cellwidth = NULL, cellheight = NULL",
        1,
    ).replace(
        "w = dist_render$dim[1], h = dist_render$dim[2]",
        "w = dist_floor[1], h = dist_floor[2]",
        1,
    )
    assert broken != core
    with pytest.raises(AssertionError):
        _assert_sample_distance_label_contract(broken)


def test_correlation_heatmaps_use_measured_fixed_cell_geometry() -> None:
    _assert_correlation_geometry_contract(_text("sample_correlation.R"))


def test_correlation_geometry_gate_rejects_the_previous_fixed_canvas() -> None:
    corr = _text("sample_correlation.R")
    broken = corr.replace(
        "cellwidth = cell_width_pt, cellheight = cell_height_pt",
        "cellwidth = NULL, cellheight = NULL",
        1,
    ).replace(
        "width = corr_render$dim[1]",
        "width = fig_w",
        1,
    )
    assert broken != corr
    with pytest.raises(AssertionError):
        _assert_correlation_geometry_contract(broken)


def test_enrichment_placeholders_wrap_measured_text_and_remain_centered() -> None:
    _assert_enrichment_placeholder_wrap_contract(_text("make_enrichment_figures.R"))


def test_placeholder_wrap_gate_rejects_the_previous_single_line_annotation() -> None:
    enrich = _text("make_enrichment_figures.R")
    broken = enrich.replace("label = wrapped", "label = msg", 1).replace(
        'paste(collapse = "\\n")', 'paste(collapse = " ")', 1
    )
    assert broken != enrich
    with pytest.raises(AssertionError):
        _assert_enrichment_placeholder_wrap_contract(broken)


def test_sample_distance_svg_uses_one_continuous_gradient_legend() -> None:
    core = _text("make_figures.R")
    _assert_sample_distance_smooth_legend_contract(core)


def test_sample_distance_legend_contract_rejects_the_seamed_rect_stack() -> None:
    core = _text("make_figures.R")
    broken = core.replace(
        "ph$gtable <- smooth_continuous_legend(ph$gtable)",
        "ph$gtable <- ph$gtable",
        1,
    )
    assert broken != core
    with pytest.raises(AssertionError):
        _assert_sample_distance_smooth_legend_contract(broken)

    seamed_svg = "<svg>" + "".join(
        f"<rect x='0' y='{i * 0.59:.2f}' width='10' height='0.59' "
        "style='stroke: none; fill: #2F6DAC;' />"
        for i in range(255)
    ) + "</svg>"
    with pytest.raises(AssertionError):
        _assert_seam_free_continuous_svg(seamed_svg)


def test_sample_distance_legend_transform_renders_one_svg_gradient(tmp_path: Path) -> None:
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript is unavailable on this host")
    core = _text("make_figures.R")
    start = core.index("smooth_continuous_legend <- function")
    end = core.index("# ---- Grouping factor", start)
    helper = core[start:end]
    svg_path = tmp_path / "legend.svg"
    r_path = svg_path.as_posix().replace("'", "\\'")
    script = f"""
if (!requireNamespace('svglite', quietly = TRUE)) quit(status = 77)
{helper}
n <- 256L
fills <- grDevices::colorRampPalette(c('#F7FBFF', '#08519C'))(n)
stripes <- grid::rectGrob(
  x = grid::unit(0.25, 'npc'),
  y = grid::unit(seq(0, 1 - 1 / n, length.out = n), 'npc'),
  width = grid::unit(10, 'pt'), height = grid::unit(rep(1 / n, n), 'npc'),
  hjust = 0, vjust = 0, gp = grid::gpar(fill = fills, col = NA)
)
legend <- grid::gTree(children = grid::gList(stripes, grid::textGrob('distance')))
mock <- list(layout = data.frame(name = 'legend'), grobs = list(legend))
fixed <- smooth_continuous_legend(mock)
svglite::svglite('{r_path}', width = 2, height = 3, bg = 'white')
grid::grid.newpage(); grid::grid.draw(fixed$grobs[[1]]); grDevices::dev.off()
"""
    script_path = tmp_path / "sample-distance-gradient.R"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [rscript, "--vanilla", str(script_path)], capture_output=True, text=True,
        timeout=30, check=False,
    )
    if result.returncode == 77:
        pytest.skip("svglite is unavailable in the R environment")
    assert result.returncode == 0, result.stderr
    assert svg_path.is_file(), result.stdout + result.stderr
    _assert_seam_free_continuous_svg(svg_path.read_text(encoding="utf-8"))


def test_ppi_component_size_25_radius_lookup_rejects_the_empty_source_column() -> None:
    ppi = _text("build_string_network.R")
    ids = [f"node_{index:02d}" for index in range(25)]
    degree_bands = {node: ("4+" if index % 5 == 0 else "2-3") for index, node in enumerate(ids)}
    radii = {"1": 0.65, "2-3": 0.85, "4+": 1.10}
    assigned = [radii[degree_bands[node]] for node in ids]
    assert len(assigned) == 25 and all(value > 0 for value in assigned)
    broken = ppi.replace(
        "node_plot$degree_band[match(vertex_order, node_plot$id)]",
        "nodes_df$degree_band[match(vertex_order, nodes_df$id)]",
        1,
    )
    assert broken != ppi
    with pytest.raises(AssertionError):
        _assert_ppi_geometry_contract(broken)


def test_ppi_exported_geometry_gate_cannot_be_bypassed() -> None:
    ppi = _text("build_string_network.R")
    broken = ppi.replace('    validate_exported_ppi_svg(out[["svg"]])', "    TRUE", 1)
    assert broken != ppi
    with pytest.raises(AssertionError):
        _assert_ppi_geometry_contract(broken)


def test_ppi_dense_static_layout_falls_back_to_warning_after_complete_exports() -> None:
    source = _text("build_string_network.R")
    _assert_ppi_bounded_static_layout_contract(source)
    assert "Static figure WARNING:" in source
    assert "Network tables and interactive exports remain complete." in source
    assert "placeholder_fig(if (grepl(\"dense component\", fig_error, fixed = TRUE))" in source
    assert 'write_check(check_status, check_message)' in source
    assert 'write_provenance(check_status, if (fig_ok)' in source


def test_ppi_dense_static_layout_gate_rejects_the_old_unbounded_search() -> None:
    source = _text("build_string_network.R")
    broken = source.replace(
        "    if (nrow(component_edges) > outerplanar_edge_bound) {\n"
        "      stop(sprintf(\n"
        "        \"dense component %d has %d vertices and %d edges, exceeding the crossing-free circular bound of %d\",\n"
        "        component_rank, length(ids), nrow(component_edges), outerplanar_edge_bound\n"
        "      ))\n"
        "    }\n",
        "",
        1,
    ).replace(
        "PPI_CIRCLE_SCORE_EVALUATION_BUDGET <- 96L",
        "PPI_CIRCLE_SCORE_EVALUATION_BUDGET <- Inf",
        1,
    )
    assert broken != source
    with pytest.raises(AssertionError):
        _assert_ppi_bounded_static_layout_contract(broken)


def test_diagnostic_annotations_are_legible_and_self_describing() -> None:
    core = _text("make_figures.R")
    corr = _text("sample_correlation.R")
    _assert_diagnostic_annotation_contract(core, corr)


def test_diagnostic_annotation_contract_rejects_the_previous_unlabelled_state() -> None:
    core = _text("make_figures.R")
    corr = _text("sample_correlation.R")
    broken_core = core.replace('label = sprintf("raw p = %.3g", alpha_thr)', 'label = ""', 1)
    broken_corr = corr.replace(
        "display_numbers = if (show_num) number_labels else FALSE",
        "display_numbers = show_num, number_format = number_fmt",
        1,
    )
    assert broken_core != core and broken_corr != corr
    with pytest.raises(AssertionError):
        _assert_diagnostic_annotation_contract(broken_core, broken_corr)


def test_enrichment_label_contract_rejects_anchors_inside_the_graph_hull() -> None:
    enrich = _text("make_enrichment_figures.R")
    broken = enrich.replace(
        'lab$label_x <- ifelse(lab$side == "left", x_range[1] - gap, x_range[2] + gap)',
        'lab$label_x <- ifelse(lab$side == "left", x_range[1] + gap, x_range[2] - gap)',
        1,
    )
    assert broken != enrich
    with pytest.raises(AssertionError):
        _assert_external_enrichment_label_contract(broken)


def test_enrichment_label_contract_rejects_an_invisible_emap_outline() -> None:
    enrich = _text("make_enrichment_figures.R")
    broken = enrich.replace("shape = 21", "shape = 19", 1)
    assert broken != enrich
    with pytest.raises(AssertionError):
        _assert_external_enrichment_label_contract(broken)


def test_go_ora_figures_preserve_direction_specific_scope() -> None:
    _assert_directional_go_scope_contract(_text("make_enrichment_figures.R"))


def test_go_scope_contract_rejects_the_previous_combined_only_networks() -> None:
    enrich = _text("make_enrichment_figures.R")
    broken = enrich.replace("make_cnet(go_plot_obj", "make_cnet(ego_all", 1)
    assert broken != enrich
    with pytest.raises(AssertionError):
        _assert_directional_go_scope_contract(broken)


def test_go_and_kegg_gsea_titles_share_the_selected_result_row() -> None:
    _assert_gsea_title_contract(_text("make_enrichment_figures.R"))


@pytest.mark.parametrize(
    ("old", "broken"),
    [
        ("title = go_gsea_title", 'title = ""'),
        ("title = kegg_gsea_title", 'title = ""'),
        ('all(c("Description", "ID") %in% names(tab))', '"Description" %in% names(tab)'),
    ],
)
def test_gsea_title_contract_rejects_blank_or_partially_derived_titles(
    old: str, broken: str
) -> None:
    source = _text("make_enrichment_figures.R")
    damaged = source.replace(old, broken, 1)
    assert damaged != source
    with pytest.raises(AssertionError):
        _assert_gsea_title_contract(damaged)


def test_svg_title_identity_gate_reads_description_and_id_from_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "gsea.csv"
    svg_path = tmp_path / "gsea.svg"
    csv_path.write_text(
        '"ID","Description"\n"GO:0120192","tight junction assembly"\n',
        encoding="utf-8",
    )
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>'
        'tight junction assembly (GO:0120192)</text></svg>',
        encoding="utf-8",
    )
    assert _assert_svg_title_matches_csv(svg_path, csv_path) == (
        "tight junction assembly (GO:0120192)"
    )
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>GO:0120192</text></svg>',
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        _assert_svg_title_matches_csv(svg_path, csv_path)


def test_ppi_provenance_and_determinism_contract_is_declared() -> None:
    ppi = _text("build_string_network.R")
    rule = (ROOT / "workflow" / "rules" / "ppi.smk").read_text(encoding="utf-8")
    assert 'provenance="results/networks/string_ppi_provenance.json"' in rule
    for constant in ("COMMUNITY_SEED", "LAYOUT_SEED", "LABEL_SEED"):
        assert f"{constant} <- 42L" in ppi
    for key in (
        "configured_version",
        "realized_version",
        "query_date_utc",
        "score_threshold_combined",
        "minimum_combined_score",
        "mapped_seed_count",
        "community_detection",
        "edge_distance",
        "layout_fallback_reason",
    ):
        assert key in ppi
    assert 'write_provenance("WARNING", msg)' in ppi
    assert "write_provenance(check_status" in ppi
    assert "deterministic component-wise crossing-optimised circular shelf" in ppi
    assert "manual occupied STRING combined-score bands" in ppi
    assert "no node labels in the static topology panel" in ppi
