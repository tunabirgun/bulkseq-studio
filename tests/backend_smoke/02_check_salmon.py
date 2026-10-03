from __future__ import annotations

import csv
import gzip
import math
import os
from pathlib import Path
import sys


project = Path(os.environ["BULKSEQ_SMOKE_ROOT"]) / "projects" / "salmon"
genome = "".join((project / "inputs/genome.fa").read_text(encoding="ascii").splitlines()[1:])
transcripts = {}
with (project / "inputs/annotation.gtf").open(encoding="ascii") as handle:
    for line in handle:
        fields = line.rstrip("\n").split("\t")
        attributes = dict(item.strip().split(" ", 1) for item in fields[8].split(";") if item.strip())
        transcript = attributes["transcript_id"].strip('"')
        transcripts[transcript] = genome[int(fields[3]) - 1:int(fields[4])]
if len(transcripts) != 3:
    raise SystemExit("Synthetic annotation did not yield three transcripts")

planted = {name: 0 for name in transcripts}
with gzip.open(project / "inputs/reads.fastq.gz", "rt", encoding="ascii") as handle:
    lines = [line.rstrip("\n") for line in handle]
if len(lines) != 27 * 4:
    raise SystemExit("Synthetic read count changed")
for index in range(0, len(lines), 4):
    read = lines[index + 1]
    matches = [name for name, sequence in transcripts.items() if read in sequence]
    if len(matches) != 1:
        raise SystemExit(f"Read {index // 4 + 1} does not have one exact transcript origin")
    planted[matches[0]] += 1

with (project / "references/tx2gene.tsv").open(encoding="utf-8") as handle:
    tx2gene = dict(csv.reader(handle, delimiter="\t"))
with (project / "results/salmon/S01/quant.sf").open(encoding="utf-8") as handle:
    quant = list(csv.DictReader(handle, delimiter="\t"))
if set(tx2gene) != set(transcripts) or {row["Name"] for row in quant} != set(transcripts):
    raise SystemExit("Salmon transcript names and tx2gene disagree with the fixture")
if len(set(tx2gene.values())) != len(transcripts):
    raise SystemExit("The independent one-transcript-per-gene calculation no longer applies")
for row in quant:
    if not math.isclose(float(row["NumReads"]), planted[row["Name"]], abs_tol=1e-3):
        raise SystemExit(f"Salmon quantification differs from planted reads: {row['Name']}")

# With one sample and one transcript per gene, lengthScaledTPM weights each TPM by
# its effective length, then rescales to the observed library size before R's round().
weighted = {row["Name"]: float(row["TPM"]) * float(row["EffectiveLength"])
            for row in quant}
scale = sum(float(row["NumReads"]) for row in quant) / sum(weighted.values())
expected = {tx2gene[transcript]: round(value * scale)
            for transcript, value in weighted.items()}
with (project / "results/counts/counts.txt").open(encoding="utf-8") as handle:
    actual = {row["Geneid"]: int(row["S01"])
              for row in csv.DictReader((line for line in handle if not line.startswith("#")),
                                        delimiter="\t")}


def check_counts(observed: dict[str, int]) -> None:
    if observed != expected:
        raise ValueError(f"gene counts differ from independent lengthScaledTPM: {observed}")


check_counts(actual)
if sys.argv[1:] == ["--negative-count"]:
    altered = dict(actual)
    altered[sorted(altered)[0]] += 1
    check_counts(altered)
elif sys.argv[1:]:
    raise SystemExit("Unknown Salmon checker argument")
print(f"PASS: {sum(planted.values())} planted reads, {len(transcripts)} quant transcripts and "
      f"{len(actual)} gene counts consistent with the configured lengthScaledTPM/rounding path")
