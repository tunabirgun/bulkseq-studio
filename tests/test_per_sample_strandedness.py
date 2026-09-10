import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


star_mod = _load("infer_strandedness_star", SCRIPTS / "infer_strandedness_star.py")
fc_mod = _load("infer_strandedness_fc", SCRIPTS / "infer_strandedness_fc.py")
build_mod = _load("build_star_genecounts", SCRIPTS / "build_star_genecounts.py")
merge_mod = _load("run_featurecounts_per_sample", SCRIPTS / "run_featurecounts_per_sample.py")


def _write_tab(path: Path, fwd: int, rev: int) -> None:
    lines = [
        "N_unmapped\t0\t0\t0",
        "N_multimapping\t0\t0\t0",
        "N_noFeature\t0\t0\t0",
        "N_ambiguous\t0\t0\t0",
        f"gene1\t{fwd + rev}\t{fwd}\t{rev}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_infer_strand_reverse_stranded():
    assert star_mod.infer_strand.__module__ == "infer_strandedness_star"


def test_star_per_sample_disagreement(tmp_path):
    tab_a = tmp_path / "sampleA_ReadsPerGene.out.tab"
    tab_b = tmp_path / "sampleB_ReadsPerGene.out.tab"
    _write_tab(tab_a, fwd=10, rev=90)   # reverse-stranded -> 2
    _write_tab(tab_b, fwd=50, rev=50)   # unstranded -> 0
    assert star_mod.infer_strand(str(tab_a))[0] == 2
    assert star_mod.infer_strand(str(tab_b))[0] == 0


def test_star_uniform_samples_match_single_sample_answer(tmp_path):
    # Byte-identical requirement: when all samples agree, the per-sample answer for the
    # first sample must equal what single-sample inference would have produced.
    tab_a = tmp_path / "sampleA_ReadsPerGene.out.tab"
    tab_b = tmp_path / "sampleB_ReadsPerGene.out.tab"
    _write_tab(tab_a, fwd=90, rev=10)
    _write_tab(tab_b, fwd=90, rev=10)
    legacy = tmp_path / "strandedness.txt"
    per_sample = tmp_path / "strandedness_per_sample.tsv"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "infer_strandedness_star.py"),
         "--out", str(legacy), "--per-sample-out", str(per_sample), str(tab_a), str(tab_b)],
        check=True,
    )
    assert legacy.read_text(encoding="utf-8").strip() == "1"
    lines = per_sample.read_text(encoding="utf-8").strip().splitlines()
    assert [l.split("\t")[:2] for l in lines] == [["sampleA", "1"], ["sampleB", "1"]]


def test_build_star_genecounts_uses_each_sample_own_strand(tmp_path):
    tab_a = tmp_path / "sampleA_ReadsPerGene.out.tab"
    tab_b = tmp_path / "sampleB_ReadsPerGene.out.tab"
    tab_a.write_text(
        "N_unmapped\t0\t0\t0\nN_multimapping\t0\t0\t0\nN_noFeature\t0\t0\t0\n"
        "N_ambiguous\t0\t0\t0\ngene1\t100\t5\t95\n",
        encoding="utf-8",
    )
    tab_b.write_text(
        "N_unmapped\t0\t0\t0\nN_multimapping\t0\t0\t0\nN_noFeature\t0\t0\t0\n"
        "N_ambiguous\t0\t0\t0\ngene1\t100\t40\t60\n",
        encoding="utf-8",
    )
    strand_file = tmp_path / "strandedness_per_sample.tsv"
    strand_file.write_text("sampleA\t2\nsampleB\t0\n", encoding="utf-8")
    out = tmp_path / "counts.txt"
    summary = tmp_path / "counts.txt.summary"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "build_star_genecounts.py"),
         "--strand-file", str(strand_file), "--out", str(out), "--summary", str(summary),
         str(tab_a), str(tab_b)],
        check=True,
    )
    rows = out.read_text(encoding="utf-8").splitlines()
    header = rows[1].split("\t")
    gene_row = rows[2].split("\t")
    a_col = header.index("sampleA")
    b_col = header.index("sampleB")
    assert gene_row[a_col] == "95"  # reverse column for sampleA
    assert gene_row[b_col] == "100"  # unstranded column for sampleB


def test_build_star_genecounts_all_uniform_unchanged(tmp_path):
    # Negative/positive control: when every sample has the same strandedness, the merged
    # counts must equal what a single global --strand call would have produced.
    tab_a = tmp_path / "sampleA_ReadsPerGene.out.tab"
    tab_b = tmp_path / "sampleB_ReadsPerGene.out.tab"
    for t in (tab_a, tab_b):
        t.write_text(
            "N_unmapped\t0\t0\t0\nN_multimapping\t0\t0\t0\nN_noFeature\t0\t0\t0\n"
            "N_ambiguous\t0\t0\t0\ngene1\t100\t5\t95\n",
            encoding="utf-8",
        )
    strand_file = tmp_path / "strandedness_per_sample.tsv"
    strand_file.write_text("sampleA\t2\nsampleB\t2\n", encoding="utf-8")
    out = tmp_path / "counts.txt"
    summary = tmp_path / "counts.txt.summary"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "build_star_genecounts.py"),
         "--strand-file", str(strand_file), "--out", str(out), "--summary", str(summary),
         str(tab_a), str(tab_b)],
        check=True,
    )
    row = out.read_text(encoding="utf-8").splitlines()[2].split("\t")
    assert row[-2:] == ["95", "95"]


def test_merge_featurecounts_per_sample(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, check=True, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1])
        bam = cmd[-1]
        strand = cmd[cmd.index("-s") + 1]
        calls.append((bam, strand))
        val = "10" if strand == "2" else "3"
        out.write_text(f"Geneid\tChr\tStart\tEnd\tStrand\tLength\t{bam}\n"
                        f"gene1\t1\t1\t100\t+\t100\t{val}\n", encoding="utf-8")
        Path(str(out) + ".summary").write_text(
            f"Status\t{bam}\nAssigned\t{val}\nUnassigned_Unmapped\t0\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(merge_mod.subprocess, "run", fake_run)
    bam_a = str(tmp_path / "sampleA_Aligned.sortedByCoord.out.bam")
    bam_b = str(tmp_path / "sampleB_Aligned.sortedByCoord.out.bam")
    strand_file = tmp_path / "strandedness_per_sample.tsv"
    strand_file.write_text("sampleA\t2\nsampleB\t0\n", encoding="utf-8")
    out = tmp_path / "counts.txt"
    summary = tmp_path / "counts.txt.summary"

    import sys as _sys
    argv = [
        "run_featurecounts_per_sample.py", "--bam", bam_a, "--bam", bam_b,
        "--gtf", "genes.gtf", "--strand-file", str(strand_file),
        "--out", str(out), "--summary", str(summary), "--tmpdir", str(tmp_path),
    ]
    monkeypatch.setattr(_sys, "argv", argv)
    merge_mod.main()

    assert [c[1] for c in calls] == ["2", "0"]
    rows = out.read_text(encoding="utf-8").splitlines()
    header = rows[1].split("\t")
    values = rows[2].split("\t")
    assert values[header.index("sampleA")] == "10"
    assert values[header.index("sampleB")] == "3"


def test_hisat2_strandedness_per_sample_output(tmp_path, monkeypatch):
    def fake_assigned(bam, gtf, strand, paired, feature, attribute, threads, tmpdir):
        if "sampleA" in bam:
            return 90 if strand == 2 else 10
        return 50

    monkeypatch.setattr(fc_mod, "assigned", fake_assigned)
    bam_a = str(tmp_path / "sampleA_Aligned.sortedByCoord.out.bam")
    bam_b = str(tmp_path / "sampleB_Aligned.sortedByCoord.out.bam")
    legacy = tmp_path / "strandedness.txt"
    per_sample = tmp_path / "strandedness_per_sample.tsv"
    import sys as _sys
    argv = ["infer_strandedness_fc.py", "--bam", bam_a, "--bam", bam_b, "--gtf", "genes.gtf",
            "--out", str(legacy), "--per-sample-out", str(per_sample)]
    monkeypatch.setattr(_sys, "argv", argv)
    fc_mod.main()
    lines = {l.split("\t")[0]: l.split("\t")[1] for l in per_sample.read_text(encoding="utf-8").splitlines()}
    assert lines["sampleA"] == "2"
    assert lines["sampleB"] == "0"
    assert legacy.read_text(encoding="utf-8").strip() == "2"


def _extract_rule_shell(rule_name: str, smk_path: Path) -> str:
    """Pull the literal shell string out of one `rule <name>:` block in a .smk file.

    Matches the actual pipeline code (so a future edit to the shell is exercised by the
    test), rather than a Python reimplementation that could silently drift from it.
    """
    import re

    text = smk_path.read_text(encoding="utf-8")
    m = re.search(
        # Allow comment lines between "shell:" and the opening r""" (read_length's shell
        # is preceded by one), so this can't skip past the intended rule to a later one.
        rf'rule {re.escape(rule_name)}:.*?shell:\s*\n(?:\s*#[^\n]*\n)*\s*r"""(.*?)"""',
        text, re.S,
    )
    assert m, f"rule {rule_name} shell not found in {smk_path}"
    # Snakemake shell strings double literal braces ({{ }}) around Python-format
    # placeholders ({input:q}, {output}); un-double them once those are substituted below.
    return m.group(1)


def _bash_available() -> bool:
    """True when this host can run a bash script — WSL2 on Windows (the only place bash
    reaches, per the project's tri-platform verification matrix), bash directly elsewhere."""
    import shutil

    if sys.platform.startswith("win"):
        return shutil.which("wsl") is not None
    return shutil.which("bash") is not None


def _run_read_length_shell(fastq_paths, tmp_path) -> int:
    import shlex

    from app.core.paths import windows_to_wsl_path

    if not _bash_available():
        pytest.skip("bash is not available on this host (install WSL2 on Windows)")
    shell = _extract_rule_shell("read_length", ROOT / "workflow" / "rules" / "reference.smk")
    output = tmp_path / "read_length.txt"
    on_windows = sys.platform.startswith("win")
    # Passing a multi-line script as a `wsl bash -c "..."` argument goes through Windows'
    # own command-line requoting on the way to wsl.exe and corrupts embedded quotes/newlines
    # (observed: `$f` expanded to empty inside the for-loop). Writing the script to a file
    # and running `bash <file>` sidesteps that relay entirely.
    to_shell_path = (lambda p: windows_to_wsl_path(p)) if on_windows else (lambda p: str(p))
    quoted_inputs = " ".join(shlex.quote(to_shell_path(p)) for p in fastq_paths)
    script = (
        shell.replace("{{", "{").replace("}}", "}")
        .replace("{input:q}", quoted_inputs)
        .replace("{output}", shlex.quote(to_shell_path(output)))
    )
    script_file = tmp_path / "read_length.sh"
    script_file.write_text(script, encoding="utf-8", newline="\n")
    bash_cmd = (["wsl", "bash", windows_to_wsl_path(script_file)] if on_windows
                else ["bash", str(script_file)])
    result = subprocess.run(bash_cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.exists(), f"shell did not write {output}: {result.stdout} {result.stderr}"
    return int(output.read_text(encoding="utf-8").strip())


def test_read_length_takes_max_across_samples(tmp_path):
    import gzip

    short = tmp_path / "short.fastq.gz"
    long_ = tmp_path / "long.fastq.gz"
    # newline="" keeps the fixture's line endings as plain "\n" on every OS (Windows text
    # mode would otherwise write "\r\n", off-by-one inflating the length the shell reads).
    with gzip.open(short, "wt", newline="") as h:
        h.write("@r\nACGT\n+\nIIII\n")
    with gzip.open(long_, "wt", newline="") as h:
        h.write("@r\nACGTACGTAC\n+\nIIIIIIIIII\n")

    # The shorter fastq listed first must not cap the result: the max, not the first, wins.
    assert _run_read_length_shell([short, long_], tmp_path) == 10
    # Uniform read lengths reproduce the single-sample answer unchanged.
    assert _run_read_length_shell([long_, long_], tmp_path) == 10


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
