"""Plain-language descriptions of every cluster setting (config `execution` section).

One table serves the interface's Cluster panel, `bulkseq config` users and the documentation
test that requires each setting to appear on the cluster page.
"""

from __future__ import annotations

# (group title, [(field, label, help)]). Every ExecutionConfig field except rule_resources,
# which the panel edits as a per-step table, appears exactly once.
EXECUTION_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Where jobs go", [
        ("slurm_partition", "Partition",
         "Slurm partition (queue) for every job, or a comma-separated list Slurm may choose from. "
         "Empty uses the cluster's default. List them with `sinfo -s`."),
        ("slurm_account", "Account",
         "Account (project) charged for the jobs, passed as -A. Most centres require it; "
         "`sacctmgr show assoc user=$USER format=account`, or the centre's own command, lists yours."),
        ("slurm_no_account", "Submit without an account",
         "Passes --slurm-no-account for clusters that have no accounts. The 2.8.0 executor plugin defines the "
         "flag but does not yet act on it: with no account it still tries to guess one and submits without -A "
         "when that fails."),
        ("slurm_qos", "Quality of service", "QOS name passed as --qos, where the site requires one."),
        ("slurm_constraint", "Node features",
         "Feature expression passed as --constraint, e.g. a node type on a partition that mixes several."),
        ("slurm_clusters", "Cluster", "For federated sites: the cluster name passed as --clusters."),
        ("slurm_reservation", "Reservation", "An advance reservation the jobs should run in."),
        ("slurm_exclude_nodes", "Exclude nodes", "Nodes never to use, e.g. a017,a[040-042]."),
        ("slurm_extra", "Other sbatch options",
         "Further options such as --nice=10 or --mail-type=FAIL. Options the settings above or the executor "
         "manage (account, partition, QOS, memory, CPUs, time, output) are refused here."),
    ]),
    ("Job sizes and limits", [
        ("jobs", "Jobs in the queue at once",
         "Maximum number of jobs submitted at the same time. Keep it below the site's per-user limit."),
        ("min_cpus_per_job", "Minimum CPUs per job",
         "Smallest CPU request of any job (0 = each step's own thread count). Set it to the partition's "
         "minimum where the centre sets one."),
        ("default_runtime_min", "Default walltime (min)",
         "Walltime of a step with no walltime of its own (see the per-step table)."),
        ("default_mem_mb", "Default memory floor (MB)",
         "Memory of a step that declares none: the larger of this and twice the step's input size."),
        ("retries", "Retries",
         "Times a failed job is submitted again. A retry asks for the attempt number times the walltime "
         "(the step's own or the default one), capped by the walltime cap when set."),
        ("max_runtime_min", "Walltime cap (min)",
         "Upper bound for a retried job's walltime (0 = no cap). Set it to the partition's time limit."),
        ("max_mem_mb", "Memory cap for retries (MB)",
         "When set, a retried job also asks for more memory, up to this value (e.g. the node's memory). "
         "0 keeps the memory of a retry unchanged."),
        ("tmpdir", "Temporary directory",
         "Absolute directory for each job's temporary files (STAR and samtools sort spill). Empty keeps the "
         "node's own. Quotes and '$' are not accepted, so a variable cannot be used here."),
    ]),
    ("Executor behaviour", [
        ("slurm_requeue", "Requeue",
         "site-default leaves it to the cluster; requeue lets Slurm restart a job after a node failure or "
         "pre-emption; no-requeue forbids it."),
        ("slurm_status_command", "Job-state command",
         "auto uses sacct when present. Choose squeue on a cluster without job accounting."),
        ("slurm_array_jobs", "Submit as job arrays",
         "Empty, all, or comma-separated step or rule names whose jobs are grouped into Slurm job arrays "
         "(a step name such as fastp covers every rule that reads its settings)."),
        ("slurm_array_limit", "Array size limit", "Largest array per submission (0 = the executor's default)."),
        ("slurm_jobname_prefix", "Job-name prefix", "Prefix shown in squeue (letters, digits, _ and -)."),
        ("slurm_pass_command_as_script", "Pass commands as scripts",
         "Feeds each job's command to sbatch on standard input, for sites with a short command-line limit."),
        ("slurm_efficiency_report", "Efficiency report",
         "Writes logs/slurm/efficiency_report_<run id>.csv with each job's CPU and memory use at the end of "
         "the run."),
        ("slurm_partition_config", "Partition limits file",
         "Optional YAML file (path relative to the project) describing partition limits, so the executor "
         "chooses a partition for each job by its size."),
        ("latency_wait_s", "Shared-filesystem wait (s)",
         "How long to wait for a finished job's output to appear on the shared filesystem."),
        ("local_cores", "Login-node cores",
         "Cores the steps that run on the submitting host may use together (downloads, enrichment)."),
    ]),
    ("Internet access", [
        ("network_rules_on_submit_host", "Run online steps on the login node",
         "Downloads, GEO retrieval, enrichment, STRING and the MSigDB Hallmark set run where the run was "
         "started, because compute nodes often have no internet."),
        ("compute_nodes_have_internet", "Compute nodes have internet",
         "Single-allocation mode only: when false, the job leaves the online steps, and the figures, check "
         "summary and reports that depend on them, for a finishing run on the login node."),
    ]),
    ("Single-allocation mode (bulkseq slurm-script)", [
        ("allocation_cpus", "CPUs", "CPUs of the one job (0 = the whole node, --exclusive)."),
        ("allocation_mem_gb", "Memory (GB)", "Memory of the one job (0 = all of the node's memory)."),
        ("allocation_time_min", "Walltime (min)",
         "Walltime of the one job. A run that needs longer is continued by submitting the script again."),
        ("container_image", "Container image",
         "Apptainer/Singularity image (.sif) to run the workflow in, for sites that forbid conda installs. "
         "Empty runs the installed environment."),
        ("container_runtime", "Container command", "apptainer or singularity, whichever the cluster provides."),
        ("container_binds", "Container bind paths",
         "Further directories the container must see, comma-separated absolute paths. The project folder, "
         "the temporary directory and the folders of the configured reference and FASTQ files are bound "
         "already."),
    ]),
]

# Per-step scheduler placement (execution.rule_resources.<step>.<field>).
RULE_PLACEMENT_FIELDS: list[tuple[str, str]] = [
    ("slurm_partition", "Partition"),
    ("slurm_account", "Account"),
    ("qos", "QOS"),
    ("constraint", "Node features"),
    ("clusters", "Cluster"),
    ("slurm_extra", "Other sbatch options"),
]


def documented_fields() -> list[str]:
    return [field for _, rows in EXECUTION_GROUPS for field, _, _ in rows]
