from __future__ import annotations

import gzip
import os
from pathlib import Path
import sys


root = Path(os.environ["BULKSEQ_SMOKE_ROOT"])
out = root / "ribodetector"


def records(path: Path) -> dict[str, tuple[str, str]]:
    if not path.is_file():
        raise ValueError(f"Missing FASTQ output: {path.name}")
    with gzip.open(path, "rt", encoding="ascii") as handle:
        lines = [line.rstrip("\n") for line in handle]
    if len(lines) % 4:
        raise ValueError(f"Malformed FASTQ output: {path.name}")
    result = {}
    for index in range(0, len(lines), 4):
        name, sequence, plus, quality = lines[index:index + 4]
        if not name.startswith("@") or plus != "+" or len(sequence) != len(quality):
            raise ValueError(f"Invalid FASTQ record in {path.name}")
        if name in result:
            raise ValueError(f"Duplicate read in {path.name}: {name}")
        result[name] = (sequence, quality)
    return result


source = records(root / "inputs/reads.fastq.gz")
non = records(out / "non_rrna.fastq.gz")
rrna = records(out / "rrna.fastq.gz")


def check_outputs(non_rrna: dict, ribosomal: dict) -> None:
    if non_rrna.keys() & ribosomal.keys() or (non_rrna | ribosomal) != source:
        raise ValueError("RiboDetector changed read identities, sequences or qualities")


check_outputs(non, rrna)
if len(source) != 27 or not (out / "classification.log").is_file():
    raise SystemExit("Classification did not cover the 27-read fixture")
if sys.argv[1:] == ["--negative-sequence"]:
    changed = dict(non or rrna)
    first = next(iter(changed))
    sequence, quality = changed[first]
    changed[first] = (sequence + "A", quality)
    check_outputs(changed if non else non, rrna if non else changed)
elif sys.argv[1:]:
    raise SystemExit("Unknown RiboDetector checker argument")
print(f"PASS: packaged CPU classifier preserved {len(non)} non-rRNA and {len(rrna)} rRNA "
      "read records. This is not an accuracy benchmark")
