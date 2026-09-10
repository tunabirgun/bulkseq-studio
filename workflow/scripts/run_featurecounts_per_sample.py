#!/usr/bin/env python3
# Run featureCounts once per BAM, each with its own -s (from strandedness_per_sample.tsv),
# then merge the single-sample outputs into one featureCounts-shaped counts.txt + .summary --
# identical schema to a single multi-BAM featureCounts invocation, so DESeq2 and everything
# downstream are unchanged. A per-BAM run is required because featureCounts takes one -s for
# the whole invocation, and samples in the same run can disagree on library strandedness.
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

_SUFFIX = "_Aligned.sortedByCoord.out.bam"


def sample_id(path: str) -> str:
    base = os.path.basename(path)
    return base[: -len(_SUFFIX)] if base.endswith(_SUFFIX) else os.path.splitext(base)[0]


def load_strand_map(path: str) -> dict[str, int]:
    # Column 3 (reverse-strand ratio, check 21) is tolerated but ignored here.
    strands: dict[str, int] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            sid, _, rest = line.rstrip("\n").partition("\t")
            if sid:
                strands[sid] = int(rest.split("\t", 1)[0])
    return strands


def run_one(bam, gtf, strand, paired, feature, attribute, threads, tmpdir, workdir) -> Path:
    out = Path(workdir) / f"{sample_id(bam)}.fc.txt"
    cmd = [
        "featureCounts", "-a", gtf, "-o", str(out), "-T", str(threads),
        "--tmpDir", tmpdir, "-t", feature, "-g", attribute, "-s", str(strand), "-Q", "10",
    ]
    if paired:
        cmd += ["-p", "--countReadPairs"]
    cmd.append(bam)
    subprocess.run(cmd, check=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bam", required=True, action="append", help="one per sample, in sample order")
    ap.add_argument("--gtf", required=True)
    ap.add_argument("--strand-file", required=True, help="strandedness_per_sample.tsv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--paired", action="store_true")
    ap.add_argument("--feature", default="exon")
    ap.add_argument("--attribute", default="gene_id")
    ap.add_argument("--threads", default="4")
    ap.add_argument("--tmpdir", default=".")
    args = ap.parse_args()

    strands = load_strand_map(args.strand_file)
    samples = [sample_id(b) for b in args.bam]

    with tempfile.TemporaryDirectory(dir=args.tmpdir) as workdir:
        per_sample_out = [
            run_one(bam, args.gtf, strands[sid], args.paired, args.feature, args.attribute,
                    args.threads, args.tmpdir, workdir)
            for bam, sid in zip(args.bam, samples)
        ]

        meta_rows: list[list[str]] = []
        counts: list[list[str]] = []
        for i, out in enumerate(per_sample_out):
            lines = [l for l in out.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
            header = lines[0].split("\t")
            count_col = len(header) - 1
            if i == 0:
                for line in lines[1:]:
                    parts = line.split("\t")
                    meta_rows.append(parts[:count_col])
                    counts.append([parts[count_col]])
            else:
                for j, line in enumerate(lines[1:]):
                    counts[j].append(line.split("\t")[count_col])

        per_sample_s = " ".join(f"{sid}={strands[sid]}" for sid in samples)

        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(
                "# Program:featureCounts (run per-sample; merged, per-sample strandedness); "
                f"per-sample -s: {per_sample_s}\n"
            )
            handle.write("\t".join(["Geneid", "Chr", "Start", "End", "Strand", "Length"] + samples) + "\n")
            for meta, row in zip(meta_rows, counts):
                handle.write("\t".join(meta + row) + "\n")

        status_rows: dict[str, list[str]] = {}
        status_order: list[str] = []
        for i, out in enumerate(per_sample_out):
            summary_path = Path(str(out) + ".summary")
            for line in summary_path.read_text(encoding="utf-8").splitlines()[1:]:
                status, value = line.split("\t")
                if status not in status_rows:
                    status_rows[status] = ["0"] * len(samples)
                    status_order.append(status)
                status_rows[status][i] = value

        os.makedirs(os.path.dirname(args.summary) or ".", exist_ok=True)
        with open(args.summary, "w", encoding="utf-8") as handle:
            handle.write("Status\t" + "\t".join(samples) + "\n")
            for status in status_order:
                handle.write(status + "\t" + "\t".join(status_rows[status]) + "\n")


if __name__ == "__main__":
    main()
