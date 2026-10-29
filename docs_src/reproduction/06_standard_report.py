"""Build the synthetic standard report shown in the documentation preview."""
from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "examples" / "standard"


def esc(value: object) -> str:
    return html.escape(str(value))


def tag(name: str, **attrs: object) -> str:
    return f"<{name} " + " ".join(f'{key}="{esc(value)}"' for key, value in attrs.items()) + "/>"


def label(x: float, y: float, value: object, size: int = 14, anchor: str = "start") -> str:
    return f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}">{esc(value)}</text>'


def write_svg(path: Path, width: int, height: int, title: str, body: str) -> None:
    source = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
        f'<title>{esc(title)}</title><rect width="100%" height="100%" fill="white"/>'
        f'<g font-family="Times New Roman, Liberation Serif, DejaVu Serif, serif" fill="#000">{body}</g></svg>'
    )
    path.write_text(source, encoding="utf-8")


def raster(path: Path, width: int, height: int, paint) -> None:
    from PIL import Image, ImageDraw, ImageFont

    scale = 2
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    font_file = os.environ.get("BULKSEQ_REPORT_FONT", "times.ttf")
    try:
        font = ImageFont.truetype(font_file, 14 * scale)
        small = ImageFont.truetype(font_file, 12 * scale)
    except OSError:
        font = ImageFont.load_default()
        small = font
    paint(draw, scale, font, small)
    image.save(path.with_suffix(".png"), "PNG", dpi=(300, 300))


def draw_text(draw, xy: tuple[float, float], value: object, font, *, anchor: str = "la", fill: str = "black") -> None:
    draw.text(xy, str(value), font=font, fill=fill, anchor=anchor)


def load() -> tuple[dict, dict]:
    return (
        json.loads((ROOT / "docs_src" / "model-examples.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "docs_src" / "learning-examples.json").read_text(encoding="utf-8")),
    )


def de_rows(model: dict) -> list[dict[str, object]]:
    count_rows = dict(zip(model["genes"], model["counts"], strict=True))
    retained = {value["id"] for value in model["models"]["deseq2"]}
    geometric = {
        gene: math.exp(sum(math.log(value) for value in counts) / len(counts))
        for gene, counts in count_rows.items() if gene in retained and all(value > 0 for value in counts)
    }
    factors = []
    for index in range(len(model["samples"])):
        factors.append(math.exp(statistics.median(math.log(count_rows[gene][index] / geometric[gene]) for gene in geometric)))
    return sorted([
        {
            "gene_id": value["id"], "symbol": value["id"], "log2FoldChange": value["log2fc"],
            "pvalue": value["pvalue"], "padj": value["padj"],
            "baseMean": sum(count / factor for count, factor in zip(count_rows[value["id"]], factors, strict=True)) / len(factors),
            "biotype": "synthetic_feature",
        }
        for value in model["models"]["deseq2"]
    ], key=lambda row: (float(row["padj"]), str(row["gene_id"])))


def write_table(path: Path, rows: list[dict[str, object]]) -> None:
    fields = ["gene_id", "symbol", "log2FoldChange", "pvalue", "padj", "baseMean", "biotype"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def axes(width: int, height: int, left: int = 78, right: int = 38, top: int = 28, bottom: int = 65) -> tuple[str, int, int, int, int]:
    return tag("line", x1=left, y1=height-bottom, x2=width-right, y2=height-bottom, stroke="#000") + tag("line", x1=left, y1=top, x2=left, y2=height-bottom, stroke="#000"), left, right, top, bottom


def volcano(rows: list[dict[str, object]], path: Path) -> None:
    width, height = 720, 470
    body, left, right, top, bottom = axes(width, height, top=62)
    ymax = max(-math.log10(float(row["padj"])) for row in rows) + .5
    body += tag("line", x1=left, y1=height-bottom-(height-top-bottom)*(-math.log10(.05)/ymax), x2=width-right, y2=height-bottom-(height-top-bottom)*(-math.log10(.05)/ymax), stroke="#777", **{"stroke-dasharray": "4 4"})
    for cutoff in (-1, 1):
        x = left + (cutoff + 4) / 8 * (width-left-right)
        body += tag("line", x1=x, y1=top, x2=x, y2=height-bottom, stroke="#777", **{"stroke-dasharray": "4 4"})
    for row in rows:
        effect, adjusted = float(row["log2FoldChange"]), float(row["padj"])
        x = left + (effect + 4) / 8 * (width-left-right)
        y = height-bottom-(-math.log10(adjusted)/ymax)*(height-top-bottom)
        selected = adjusted < .05 and abs(effect) >= 1
        color = "#2d4fa3" if selected and effect < 0 else "#8b2c22" if selected else "#777"
        body += tag("circle", cx=x, cy=y, r=2.8 if selected else 1.8, fill=color, opacity=1 if selected else .45)
    for value in range(-4, 5, 2):
        x = left + (value + 4) / 8 * (width-left-right)
        body += tag("line", x1=x, y1=height-bottom, x2=x, y2=height-bottom+5, stroke="#000") + label(x, height-bottom+21, value, 12, "middle")
    for value in range(0, math.floor(ymax) + 1, 3):
        y = height-bottom-(value/ymax)*(height-top-bottom)
        body += tag("line", x1=left-5, y1=y, x2=left, y2=y, stroke="#000") + label(left-10, y+4, value, 12, "end")
    body += label(left, 28, "−log10 adjusted p-value", 14)
    body += label(width-right, 28, "Red: higher selected · blue: lower selected · gray: other", 12, "end")
    body += label((left+width-right)/2, height-18, "log2 fold change (B vs A)", 14, "middle")
    write_svg(path, width, height, "Volcano plot of synthetic DESeq2 results", body)
    def paint(draw, scale, font, small):
        s = scale
        draw.line((left*s, (height-bottom)*s, (width-right)*s, (height-bottom)*s), fill="black", width=2*s)
        draw.line((left*s, top*s, left*s, (height-bottom)*s), fill="black", width=2*s)
        threshold_y = height-bottom-(height-top-bottom)*(-math.log10(.05)/ymax)
        draw.line((left*s, threshold_y*s, (width-right)*s, threshold_y*s), fill="#777777", width=s)
        for cutoff in (-1, 1):
            x = left+(cutoff+4)/8*(width-left-right)
            draw.line((x*s, top*s, x*s, (height-bottom)*s), fill="#777777", width=s)
        for row in rows:
            effect, adjusted = float(row["log2FoldChange"]), float(row["padj"])
            x = left+(effect+4)/8*(width-left-right)
            y = height-bottom-(-math.log10(adjusted)/ymax)*(height-top-bottom)
            selected = adjusted < .05 and abs(effect) >= 1
            color = "#2d4fa3" if selected and effect < 0 else "#8b2c22" if selected else "#999999"
            radius = 5 if selected else 3
            draw.ellipse(((x-radius)*s, (y-radius)*s, (x+radius)*s, (y+radius)*s), fill=color)
        for value in range(-4, 5, 2):
            x = left+(value+4)/8*(width-left-right)
            draw.line((x*s, (height-bottom)*s, x*s, (height-bottom+5)*s), fill="black", width=s)
            draw_text(draw, (x*s, (height-bottom+10)*s), value, small, anchor="ma")
        for value in range(0, math.floor(ymax)+1, 3):
            y = height-bottom-(value/ymax)*(height-top-bottom)
            draw_text(draw, ((left-10)*s, y*s), value, small, anchor="ra")
        draw_text(draw, ((left+width-right)/2*s, (height-18)*s), "log2 fold change (B vs A)", font, anchor="ma")
        draw_text(draw, (left*s, 28*s), "−log10 adjusted p-value", font)
        draw_text(draw, ((width-right)*s, 28*s), "Red: higher selected · blue: lower selected · gray: other", small, anchor="ra")
    raster(path, width, height, paint)


def pca(learning: dict, path: Path) -> None:
    data = learning["pca"]["100"]
    width, height = 720, 460
    body, left, right, top, bottom = axes(width, height, top=62)
    xs, ys = [value[0] for value in data["scores"]], [value[1] for value in data["scores"]]
    xmin, xmax, ymin, ymax = min(xs)-.8, max(xs)+.8, min(ys)-.8, max(ys)+.8
    for index, (first, second) in enumerate(data["scores"]):
        x = left+(first-xmin)/(xmax-xmin)*(width-left-right)
        y = height-bottom-(second-ymin)/(ymax-ymin)*(height-top-bottom)
        body += tag("circle", cx=x, cy=y, r=6, fill="#8b2c22" if index < 4 else "#2d4fa3")
        body += label(x+9, y+4, f'{"A" if index < 4 else "B"}{index % 4+1}', 13)
    for value in range(math.ceil(xmin), math.floor(xmax) + 1, 2):
        x = left+(value-xmin)/(xmax-xmin)*(width-left-right)
        body += tag("line", x1=x, y1=height-bottom, x2=x, y2=height-bottom+5, stroke="#000") + label(x, height-bottom+21, value, 12, "middle")
    for value in range(math.ceil(ymin), math.floor(ymax) + 1, 2):
        y = height-bottom-(value-ymin)/(ymax-ymin)*(height-top-bottom)
        body += tag("line", x1=left-5, y1=y, x2=left, y2=y, stroke="#000") + label(left-10, y+4, value, 12, "end")
    body += label(left, 28, f'PC2 ({data["percent"][1]:.1f}% variance)', 14)
    body += label((left+width-right)/2, height-20, f'PC1 ({data["percent"][0]:.1f}% variance)', 14, "middle")
    write_svg(path, width, height, "PCA of synthetic rlog expression", body)
    def paint(draw, scale, font, small):
        s = scale
        draw.line((left*s, (height-bottom)*s, (width-right)*s, (height-bottom)*s), fill="black", width=2*s)
        draw.line((left*s, top*s, left*s, (height-bottom)*s), fill="black", width=2*s)
        for index, (first, second) in enumerate(data["scores"]):
            x = left+(first-xmin)/(xmax-xmin)*(width-left-right)
            y = height-bottom-(second-ymin)/(ymax-ymin)*(height-top-bottom)
            color = "#8b2c22" if index < 4 else "#2d4fa3"
            draw.ellipse(((x-6)*s, (y-6)*s, (x+6)*s, (y+6)*s), fill=color)
            draw_text(draw, ((x+9)*s, (y+4)*s), f'{"A" if index < 4 else "B"}{index%4+1}', small)
        for value in range(math.ceil(xmin), math.floor(xmax)+1, 2):
            x = left+(value-xmin)/(xmax-xmin)*(width-left-right)
            draw_text(draw, (x*s, (height-bottom+10)*s), value, small, anchor="ma")
        for value in range(math.ceil(ymin), math.floor(ymax)+1, 2):
            y = height-bottom-(value-ymin)/(ymax-ymin)*(height-top-bottom)
            draw_text(draw, ((left-10)*s, y*s), value, small, anchor="ra")
        draw_text(draw, ((left+width-right)/2*s, (height-20)*s), f'PC1 ({data["percent"][0]:.1f}% variance)', font, anchor="ma")
        draw_text(draw, (left*s, 28*s), f'PC2 ({data["percent"][1]:.1f}% variance)', font)
    raster(path, width, height, paint)


def ma(rows: list[dict[str, object]], path: Path) -> None:
    width, height = 720, 470
    body, left, right, top, bottom = axes(width, height, top=85)
    xmax = math.log10(max(float(row["baseMean"]) for row in rows))+.2
    body += tag("line", x1=left, y1=(top+height-bottom)/2, x2=width-right, y2=(top+height-bottom)/2, stroke="#777", **{"stroke-dasharray": "4 4"})
    for row in rows:
        effect, adjusted = float(row["log2FoldChange"]), float(row["padj"])
        x = left+math.log10(max(float(row["baseMean"]), 1))/xmax*(width-left-right)
        y = height-bottom-(effect+4)/8*(height-top-bottom)
        color = "#2d4fa3" if adjusted < .05 and effect < 0 else "#8b2c22" if adjusted < .05 else "#777"
        body += tag("circle", cx=x, cy=y, r=2.8 if adjusted < .05 else 1.8, fill=color, opacity=1 if adjusted < .05 else .45)
    for value in range(0, math.floor(xmax) + 1):
        x = left+value/xmax*(width-left-right)
        body += tag("line", x1=x, y1=height-bottom, x2=x, y2=height-bottom+5, stroke="#000") + label(x, height-bottom+21, value, 12, "middle")
    for value in range(-4, 5, 2):
        y = height-bottom-(value+4)/8*(height-top-bottom)
        body += tag("line", x1=left-5, y1=y, x2=left, y2=y, stroke="#000") + label(left-10, y+4, value, 12, "end")
    body += label(left, 28, "log2 fold change (B vs A)", 14)
    body += label(width-right, 54, "Red: higher significant · blue: lower significant · gray: other", 12, "end")
    body += label((left+width-right)/2, height-18, "log10 mean normalized count", 14, "middle")
    write_svg(path, width, height, "MA plot of synthetic DESeq2 results", body)
    def paint(draw, scale, font, small):
        s = scale
        draw.line((left*s, (height-bottom)*s, (width-right)*s, (height-bottom)*s), fill="black", width=2*s)
        draw.line((left*s, top*s, left*s, (height-bottom)*s), fill="black", width=2*s)
        zero = (top+height-bottom)/2
        draw.line((left*s, zero*s, (width-right)*s, zero*s), fill="#777777", width=s)
        for row in rows:
            effect, adjusted = float(row["log2FoldChange"]), float(row["padj"])
            x = left+math.log10(max(float(row["baseMean"]), 1))/xmax*(width-left-right)
            y = height-bottom-(effect+4)/8*(height-top-bottom)
            color = "#2d4fa3" if adjusted < .05 and effect < 0 else "#8b2c22" if adjusted < .05 else "#999999"
            radius = 5 if adjusted < .05 else 3
            draw.ellipse(((x-radius)*s, (y-radius)*s, (x+radius)*s, (y+radius)*s), fill=color)
        for value in range(0, math.floor(xmax)+1):
            x = left+value/xmax*(width-left-right)
            draw_text(draw, (x*s, (height-bottom+10)*s), value, small, anchor="ma")
        for value in range(-4, 5, 2):
            y = height-bottom-(value+4)/8*(height-top-bottom)
            draw_text(draw, ((left-10)*s, y*s), value, small, anchor="ra")
        draw_text(draw, ((left+width-right)/2*s, (height-18)*s), "log10 mean normalized count", font, anchor="ma")
        draw_text(draw, (left*s, 28*s), "log2 fold change (B vs A)", font)
        draw_text(draw, ((width-right)*s, 54*s), "Red: higher significant · blue: lower significant · gray: other", small, anchor="ra")
    raster(path, width, height, paint)


def heatmap(learning: dict, path: Path) -> None:
    data, width, height, left, top = learning["heatmap"], 720, 585, 95, 62
    cell_w, cell_h = 70, 34
    body = ""
    for col, sample in enumerate(data["samples"]):
        body += label(left+col*cell_w+cell_w/2, 40, sample, 13, "middle")
    for row, gene in enumerate(data["genes"][:12]):
        body += label(left-12, top+row*cell_h+22, gene, 12, "end")
        for col, z in enumerate(data["z"][row]):
            t = min(1, abs(z)/2.5); endpoint = (183,72,38) if z < 0 else (55,87,175)
            rgb = tuple(round(248+(value-248)*t) for value in endpoint)
            body += tag("rect", x=left+col*cell_w, y=top+row*cell_h, width=cell_w-3, height=cell_h-3, fill=f"rgb{rgb}")
    for index in range(101):
        z = -2.5 + 5 * index / 100
        endpoint = (183,72,38) if z < 0 else (55,87,175)
        rgb = tuple(round(248+(value-248)*min(1, abs(z)/2.5)) for value in endpoint)
        body += tag("rect", x=left+index*2, y=top+12*cell_h+20, width=2.1, height=12, fill=f"rgb{rgb}")
    body += label(left, top+12*cell_h+52, "−2.5") + label(left+100, top+12*cell_h+52, "0", 12, "middle") + label(left+200, top+12*cell_h+52, "+2.5", 12, "end")
    body += label(left, height-18, "Row z-score: each gene centered and scaled across samples", 13)
    write_svg(path, width, height, "Heatmap of synthetic transformed expression", body)
    def paint(draw, scale, font, small):
        s = scale
        for col, sample in enumerate(data["samples"]):
            draw_text(draw, ((left+col*cell_w+cell_w/2)*s, 40*s), sample, small, anchor="ma")
        for row, gene in enumerate(data["genes"][:12]):
            draw_text(draw, ((left-12)*s, (top+row*cell_h+17)*s), gene, small, anchor="ra")
            for col, z in enumerate(data["z"][row]):
                t = min(1, abs(z)/2.5); endpoint = (183,72,38) if z < 0 else (55,87,175)
                rgb = tuple(round(248+(value-248)*t) for value in endpoint)
                draw.rectangle(((left+col*cell_w)*s, (top+row*cell_h)*s, (left+col*cell_w+cell_w-3)*s, (top+row*cell_h+cell_h-3)*s), fill=rgb)
        for index in range(101):
            z = -2.5+5*index/100
            endpoint = (183,72,38) if z < 0 else (55,87,175)
            rgb = tuple(round(248+(value-248)*min(1, abs(z)/2.5)) for value in endpoint)
            draw.rectangle(((left+index*2)*s, (top+12*cell_h+20)*s, (left+index*2+2.1)*s, (top+12*cell_h+32)*s), fill=rgb)
        draw_text(draw, (left*s, (top+12*cell_h+52)*s), "−2.5", small)
        draw_text(draw, ((left+100)*s, (top+12*cell_h+52)*s), "0", small, anchor="ma")
        draw_text(draw, ((left+200)*s, (top+12*cell_h+52)*s), "+2.5", small, anchor="ra")
        draw_text(draw, (left*s, (height-18)*s), "Row z-score: each gene centered and scaled across samples", font)
    raster(path, width, height, paint)


def build(output: Path) -> None:
    model, learning = load()
    output.mkdir(parents=True, exist_ok=True)
    figures = output / "results" / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    rows = de_rows(model)
    selected = [row for row in rows if float(row["padj"]) < .05 and abs(float(row["log2FoldChange"])) >= 1]
    write_table(output / "results" / "deseq2" / "deseq2_results.csv", rows)
    write_table(output / "results" / "deseq2" / "upregulated_genes.csv", sorted((row for row in selected if float(row["log2FoldChange"]) > 0), key=lambda row: float(row["log2FoldChange"]), reverse=True))
    write_table(output / "results" / "deseq2" / "downregulated_genes.csv", sorted((row for row in selected if float(row["log2FoldChange"]) < 0), key=lambda row: float(row["log2FoldChange"])))
    volcano(rows, figures / "volcano.svg")
    pca(learning, figures / "pca.svg")
    ma(rows, figures / "ma_plot.svg")
    heatmap(learning, figures / "top_deg_heatmap.svg")
    (output / "config").mkdir(exist_ok=True)
    (output / "config" / "samples.tsv").write_text("sample_id\tcondition\n" + "\n".join(f"{sample}\t{'A' if sample.startswith('A') else 'B'}" for sample in model["samples"]) + "\n", encoding="utf-8")
    reports = output / "results" / "reports"
    reports.mkdir(exist_ok=True)
    provenance = model["provenance"]
    run = {
        "app_version": re.search(r"documentedVersion = '([^']+)'", (ROOT / "docs_src" / "site-config.mjs").read_text(encoding="utf-8")).group(1),
        "run_date": "Synthetic standard analysis report; deterministic DESeq2 fixture",
        "input": {"type": "count_matrix", "samples": "config/samples.tsv"},
        "reference": {"mode": "synthetic teaching reference", "source": "docs_src/model-examples.json"},
        "workflow": {"de_engine": "DESeq2"},
        "deseq2": {"alpha": .05, "lfc_threshold": 1, "design_formula": "~ condition", "contrasts": [{"numerator": "B", "denominator": "A"}]},
        "software_versions": {"R": provenance["R"]}, "r_packages": provenance["packages"],
    }
    (reports / "run_summary.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    (output / "checks").mkdir(exist_ok=True)
    (output / "checks" / "sanity_checks.txt").write_text("Overall: REVIEW_REQUIRED\n01_synthetic_example: REVIEW_REQUIRED\n- This report is a synthetic teaching example, not a completed biological study.\n", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "workflow" / "scripts" / "make_html_report.py"), "--project", str(output)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    build(parser.parse_args().output)
