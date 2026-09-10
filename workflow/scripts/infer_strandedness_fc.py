#!/usr/bin/env python3
# Infer library strandedness per sample for the HISAT2 route. STAR gets this free from its
# ReadsPerGene table; HISAT2 does not, so count each sample's BAM with featureCounts in
# forward (-s 1) and reverse (-s 2) modes and compare the assigned fragments, using the same
# rev/(fwd+rev) ratio and 0.7/0.3 thresholds as the STAR path (the ratio is a property of the
# library prep, not the counter). Run independently per sample so a mixed-strandedness
# multi-study run is not miscounted from one sample's answer.
# Paired libraries MUST be counted with -p --countReadPairs: otherwise read1 and
# read2 of each fragment land in opposite strand buckets (fwd ~= rev) and a genuinely
# stranded library is misread as unstranded.
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

_SUFFIX = "_Aligned.sortedByCoord.out.bam"


def sample_id(path: str) -> str:
    base = os.path.basename(path)
    return base[: -len(_SUFFIX)] if base.endswith(_SUFFIX) else os.path.splitext(base)[0]


def assigned(bam, gtf, strand, paired, feature, attribute, threads, tmpdir):
    with tempfile.TemporaryDirectory(dir=tmpdir) as td:
        out = Path(td) / "fc.txt"
        cmd = [
            "featureCounts", "-a", gtf, "-o", str(out), "-T", str(threads),
            "--tmpDir", td, "-t", feature, "-g", attribute, "-s", str(strand), "-Q", "10",
        ]
        if paired:
            cmd += ["-p", "--countReadPairs"]
        cmd.append(bam)
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for line in Path(str(out) + ".summary").read_text(encoding="utf-8").splitlines():
            if line.startswith("Assigned\t"):
                return int(line.split("\t")[1])
    return 0


def infer_strand(bam, gtf, paired, feature, attribute, threads, tmpdir) -> tuple[int, float]:
    fwd = assigned(bam, gtf, 1, paired, feature, attribute, threads, tmpdir)
    rev = assigned(bam, gtf, 2, paired, feature, attribute, threads, tmpdir)
    total = fwd + rev
    ratio = (rev / total) if total else 0.5
    strand = 2 if ratio > 0.7 else (1 if ratio < 0.3 else 0)
    print(f"{sample_id(bam)}: fwd(-s1)={fwd} rev(-s2)={rev} ratio={ratio:.3f} -> strandedness={strand}")
    return strand, ratio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bam", required=True, action="append", help="one per sample, in sample order")
    ap.add_argument("--gtf", required=True)
    ap.add_argument("--out", required=True, help="legacy single-value strandedness.txt (first sample)")
    ap.add_argument("--per-sample-out", required=True, help="sample_id<TAB>strand, one per line")
    ap.add_argument("--threads", default="4")
    ap.add_argument("--tmpdir", default=".")
    ap.add_argument("--paired", action="store_true")
    ap.add_argument("--feature", default="exon")
    ap.add_argument("--attribute", default="gene_id")
    a = ap.parse_args()

    per_sample = {
        sample_id(bam): infer_strand(bam, a.gtf, a.paired, a.feature, a.attribute, a.threads, a.tmpdir)
        for bam in a.bam
    }

    os.makedirs(os.path.dirname(a.per_sample_out) or ".", exist_ok=True)
    with open(a.per_sample_out, "w", encoding="utf-8") as handle:
        for sid, (strand, ratio) in per_sample.items():
            handle.write(f"{sid}\t{strand}\t{ratio:.4f}\n")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    Path(a.out).write_text(f"{per_sample[sample_id(a.bam[0])][0]}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
