from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--counts", required=True)
    parser.add_argument("--check", required=True)
    args = parser.parse_args()
    samples = pd.read_csv(args.samples, sep="\t", dtype=str).fillna("")
    rows = [{"gene_id": "placeholder_gene_1", **{sid: 100 for sid in samples["sample_id"].tolist()}}]
    out = Path(args.counts)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, sep="\t", index=False)
    check = {"check": "07_quantification_qc", "status": "REVIEW_REQUIRED", "messages": [{"status": "REVIEW_REQUIRED", "message": "Count matrix is placeholder output until featureCounts is enabled."}]}
    Path(args.check).write_text(json.dumps(check, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
