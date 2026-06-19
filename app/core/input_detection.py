from __future__ import annotations

import re
from pathlib import Path


PAIR_PATTERNS = [
    re.compile(r"(?P<prefix>.+?)(?:_R)(?P<read>[12])(?P<suffix>(?:[_\.].*)?\.(?:fastq|fq)(?:\.gz)?)$", re.IGNORECASE),
    re.compile(r"(?P<prefix>.+?)(?:_)(?P<read>[12])(?P<suffix>\.(?:fastq|fq)(?:\.gz)?)$", re.IGNORECASE),
    re.compile(r"(?P<prefix>.+?)(?:\.)(?P<read>[12])(?P<suffix>\.(?:fastq|fq)(?:\.gz)?)$", re.IGNORECASE),
]


def is_fastq(path: str | Path) -> bool:
    name = str(path).lower()
    return name.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))


def detect_fastq_inputs(paths: list[str | Path]) -> list[dict[str, str]]:
    files = [Path(p) for p in paths if is_fastq(p)]
    grouped: dict[tuple[str, str], dict[str, Path]] = {}
    singles: list[Path] = []

    for file in files:
        matched = False
        for pattern in PAIR_PATTERNS:
            match = pattern.match(file.name)
            if match:
                key = (match.group("prefix"), match.group("suffix"))
                grouped.setdefault(key, {})[match.group("read")] = file
                matched = True
                break
        if not matched:
            singles.append(file)

    rows: list[dict[str, str]] = []
    replicate_by_sample: dict[str, int] = {}
    for (prefix, _suffix), reads in sorted(grouped.items()):
        sample_id = sanitize_sample_id(prefix)
        replicate_by_sample[sample_id] = replicate_by_sample.get(sample_id, 0) + 1
        if "1" in reads and "2" in reads:
            rows.append(_row(sample_id, "paired", reads["1"], reads["2"], prefix, replicate_by_sample[sample_id]))
        else:
            only = reads.get("1") or reads.get("2")
            if only:
                rows.append(_row(sample_id, "single", only, None, prefix, replicate_by_sample[sample_id]))

    for file in singles:
        sample_id = sanitize_sample_id(file.name.split(".fastq")[0].split(".fq")[0])
        replicate_by_sample[sample_id] = replicate_by_sample.get(sample_id, 0) + 1
        rows.append(_row(sample_id, "single", file, None, sample_id, replicate_by_sample[sample_id]))
    return rows


def sanitize_sample_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "sample"


def _row(sample_id: str, layout: str, r1: Path, r2: Path | None, pair_id: str, replicate: int) -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "original_accession": "",
        "original_filename": r1.name,
        "layout": layout,
        "fastq_1": str(r1),
        "fastq_2": str(r2) if r2 else "",
        "detected_pair_id": pair_id,
        "condition": "unknown",
        "replicate": str(replicate),
        "batch": "unknown",
    }
