"""0.37.0 cluster hardening: every Slurm setting reaches Snakemake, split stages, the
single-allocation script, scheduling-only saves, and the workflow changes that must leave a
local run's commands untouched."""
from __future__ import annotations

import ast
import os
import re
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.cli import EXIT_OK, main
from app.core.cluster_settings import documented_fields
from app.core.config_models import AppConfig, ExecutionConfig, RuleRuntimeMin
from app.core.project import ProjectManager
from app.core import snakemake_runner
from app.core.slurm_script import ALLOCATION_SCRIPT, allocation_directives, container_binds, render_allocation_script
from app.core.snakemake_runner import (
    ONLINE_ANALYSIS_RULES,
    PREFETCH_RULES,
    STEP_RULES,
    _slurm_minutes,
    build_snakemake_args,
    cluster_default_resources,
    cluster_launch_problems,
    rule_resource_overrides,
    slurm_plugin_args,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / "workflow"


@pytest.fixture()
def project(tmp_path) -> Path:
    return ProjectManager().create_project("hardening", tmp_path)


def _config(project: Path, **execution) -> AppConfig:
    manager = ProjectManager()
    config = manager.load_config(project)
    for key, value in execution.items():
        setattr(config.execution, key, value)
    manager.save_config(project, config)
    return manager.load_config(project)


def _values(argv: list[str], option: str) -> list[str]:
    values = []
    for token in argv[argv.index(option) + 1:]:
        if token.startswith("--"):
            break
        values.append(token)
    return values


# ------------------------------------------------------------------ arguments

def test_local_runs_get_none_of_the_cluster_options(project) -> None:
    argv = build_snakemake_args(_config(project, slurm_qos="q", tmpdir="/scratch/t", retries=3),
                                "run", project, exec_profile="local")
    for option in ("--local-cores", "--latency-wait", "--retries", "--set-resources", "--slurm-qos",
                   "--default-resources", "--until", "--omit-from"):
        assert option not in argv


def test_every_slurm_placement_setting_becomes_a_quoted_default_resource(project) -> None:
    config = _config(project, slurm_partition="compute", slurm_account="proj", slurm_qos="normal",
                     slurm_constraint="nodeA|nodeB", slurm_clusters="clusterA", tmpdir="/scratch/u/tmp",
                     min_cpus_per_job=56)
    resources = cluster_default_resources(config, "slurm")
    assert "qos='normal'" in resources and "constraint='nodeA|nodeB'" in resources
    # The fallback walltime scales with the attempt (retries=1 by default); memory only under a cap.
    assert resources[0] == "runtime=240*attempt" and resources[1] == "mem_mb=max(2*input.size_mb, 4000)"
    capped = cluster_default_resources(_config(project, max_runtime_min=600, max_mem_mb=250000), "slurm")
    assert capped[0] == "runtime=min(240*attempt, 600)"
    assert capped[1] == "mem_mb=min(max(2*input.size_mb, 4000)*attempt, 250000)"
    assert cluster_default_resources(_config(project, retries=0), "slurm")[0] == "runtime=240"
    assert "clusters='clusterA'" in resources and "tmpdir='/scratch/u/tmp'" in resources
    assert "cpus_per_task=max(threads, 56)" in resources
    for item in resources:
        ast.parse(item.split("=", 1)[1], mode="eval")


def test_plugin_settings_map_to_their_flags(project) -> None:
    config = _config(project, slurm_requeue="requeue", slurm_no_account=True, slurm_reservation="maint",
                     slurm_array_jobs="fastp,star_align", slurm_array_limit=500, slurm_status_command="squeue",
                     slurm_jobname_prefix="bsq", slurm_exclude_nodes="a[001-002]",
                     slurm_pass_command_as_script=True, slurm_efficiency_report=True)
    args = slurm_plugin_args(config)
    for expected in (["--slurm-requeue"], ["--slurm-no-account"], ["--slurm-reservation", "maint"],
                     ["--slurm-array-jobs", ",".join([*STEP_RULES["fastp"], "star_align"])], ["--slurm-array-limit", "500"],
                     ["--slurm-status-command", "squeue"], ["--slurm-jobname-prefix", "bsq"],
                     ["--slurm-exclude-failed-nodes", "a[001-002]"], ["--slurm-pass-command-as-script"],
                     ["--slurm-efficiency-report"]):
        position = args.index(expected[0])
        assert args[position:position + len(expected)] == expected
    # Old logs are never deleted: a resumed run must keep the record of the first attempt.
    assert args[args.index("--slurm-delete-logfiles-older-than") + 1] == "0"
    # The plugin treats the report path as a directory.
    assert args[args.index("--slurm-efficiency-report-path") + 1] == "logs/slurm"
    # A step key covers every rule that reads it; rule names pass through.
    fastp = slurm_plugin_args(_config(project, slurm_array_jobs="fastp,star_align"))
    assert fastp[fastp.index("--slurm-array-jobs") + 1] == ",".join([*STEP_RULES["fastp"], "star_align"])


def test_cluster_runs_carry_the_scheduling_settings(project) -> None:
    config = _config(project, local_cores=3, latency_wait_s=120, retries=2)
    argv = build_snakemake_args(config, "run", project, exec_profile="slurm")
    assert _values(argv, "--local-cores") == ["3"]
    assert _values(argv, "--latency-wait") == ["120"]
    assert _values(argv, "--retries") == ["2"]


def test_per_rule_placement_becomes_set_resources(project) -> None:
    config = _config(project)
    config.execution.rule_resources = {"star_index": {"slurm_partition": "bigmem", "qos": "long"},
                                       "deseq2": {"slurm_extra": "--nice=3"}}
    config = AppConfig.model_validate(config.model_dump(mode="json", warnings=False))
    # A step key names every rule that reads its settings; `deseq2` covers the other DE engines.
    assert rule_resource_overrides(config, "slurm") == [
        *(f"{rule}:slurm_extra='--nice=3'" for rule in STEP_RULES["deseq2"]),
        "star_index:slurm_partition='bigmem'", "star_index:qos='long'"]
    assert rule_resource_overrides(config, "kubernetes") == []
    argv = build_snakemake_args(config, "run", project, exec_profile="slurm")
    assert "star_index:qos='long'" in _values(argv, "--set-resources")


def test_every_step_key_names_rules_the_workflow_defines() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOW.rglob("*.smk"))
    rules = set(re.findall(r"^\s*rule (\w+):", text, re.M))
    from app.core.config_models import RuleMemoryGb, RuleRuntimeMin, RuleThreads
    from app.core.snakemake_runner import step_rules
    keys = set(RuleThreads.model_fields) | set(RuleMemoryGb.model_fields) | set(RuleRuntimeMin.model_fields)
    for key in keys:
        expanded = step_rules(key)
        assert set(expanded) <= rules, (key, set(expanded) - rules)
    for key, expanded in STEP_RULES.items():
        assert key in keys and set(expanded) <= rules


@pytest.mark.parametrize("bad", [{"star index": {}}, {"x;y": {}}])
def test_rule_resources_keys_must_be_rule_names(bad) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(rule_resources=bad)


@pytest.mark.parametrize("field,value", [
    ("slurm_constraint", "a b"), ("slurm_jobname_prefix", "has space"), ("slurm_qos", "q;x"),
    ("slurm_array_jobs", "all,fastp x"), ("tmpdir", "$TMPDIR"), ("container_image", "a'b"),
    ("slurm_extra", "--qos=normal"), ("slurm_extra", "--constraint=x"),
    ("slurm_exclude_nodes", "a[040,042]"), ("container_binds", "relative/path"), ("container_binds", ["/a:/b"]),
])
def test_execution_values_are_validated(field, value) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(**{field: value})


def test_local_pool_can_be_overridden_for_one_launch(project) -> None:
    argv = build_snakemake_args(_config(project), "run", project, exec_profile="local", cores=96, memory_gb=240)
    assert _values(argv, "--cores") == ["96"]
    assert _values(argv, "--resources")[0] == "mem_mb=240000"


# ------------------------------------------------------------------ split stages

def test_prefetch_runs_only_the_downloads_that_exist(project) -> None:
    rules = ["all", "download_fastq", "download_genome", "star_align"]
    argv = build_snakemake_args(_config(project), "run", project, stage="prefetch", defined_rules=rules)
    assert _values(argv, "--until") == ["download_fastq", "download_genome"]


def test_prefetch_with_nothing_to_download_is_an_explicit_error(project) -> None:
    with pytest.raises(ValueError):
        build_snakemake_args(_config(project), "run", project, stage="prefetch", defined_rules=["all", "deseq2"])


def test_compute_stage_omits_the_online_analysis_steps(project) -> None:
    rules = ["all", "deseq2", "enrichment", "network_string"]
    argv = build_snakemake_args(_config(project), "run", project, stage="compute", defined_rules=rules)
    assert _values(argv, "--omit-from") == ["enrichment", "network_string"]
    argv = build_snakemake_args(_config(project), "run", project, stage="online", defined_rules=rules)
    assert _values(argv, "--until") == ["enrichment", "network_string"]
    with pytest.raises(ValueError):
        build_snakemake_args(_config(project), "run", project, stage="online", defined_rules=["all", "deseq2"])


def test_stage_rule_lists_are_rules_the_workflow_defines() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOW.rglob("*.smk"))
    for name in (*PREFETCH_RULES, *ONLINE_ANALYSIS_RULES):
        assert re.search(rf"^\s*rule {name}:", text, re.M), name


def test_online_analysis_steps_run_on_the_submit_host_too() -> None:
    snakefile = (WORKFLOW / "Snakefile").read_text(encoding="utf-8")
    network = re.search(r"NETWORK_RULES = \((.*?)\)", snakefile, re.S).group(1)
    for name in (*PREFETCH_RULES, *ONLINE_ANALYSIS_RULES):
        assert f'"{name}"' in network


def test_cli_rejects_a_stage_with_a_cluster_profile(project) -> None:
    assert main(["run", "-C", str(project), "--exec-profile", "slurm", "--stage", "compute"]) == 2


# ------------------------------------------------------------------ allocation script

def test_allocation_script_asks_for_one_whole_node_by_default(project) -> None:
    config = _config(project, slurm_partition="compute", slurm_account="proj", slurm_extra="--mail-type=FAIL")
    directives = allocation_directives(config, "bulkseq-x")
    for expected in ("--nodes=1", "--exclusive", "--mem=0", "--partition=compute", "--account=proj",
                     "--time=1-00:00:00", "--mail-type=FAIL"):
        assert expected in directives
    config = _config(project, allocation_cpus=56, allocation_mem_gb=200, allocation_time_min=4320,
                     slurm_no_account=True)
    directives = allocation_directives(config, "bulkseq-x")
    assert "--cpus-per-task=56" in directives and "--mem=200G" in directives and "--time=3-00:00:00" in directives
    assert not any(d.startswith("--account") for d in directives) and "--exclusive" not in directives


def test_allocation_script_leaves_online_steps_for_the_login_node_when_offline(project) -> None:
    text = render_allocation_script(project, _config(project), python="/opt/py/bin/python")
    assert '--stage "${1:-compute}"' in text and "bulkseq run -C" in text and "--stage online" in text
    assert "SLURM_CPUS_PER_TASK" in text and "--memory-gb \"$MEM_GB\"" in text
    online = render_allocation_script(project, _config(project, compute_nodes_have_internet=True))
    assert '--stage "${1:-all}"' in online


def test_allocation_script_can_be_submitted_from_anywhere_and_resumes(project) -> None:
    text = render_allocation_script(project, _config(project))
    root = project.resolve()
    assert f"#SBATCH --chdir={root}" in text
    assert f"#SBATCH --output={root / 'logs' / 'slurm' / 'allocation-%j.log'}" in text
    # Resume mode: a resubmission after a walltime kill unlocks the project and repeats incomplete steps.
    assert "--exec-profile local --mode resume" in text


def test_allocation_script_can_run_inside_a_container(project) -> None:
    config = _config(project, container_image="/images/bulkseq.sif", container_runtime="singularity",
                     tmpdir="/scratch/tmp", container_binds=["/data/refs"])
    text = render_allocation_script(project, config)
    assert "singularity exec" in text and "/images/bulkseq.sif python -m app.cli run" in text
    assert "--bind /scratch/tmp" in text and "--bind /data/refs" in text and "export PYTHONPATH" not in text
    # The finishing hint goes through the image too, since the host has no bulkseq.
    assert "singularity run /images/bulkseq.sif run -C" in text


def test_container_binds_cover_the_inputs_the_project_names(project) -> None:
    manager = ProjectManager()
    config = manager.load_config(project)
    config.reference.genome_fasta = "/refs/wheat/genome.fa"
    config.reference.star_index = "/refs/wheat/star"
    config.execution.container_binds = ["/scratch/extra", "/refs/wheat/sub"]
    (project / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\tlayout\tfastq_1\tfastq_2\n"
        "a\tc\tsingle\t/data/reads/a.fq.gz\t\nb\tt\tsingle\t/data/reads/b.fq.gz\t\n", encoding="utf-8")
    binds = container_binds(project, config)
    assert str(project.resolve()) in binds and "/refs/wheat" in binds and "/data/reads" in binds
    assert "/scratch/extra" in binds and "/refs/wheat/sub" not in binds  # inside a kept folder


def test_slurm_script_command_writes_the_script(project, capsys) -> None:
    assert main(["slurm-script", "-C", str(project)]) == EXIT_OK
    script = project / ALLOCATION_SCRIPT
    assert script.read_text(encoding="utf-8").startswith("#!/bin/bash\n#SBATCH ")
    assert b"\r\n" not in script.read_bytes()
    # Slurm never creates the directory of --output; the command does.
    assert (project / "logs" / "slurm").is_dir()


# ------------------------------------------------------------------ config editing

def test_config_set_creates_a_per_rule_placement(project, capsys) -> None:
    assert main(["config", "set", "-C", str(project), "execution.rule_resources.star_index.slurm_partition",
                 "bigmem"]) == EXIT_OK
    config = ProjectManager().load_config(project)
    assert config.execution.rule_resources["star_index"].slurm_partition == "bigmem"
    assert main(["config", "set", "-C", str(project), "execution.rule_resources",
                 '{"star_align": {"qos": "long"}}']) == EXIT_OK
    config = ProjectManager().load_config(project)
    assert list(config.execution.rule_resources) == ["star_align"]


def test_config_set_validates_per_rule_values(project, capsys) -> None:
    assert main(["config", "set", "-C", str(project), "execution.rule_resources.star_index.qos", "a;b"]) == 3
    assert ProjectManager().load_config(project).execution.rule_resources == {}
    # A whole new entry given as JSON, and a malformed value, are both handled without a traceback.
    assert main(["config", "set", "-C", str(project), "execution.rule_resources.star_align", '{"qos": "long"}']) == EXIT_OK
    assert ProjectManager().load_config(project).execution.rule_resources["star_align"].qos == "long"
    assert main(["config", "set", "-C", str(project), "execution.rule_resources", "{not: [valid"]) == 3


def test_no_placeholder_survives_in_the_release_text() -> None:
    for path in (REPO / "CHANGELOG.md", REPO / "README.md", REPO / "docs" / "cluster.html",
                 REPO / "docs" / "cluster-reference.html"):
        assert not re.search(r"__[A-Z_]{3,}__", path.read_text(encoding="utf-8")), path


def test_scheduling_only_saves_keep_the_config_mtime(project) -> None:
    manager = ProjectManager()
    path = project / "config" / "config.yaml"
    old = time.time() - 3600
    os.utime(path, (old, old))
    config = manager.load_config(project)
    config.rule_memory_gb.star_align = 64
    config.rule_runtime_min.star_index = 2000
    config.execution.slurm_partition = "compute"
    manager.save_config(project, config)
    assert manager.load_config(project).rule_memory_gb.star_align == 64
    assert abs(path.stat().st_mtime - old) < 1
    config = manager.load_config(project)
    config.deseq2.alpha = 0.01
    manager.save_config(project, config)
    assert path.stat().st_mtime > old + 1800


def test_a_setting_added_by_an_update_is_not_an_analysis_change(project) -> None:
    # A file written by an older release lacks this release's new keys; saving it again (as
    # the interface does) fills them at their defaults, which must not replan a finished study.
    manager = ProjectManager()
    path = project / "config" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["reference"]["bam_index"]
    del data["rule_runtime_min"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))
    config = manager.load_config(project)
    config.execution.slurm_partition = "compute"
    manager.save_config(project, config)
    assert "bam_index" in path.read_text(encoding="utf-8")
    assert abs(path.stat().st_mtime - old) < 1


def test_every_execution_setting_is_described_once() -> None:
    fields = set(ExecutionConfig.model_fields) - {"rule_resources"}
    assert sorted(documented_fields()) == sorted(fields)


def test_every_execution_setting_is_documented_on_the_cluster_page() -> None:
    import json
    # Settings are listed on the reference page; the guide links to them.
    page = (REPO / "docs" / "cluster-reference.html").read_text(encoding="utf-8")
    for name in (*ExecutionConfig.model_fields, *RuleRuntimeMin.model_fields):
        assert f"<code>{name}</code>" in page or f"execution.{name}." in page, name
    # The page is built from the same descriptions the interface shows.
    from app.core.cluster_settings import EXECUTION_GROUPS
    exported = json.loads((REPO / "docs_src" / "cluster-settings.json").read_text(encoding="utf-8"))
    assert [(g["title"], [(s["key"], s["label"], s["help"]) for s in g["settings"]]) for g in exported] ==         [(title, list(rows)) for title, rows in EXECUTION_GROUPS]
    defaults = ExecutionConfig().model_dump(mode="json")
    assert all(s["default"] == defaults[s["key"]] for g in exported for s in g["settings"])


def test_default_config_carries_every_execution_setting() -> None:
    defaults = yaml.safe_load((REPO / "app" / "data" / "default_config.yaml").read_text(encoding="utf-8"))
    assert defaults["execution"] == ExecutionConfig().model_dump(mode="json")
    assert defaults["rule_runtime_min"] == RuleRuntimeMin().model_dump()


# ------------------------------------------------------------------ launch checks

def test_slurm_time_limits_parse() -> None:
    assert _slurm_minutes("7-00:00:00") == 10080
    assert _slurm_minutes("4:00:00") == 240
    assert _slurm_minutes("30:00") == 30
    assert _slurm_minutes("infinite") is None


def test_requests_beyond_the_partition_are_warned_by_rule(project, monkeypatch) -> None:
    config = _config(project, slurm_partition="compute", slurm_account="p")
    config.rule_memory_gb.star_index = 300
    config.rule_runtime_min.star_index = 20000
    monkeypatch.setattr(snakemake_runner, "slurm_partition_limits",
                        lambda partition, env, path: {"time_min": 10080, "cpus": 128, "mem_mb": 256000})
    monkeypatch.setattr(snakemake_runner.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(snakemake_runner, "_snakemake_interpreter", lambda s, p: None)
    _, warnings = cluster_launch_problems(config, "slurm", "dry-run", {"PATH": "/usr/bin"})
    text = "\n".join(warnings)
    assert "star_index asks for 300000 MB" in text and "star_index asks for 20000 min" in text
    assert "max_runtime_min" in text
    # A shared step key is judged for each rule that reads it, under the rule's own name.
    config.rule_memory_gb.deseq2 = 400
    _, warnings = cluster_launch_problems(config, "slurm", "dry-run", {"PATH": "/usr/bin"})
    assert any(w.startswith("limma_de asks for 400000 MB (rule_memory_gb.deseq2)") for w in warnings)


# ------------------------------------------------------------------ workflow invariants

OLD_STAR_INDEX_SHELL = r"""
            mkdir -p {output}
            GLEN=$(grep -v '^>' {input.fa} | tr -d '\n' | wc -c)
            NBASES=$(python -c "import math,sys; print(min(14, int(math.log2(int(sys.argv[1]))/2 - 1)))" $GLEN)
            RLEN=$(cat {input.rl}); OH=$((RLEN-1))
            echo "genome_length=$GLEN genomeSAindexNbases=$NBASES sjdbOverhang=$OH" > {log}
            STAR --runMode genomeGenerate --genomeDir {output} \
                 --genomeFastaFiles {input.fa} --sjdbGTFfile {input.gtf} \
                 --sjdbOverhang $OH --genomeSAindexNbases $NBASES \
                 --runThreadN {threads} >> {log} 2>&1
            """


def _shell_expression(rule_file: str, rule: str) -> str:
    text = (WORKFLOW / "rules" / rule_file).read_text(encoding="utf-8")
    body = text[text.index(f"rule {rule}:"):]
    body = body[body.index("shell:") + len("shell:"):]
    end = re.search(r"\n\s*\n\s*(?:#|rule |if |def |_)", body)
    return (body[:end.start()] if end else body).strip()


def test_star_index_command_is_unchanged_below_star_default_limit() -> None:
    # Snakemake records the shell text and reruns a rule whose text changed; existing projects
    # must not rebuild their index because of the large-genome fix.
    expression = _shell_expression("reference.smk", "star_index")
    assert eval(expression, {"_STAR_INDEX_RAM_FLAG": ""}) == OLD_STAR_INDEX_SHELL
    raised = eval(expression, {"_STAR_INDEX_RAM_FLAG": " --limitGenomeGenerateRAM 90000000000"})
    assert "--runThreadN {threads} --limitGenomeGenerateRAM 90000000000 >> {log}" in raised


def test_bam_index_command_is_unchanged_unless_csi_is_needed() -> None:
    expression = _shell_expression("alignment.smk", "samtools_index")
    assert eval(expression, {"BAM_INDEX_CSI": False}) == (
        "samtools index {input.bam} && samtools flagstat {input.bam} > {output.flagstat}")
    assert "samtools index -c -o {output.bai:q}" in eval(expression, {"BAM_INDEX_CSI": True})


def test_heavy_rules_declare_their_walltime_from_the_config() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in (WORKFLOW / "rules").glob("*.smk"))
    declared = dict(re.findall(r'rule_runtime_min\("(\w+)", (\d+)\)', text))
    assert declared == {name: str(value) for name, value in RuleRuntimeMin().model_dump().items()}


def test_study_wide_rules_do_not_size_memory_from_every_sample() -> None:
    reference = (WORKFLOW / "rules" / "reference.smk").read_text(encoding="utf-8")
    read_length = reference[reference.index("rule read_length:"):reference.index("rule read_length:") + 600]
    assert "mem_mb=2000" in read_length
    alignment = (WORKFLOW / "rules" / "alignment.smk").read_text(encoding="utf-8")
    infer = alignment[alignment.index("rule infer_strandedness:"):]
    assert 'mem_mb=rule_mem_mb("featurecounts", 8)' in infer[:1500]


def test_lock_installs_on_glibc_2_28_hosts() -> None:
    lock = (WORKFLOW / "envs" / "bulkseq.lock.yaml").read_text(encoding="utf-8")
    sysroot = re.search(r"sysroot_linux-64==([\d.]+)=", lock).group(1)
    assert tuple(int(part) for part in sysroot.split(".")) <= (2, 28)
