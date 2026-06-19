from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--design-check", required=True)
    parser.add_argument("--deseq-check", required=True)
    args = parser.parse_args()
    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("gene_id\tlog2FoldChange\tpvalue\tpadj\nplaceholder_gene_1\t0\t1\t1\n", encoding="utf-8")
    write_check(Path(args.design_check), "08_metadata_design_qc", "REVIEW_REQUIRED", "Design validation is scaffolded; R DESeq2 will perform final model checks.")
    write_check(Path(args.deseq_check), "09_deseq2_qc", "REVIEW_REQUIRED", "DESeq2 results are placeholder output until R packages are installed.")
    return 0


def write_check(path: Path, name: str, status: str, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"check": name, "status": status, "messages": [{"status": status, "message": message}]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
