from __future__ import annotations

import ast
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _star_extra(value):
    source = (PROJECT_ROOT / "workflow/rules/alignment.smk").read_text(encoding="utf-8")
    namespace = {"config": {"star": {"extra": value}}, "shlex": shlex}
    sys.path.insert(0, str(PROJECT_ROOT / "workflow/scripts"))
    try:
        exec(compile(source.split("def _star_bam_sort_ram_bytes", 1)[0], "alignment.smk", "exec"), namespace)
    finally:
        sys.path.pop(0)
    return namespace["_STAR_EXTRA"]


class StarExtraValidationTests(unittest.TestCase):
    def test_rejects_malformed_or_empty_arguments(self):
        from app.core.config_models import StarConfig

        for value in ('--outSAMattrRGline "unclosed', "--outSAMattrRGline dangling\\", '--outSAMattrRGline ""',
                      "--outSAMattrRGline ID:bad\0value",
                      '--outFilterScoreMin "1\nsysShell /bin/sh"', '--outSAMattrRGline "ID:line\rbreak"'):
            for parser in (_star_extra, lambda text: StarConfig(extra=text)):
                with self.subTest(value=value, parser=parser), self.assertRaises(ValueError):
                    parser(value)

    def test_rejects_command_capable_star_options(self):
        from app.core.config_models import StarConfig

        for option in ("--readFilesCommand", "--sysShell", "--parametersFiles", "--readFilesPrefix", "--readFilesManifest"):
            for value in (option + " value", option + "=value", f'"{option} value"', f'"-- {option[2:]} value"',
                          f'"{option[:4]}"{option[4:]} value'):
                for parser in (_star_extra, lambda text: StarConfig(extra=text)):
                    with self.subTest(value=value, parser=parser), self.assertRaisesRegex(ValueError, "star.extra"):
                        parser(value)

    def test_preserves_ordinary_flags(self):
        from app.core.config_models import StarConfig

        value = '--outFilterScoreMin 10 --outSAMattrRGline "ID:group one" SM:group'
        self.assertEqual(StarConfig(extra=value).extra, value)
        self.assertEqual(_star_extra(value), shlex.split(value))
        self.assertEqual(_star_extra(""), [])

    def test_rejects_bare_option_name_bypasses(self):
        from app.core.config_models import StarConfig

        for name in ("readFilesCommand", "sysShell", "parametersFiles", "readFilesPrefix", "readFilesManifest"):
            for value in (f"-- {name} /synthetic/value", f"--={name} /synthetic/value"):
                for parser in (_star_extra, lambda text: StarConfig(extra=text)):
                    with self.subTest(value=value, parser=parser), self.assertRaisesRegex(ValueError, "star.extra"):
                        parser(value)


@unittest.skipUnless(os.name == "posix" and shutil.which("STAR"), "requires the STAR backend")
class StarParserBoundaryTests(unittest.TestCase):
    def test_actual_star_reparses_bare_options_but_extra_validation_rejects_them(self):
        for extra in ("--outFilterScoreMin 10", "-- sysShell /synthetic/not-a-shell",
                      "--=sysShell /synthetic/not-a-shell"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                completed = subprocess.run(
                    [shutil.which("STAR"), "--genomeDir", "missing", *shlex.split(extra)],
                    cwd=directory, capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("could not open readFilesIn", completed.stderr)
                log = (Path(directory) / "Log.out").read_text(encoding="utf-8")
                if "sysShell" in extra:
                    self.assertRegex(log, r"(?m)^sysShell\s+/synthetic/not-a-shell\s+~RE-DEFINED$")
                    with self.assertRaisesRegex(ValueError, "star.extra"):
                        _star_extra(extra)
                else:
                    self.assertNotRegex(log, r"(?m)^sysShell.*~RE-DEFINED$")
                    self.assertEqual(_star_extra(extra), shlex.split(extra))


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("snakemake"),
                     "requires POSIX and the Snakemake backend")
class ToolShellQuotingTests(unittest.TestCase):
    def _run_shell(self, filename, rule, tool, params):
        from snakemake.utils import format as shell_format

        source = (PROJECT_ROOT / "workflow/rules" / filename).read_text(encoding="utf-8")
        shell_source = source.split(f"rule {rule}:", 1)[1].split("shell:", 1)[1].split("\n\n", 1)[0]
        template = ast.literal_eval("(" + shell_source.strip() + ")")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            stub = binary / tool
            stub.write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$ARGUMENTS"\n', encoding="utf-8")
            stub.chmod(0o700)
            scratch = root / "scratch"
            scratch.mkdir()
            self.assertTrue(scratch.resolve().is_relative_to(root.resolve()))
            arguments = root / "arguments.bin"
            log = root / "tool.log"
            command = shell_format(
                template,
                input=SimpleNamespace(index="index", fastqs=["read 1.fastq.gz"], gtf="annotation.gtf",
                                      strand="strandedness.tsv"),
                output=SimpleNamespace(legacy="strandedness.txt", per_sample="strandedness.tsv",
                                       counts="counts.tsv", summary="summary.tsv"),
                resources=SimpleNamespace(tmpdir=str(scratch)),
                wildcards=SimpleNamespace(sample="synthetic"), threads=1,
                params=SimpleNamespace(**params), log=str(log),
            )
            completed = subprocess.run(
                [shutil.which("bash"), "-c", command], cwd=root, capture_output=True, text=True, timeout=10,
                env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                     "ARGUMENTS": str(arguments)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = completed.stdout + completed.stderr + (log.read_text(encoding="utf-8") if log.exists() else "")
            self.assertNotIn("EXTRA_EXECUTED", output)
            return [value.decode() for value in arguments.read_bytes().split(b"\0")[:-1]]

    def test_star_extra_tokens_do_not_execute(self):
        for value in ("", "--outFilterScoreMin 10 --outFilterMatchNmin 5",
                      '--outSAMattrRGline "ID:group one" \'SM:sample\'',
                      "--outFilterScoreMin $(printf EXTRA_EXECUTED >&2)",
                      "--outFilterScoreMin `printf EXTRA_EXECUTED >&2`",
                      '--outSAMattrRGline "ID:$(printf EXTRA_EXECUTED >&2)"',
                      "--outFilterScoreMin 1\nprintf EXTRA_EXECUTED >&2\n#",
                      "--outFilterScoreMin 1; printf EXTRA_EXECUTED >&2"):
            with self.subTest(extra=value):
                observed = self._run_shell("alignment.smk", "star_align", "STAR", {
                    "bam_sort_ram": 1000000, "multimap": 10, "mismatch_nover": 1.0,
                    "twopass": "None", "extra": _star_extra(value),
                })
                start = observed.index("--twopassMode") + 2
                stop = observed.index("--outTmpDir")
                self.assertEqual(observed[start:stop], shlex.split(value))

    def test_featurecounts_values_are_single_arguments(self):
        for filename, rule in (("alignment.smk", "infer_strandedness"), ("quantification.smk", "featurecounts")):
            for field in ("feature", "attribute"):
                for value in ("gene_id", "gene id", "gene$(printf EXTRA_EXECUTED >&2)",
                              "gene`printf EXTRA_EXECUTED >&2`", "gene'quoted", "gene\nprintf EXTRA_EXECUTED >&2\n#"):
                    with self.subTest(rule=rule, field=field, value=value):
                        params = {"bam_args": "--bam synthetic.bam", "paired": "--paired",
                                  "feature": "exon", "attribute": "gene_id", field: value}
                        observed = self._run_shell(filename, rule, "python", params)
                        position = observed.index("--" + field)
                        self.assertEqual(observed[position + 1], value)

    def test_fastq_screen_missing_path_is_literal_in_diagnostic(self):
        from snakemake.utils import format as shell_format

        source = (PROJECT_ROOT / "workflow/rules/qc.smk").read_text(encoding="utf-8")
        shell_source = source.split("rule fastq_screen:", 1)[1].split("shell:", 1)[1].split("\n\n", 1)[0]
        template = ast.literal_eval("(" + shell_source.replace("_FS_GUARD +", "", 1).strip() + ")")
        for value in ("missing config.conf", "missing'quoted.conf",
                      "missing'$(printf injected > \"$DIAGNOSTIC_MARKER\")'",
                      "missing'`printf injected > \"$DIAGNOSTIC_MARKER\"`'"):
            with self.subTest(path=value), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "executed.txt"
                command = shell_format(template, params=SimpleNamespace(conf=value, subset=100000),
                                       input=SimpleNamespace(r1="synthetic.fastq.gz"),
                                       output=SimpleNamespace(txt="screen.txt"), threads=1,
                                       wildcards=SimpleNamespace(sample="synthetic"), log="screen.log")
                completed = subprocess.run(
                    [shutil.which("bash"), "-c", command], cwd=directory, capture_output=True, text=True,
                    timeout=10, env={**os.environ, "DIAGNOSTIC_MARKER": str(marker)},
                )
                self.assertFalse(marker.exists(), completed.stderr)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(value, completed.stderr)


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT))
    unittest.main()
