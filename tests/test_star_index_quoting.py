from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("snakemake"),
                     "requires POSIX and the Snakemake backend")
class StarIndexQuotingTests(unittest.TestCase):
    def test_index_path_is_one_literal_argument(self):
        from snakemake.utils import format as shell_format

        source = (PROJECT_ROOT / "workflow/rules/alignment.smk").read_text(encoding="utf-8")
        shell_source = source.split("rule star_align:", 1)[1].split("shell:", 1)[1].split("\n\n", 1)[0]
        template = ast.literal_eval("(" + shell_source.strip() + ")")
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        for name in ("index", "index with spaces", "index$(printf SHELL_EXECUTED >&2)",
                     "index`printf SHELL_EXECUTED >&2`", "index'quoted"):
            with self.subTest(index=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = root / name
                index.mkdir()
                binary = root / "bin"
                binary.mkdir()
                stub = binary / "STAR"
                stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$STAR_ARGUMENTS"\n', encoding="utf-8")
                stub.chmod(0o700)
                scratch = root / "scratch"
                scratch.mkdir()
                self.assertTrue(scratch.resolve().is_relative_to(root.resolve()))
                arguments = root / "arguments.txt"
                log = root / "star.log"
                fastqs = [str(root / "read 1.fastq.gz"), str(root / "read 2.fastq.gz")]
                command = shell_format(
                    template,
                    input=SimpleNamespace(index=str(index), fastqs=fastqs),
                    resources=SimpleNamespace(tmpdir=str(scratch)),
                    wildcards=SimpleNamespace(sample="synthetic"), threads=1,
                    params=SimpleNamespace(bam_sort_ram=1000000, multimap=10, mismatch_nover=1.0,
                                           twopass="None", extra=""), log=str(log),
                )
                completed = subprocess.run(
                    [bash, "-c", command], cwd=root, capture_output=True, text=True, timeout=10,
                    env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                         "STAR_ARGUMENTS": str(arguments)},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn("SHELL_EXECUTED", completed.stderr + log.read_text(encoding="utf-8"))
                observed = arguments.read_text(encoding="utf-8").splitlines()
                position = observed.index("--genomeDir")
                self.assertEqual(observed[position + 1:position + 3], [str(index), "--readFilesIn"])
                self.assertEqual(observed[position + 3:position + 5], fastqs)


if __name__ == "__main__":
    unittest.main()
