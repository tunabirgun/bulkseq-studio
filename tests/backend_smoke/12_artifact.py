from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageStat


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"])
out = root / "artifact"
projects = root / "projects"
limit_file = 12 * 1024 * 1024
limit_total = 120 * 1024 * 1024
files: list[dict[str, object]] = []
total = 0


def portable(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError("Artifact path is not relative and portable")


def same_pixels(before: Image.Image, after: Image.Image) -> None:
    if before.size != after.size or any(
            high > 0 for _, high in ImageChops.difference(
                before.convert("RGBA"), after.convert("RGBA")).getextrema()):
        raise ValueError("PNG pixel content changed during metadata cleanup")


def same_dpi(before: Image.Image, after: Image.Image) -> None:
    source = before.info.get("dpi")
    saved = after.info.get("dpi")
    if source is not None and (saved is None or any(
            abs(a - b) > 0.03 for a, b in zip(source, saved))):
        raise ValueError("PNG physical-size DPI changed during metadata cleanup")


identifier_labels = ("Workflow execution digest", "Bundled workflow digest",
                     "Recorded workflow digest", "Environment lock md5",
                     "Workflow commit", "Project-copy SHA-256")
identifier_rows = re.compile(
    r"<tr><td>(?:" + "|".join(identifier_labels) +
    r")</td><td class=['\"]mono['\"]>.*?</td></tr>")


def assert_public_html(content: str) -> None:
    visible = re.sub(r"data:image/[^\"']+", "", content)
    if (any(label in visible for label in identifier_labels) or
            re.search(r"(?i)\b[a-f0-9]{32,64}\b|/home/runner|[A-Z]:\\|file://", visible)):
        raise ValueError("Optional identifier or machine path remains in retained HTML")


def public_html(content: str) -> str:
    content = identifier_rows.sub("", content)
    content = re.sub(r"(Installed environment spec</td><td class=['\"]mono['\"]>[^<]*?), sha256: [^)<]+",
                     r"\1", content)
    assert_public_html(content)
    return content


def linked_source(html: Path, link: str) -> Path | None:
    if link.startswith(("#", "http://", "https://", "mailto:", "data:")):
        return None
    parsed = urlsplit(link)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Retained HTML link has an unapproved scheme")
    if not parsed.path:
        return None
    linked = (html.parent / unquote(parsed.path)).resolve()
    project = out / html.relative_to(out).parts[0]
    if not linked.is_relative_to(project.resolve()):
        raise ValueError("Retained HTML link escapes its synthetic project")
    source = projects / linked.relative_to(out.resolve())
    if not source.is_file():
        raise ValueError("Retained HTML link lacks its source target")
    if source.suffix not in {".csv", ".html", ".json", ".png", ".svg"}:
        raise ValueError("Retained HTML link uses an unapproved output type")
    return source


def completed_receipt(name: str) -> str:
    path = root / "receipts" / f"{name}.json"
    if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != {
            "phase": name, "status": "PASS"}:
        raise ValueError("Missing successful phase receipt")
    return "PASS"


if sys.argv[1:] == ["--negative-path"]:
    portable("../outside.txt")
elif sys.argv[1:] == ["--negative-pixel"]:
    same_pixels(Image.new("RGBA", (1, 1), (0, 0, 0, 255)),
                Image.new("RGBA", (1, 1), (255, 0, 0, 255)))
elif sys.argv[1:] == ["--negative-identifier"]:
    assert_public_html("<tr><td>Workflow commit</td><td class='mono'>" + "a" * 40 + "</td></tr>")
elif sys.argv[1:] == ["--negative-link"]:
    linked_source(out / "main/results/reports/results_report.html", "../../../../outside.csv")
elif sys.argv[1:] == ["--negative-missing-link"]:
    linked_source(out / "main/results/reports/results_report.html", "missing.csv")
elif sys.argv[1:] == ["--negative-receipt"]:
    completed_receipt("missing_phase")
elif sys.argv[1:] == ["--test-derivative"]:
    sample = ("<tr><td>App version</td><td class='mono'>0.34.0</td></tr>"
              "<tr><td>Workflow commit</td><td class='mono'>" + "a" * 40 + "</td></tr>"
              "<tr><td>Installed environment spec</td><td class='mono'>"
              "bulkseq.lock.yaml (source: lock, sha256: " + "b" * 64 + ")</td></tr>")
    cleaned = public_html(sample)
    if ("App version" not in cleaned or "0.34.0" not in cleaned or
            "source: lock" not in cleaned or "Workflow commit" in cleaned):
        raise SystemExit("Report derivative changed required provenance or retained a raw identifier")
    print("Report derivative preserved version/spec source and removed optional raw identifiers")
    sys.exit(0)
elif sys.argv[1:] == ["--test-dpi-roundtrip"]:
    first = BytesIO()
    Image.new("RGB", (8, 8), "black").save(first, format="PNG", dpi=(300, 300))
    first.seek(0)
    with Image.open(first) as original:
        second = BytesIO()
        Image.frombytes(original.mode, original.size, original.tobytes()).save(
            second, format="PNG", dpi=original.info["dpi"])
        second.seek(0)
        with Image.open(second) as cleaned:
            same_pixels(original, cleaned)
            same_dpi(original, cleaned)
    print("PNG pixels and physical-size DPI survived a fresh-object round trip")
    sys.exit(0)
elif sys.argv[1:]:
    raise SystemExit("Unknown artifact checker argument")
if sys.argv[1:]:
    raise SystemExit("Artifact negative control unexpectedly returned")
if out.exists():
    raise SystemExit("Smoke artifact already exists")
out.mkdir()


def retain(source: Path) -> Path:
    global total
    relative = source.relative_to(projects)
    portable(relative.as_posix())
    if source.suffix in {".csv", ".json"} and re.search(
            rb"(?i)(/home/runner|[A-Z]:\\|file://|\b[a-f0-9]{32,64}\b)",
            source.read_bytes()):
        raise SystemExit(f"Machine path in retained text: {relative}")
    size = source.stat().st_size
    if not 0 < size <= limit_file or total + size > limit_total:
        raise SystemExit(f"Smoke artifact size exceeds cap: {relative}")
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if source.suffix == ".html":
        target.write_text(public_html(target.read_text(encoding="utf-8")), encoding="utf-8")
    retained_size = target.stat().st_size
    total += retained_size
    files.append({"path": relative.as_posix(), "bytes": retained_size})
    return target


font_families: set[str] = set()
figures: dict[str, int] = {}
for name in ("main", "meta", "custom_new", "ppi"):
    folder = projects / name / "results/figures"
    vectors = sorted(folder.glob("*.svg"))
    if name == "meta":
        vectors += sorted((projects / name / "results/meta/per_study").glob("*/figures/*.svg"))
    if not vectors:
        raise SystemExit(f"No final SVG figures for {name}")
    figures[name] = len(vectors)
    for vector in vectors:
        original = vector.read_text(encoding="utf-8")
        if re.search(r"(?i)(<metadata\b|<!--|creator|generator|/home/runner|[A-Z]:\\)", original):
            raise SystemExit(f"Optional SVG metadata or machine path in {name}/{vector.name}")
        ET.fromstring(original)
        font_families.update(re.findall(r"font-family\s*[:=]\s*['\"]?([^;\"'>]+)", original))
        png = vector.with_suffix(".png")
        with Image.open(png) as source_image:
            source_image.load()
            if source_image.width < 400 or source_image.height < 300:
                raise SystemExit(f"Figure dimensions too small: {name}/{png.name}")
            saved = retain(png)
            clean = Image.frombytes(source_image.mode, source_image.size, source_image.tobytes())
            keep = {"icc_profile": source_image.info["icc_profile"]} if "icc_profile" in source_image.info else {}
            if "dpi" in source_image.info:
                keep["dpi"] = source_image.info["dpi"]
            clean.save(saved, format="PNG", **keep)
            cleaned_size = saved.stat().st_size
            total += cleaned_size - int(files[-1]["bytes"])
            files[-1]["bytes"] = cleaned_size
            if cleaned_size > limit_file or total > limit_total:
                raise SystemExit(f"Clean PNG exceeds artifact cap: {name}/{png.name}")
            with Image.open(saved) as clean_image:
                same_pixels(source_image, clean_image)
                same_dpi(source_image, clean_image)
                if set(clean_image.info) - set(keep):
                    raise SystemExit(f"Unexpected PNG metadata survived: {name}/{png.name}")
                if "icc_profile" in keep and clean_image.info.get("icc_profile") != keep["icc_profile"]:
                    raise SystemExit(f"Required PNG color profile changed: {name}/{png.name}")
                if max(ImageStat.Stat(clean_image.convert("RGB")).stddev) < 1:
                    raise SystemExit(f"Blank PNG figure: {name}/{png.name}")
        saved_svg = retain(vector)
        preview = (out / name / "vector-previews" /
                   vector.relative_to(projects / name / "results").with_suffix(".png"))
        preview.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["rsvg-convert", "--dpi-x=150", "--dpi-y=150",
                        "--background-color=white", f"--output={preview}", str(saved_svg)],
                       check=True, timeout=60, stdout=subprocess.DEVNULL)
        with Image.open(preview) as image:
            image.load()
            if image.width < 400 or image.height < 300 or max(
                    ImageStat.Stat(image.convert("RGB")).stddev) < 1:
                raise SystemExit(f"SVG preview is blank or undersized: {name}/{vector.name}")
            Image.frombytes(image.mode, image.size, image.tobytes()).save(preview, format="PNG")
        preview_size = preview.stat().st_size
        total += preview_size
        if preview_size > limit_file or total > limit_total:
            raise SystemExit("SVG preview exceeds artifact size cap")
        files.append({"path": preview.relative_to(out).as_posix(), "bytes": preview_size})

tables = {
    "main": ("results/deseq2/deseq2_results.csv",),
    "meta": ("results/meta/meta_analysis_results.csv", "results/meta/meta_eligibility.json"),
    "custom_new": ("results/enrichment/custom_ora.csv", "results/gsva/gsva_scores.csv"),
    "ppi": ("results/networks/string_ppi_nodes.csv", "results/networks/string_ppi_edges.csv",
            "results/networks/string_ppi_provenance.json"),
}
reports = {
    "main": "results/reports/results_report.html",
    "meta": "results/reports/meta_analysis_report.html",
    "custom_new": "results/reports/results_report.html",
}
per_study = projects / "meta/results/meta/per_study"
for study in sorted(per_study.iterdir()):
    if not study.is_dir():
        continue
    retain(study / "index.html")
    for table in sorted((study / "tables").glob("*.csv")):
        retain(table)
retain(per_study / "manifest.json")
for name, paths in tables.items():
    for path in paths:
        retain(projects / name / path)
for name, path in reports.items():
    retain(projects / name / path)
pending = list(out.rglob("*.html"))
checked = set()
while pending:
    html = pending.pop()
    if html in checked:
        continue
    checked.add(html)
    content = html.read_text(encoding="utf-8")
    for link in re.findall(r'''(?:href|src)=["']([^"']+)["']''', content):
        source = linked_source(html, link)
        if source is None:
            continue
        target = out / source.relative_to(projects)
        if not target.is_file():
            retain(source)
        if source.suffix == ".html":
            pending.append(target)

expected_profiles = ("prepare", "salmon", "ribodetector", "main", "meta",
                     "custom_old", "custom_new", "gsva", "custom_report", "string", "ppi")
profiles = {}
phase_outcomes = {}
for name in expected_profiles:
    profile = json.loads((root / "profiles" / f"{name}.json").read_text(encoding="utf-8"))
    if (profile["planned_parallel_jobs"] != 1 or profile["available_memory_gib"] < 3 or
            profile["usable_cpu_capacity"] < 1):
        raise SystemExit(f"Missing resource gate for {name}")
    profiles[name] = profile
    phase_outcomes[name] = completed_receipt(name)
resolved_fonts = {}
for family in sorted(font_families):
    resolved_fonts[family] = subprocess.run(
        ["fc-match", "-f", "%{family}", family], check=True, capture_output=True,
        text=True, timeout=10).stdout.strip()
manifest = {
    "scope": "Manual synthetic backend function smoke; biological validation and visual acceptance are separate",
    "phase_outcomes": phase_outcomes,
    "figure_pairs": figures,
    "reported_svg_font_families": sorted(font_families) or ["not declared in SVG"],
    "native_linux_fontconfig_matches": resolved_fonts,
    "resources": profiles,
    "reports": {name: f"{name}/{path}" for name, path in reports.items()},
    "report_derivative": "Only optional digest, checksum and commit rows were removed from retained HTML; original project reports remain unchanged",
    "files": files,
    "visual_review": "Pending human inspection of every retained PNG and SVG preview",
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
for item in files:
    portable(str(item["path"]))
    if not (out / str(item["path"])).is_file():
        raise SystemExit("Manifest path is not a retained relative file")
print(f"Retained {len(files)} bounded synthetic/public files and {sum(figures.values())} figure pairs; "
      "visual review remains pending")
