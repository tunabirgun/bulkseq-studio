from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description="Download FASTQ files from ENA URLs recorded in samples.tsv.")
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out-root", default=".")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.out_root)
    samples = pd.read_csv(args.samples, sep="\t", dtype=str).fillna("")
    downloads = planned_downloads(samples)
    if not downloads:
        raise SystemExit("No fastq_1_url/fastq_2_url columns found in samples table.")

    for url, target in downloads:
        target_path = root / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if args.dry_run:
            print(f"DRY-RUN {url} -> {target_path}")
            continue
        if target_path.exists() and target_path.stat().st_size > 0:
            print(f"SKIP existing {target_path}")
            continue
        print(f"DOWNLOAD {url} -> {target_path}")
        with urllib.request.urlopen(_with_scheme(url), timeout=60) as response:
            with target_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    return 0


def planned_downloads(samples: pd.DataFrame) -> list[tuple[str, Path]]:
    downloads: list[tuple[str, Path]] = []
    pairs = [("fastq_1_url", "fastq_1"), ("fastq_2_url", "fastq_2")]
    for _, row in samples.iterrows():
        for url_col, path_col in pairs:
            url = str(row.get(url_col, "")).strip()
            target = str(row.get(path_col, "")).strip()
            if url and target:
                downloads.append((url, Path(target)))
    return downloads


def _with_scheme(url: str) -> str:
    if url.startswith(("http://", "https://", "ftp://")):
        return url
    return f"https://{url}"


if __name__ == "__main__":
    raise SystemExit(main())
