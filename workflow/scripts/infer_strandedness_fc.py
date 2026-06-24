#!/usr/bin/env python3
# Infer library strandedness for the HISAT2 route. STAR gets this free from its
# ReadsPerGene table; HISAT2 does not, so count the first sample's BAM with
# featureCounts in forward (-s 1) and reverse (-s 2) modes and compare the assigned
# fragments, using the same rev/(fwd+rev) ratio and 0.7/0.3 thresholds as the STAR
# path (the ratio is a property of the library prep, not the counter).
# Paired libraries MUST be counted with -p --countReadPairs: otherwise read1 and
# read2 of each fragment land in opposite strand buckets (fwd ~= rev) and a genuinely
# stranded library is misread as unstranded.
import argparse
import subprocess
import tempfile
from pathlib import Path


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bam", required=True)
    ap.add_argument("--gtf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", default="4")
    ap.add_argument("--tmpdir", default=".")
    ap.add_argument("--paired", action="store_true")
    ap.add_argument("--feature", default="exon")
    ap.add_argument("--attribute", default="gene_id")
    a = ap.parse_args()
    fwd = assigned(a.bam, a.gtf, 1, a.paired, a.feature, a.attribute, a.threads, a.tmpdir)
    rev = assigned(a.bam, a.gtf, 2, a.paired, a.feature, a.attribute, a.threads, a.tmpdir)
    total = fwd + rev
    ratio = (rev / total) if total else 0.5
    strand = 2 if ratio > 0.7 else (1 if ratio < 0.3 else 0)
    print(f"fwd(-s1)={fwd} rev(-s2)={rev} ratio={ratio:.3f} -> strandedness={strand}")
    Path(a.out).write_text(f"{strand}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
