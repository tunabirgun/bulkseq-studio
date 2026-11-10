import { readFileSync } from 'node:fs';

// Every `execution` setting with its label, help text and default, exported from
// app/core/cluster_settings.py (tests/test_cluster_hardening.py keeps the two identical).
const groups = JSON.parse(readFileSync(new URL('./cluster-settings.json', import.meta.url), 'utf8'));

const shown = value => value === '' ? '<em>empty</em>' : `<code>${typeof value === 'object' ? JSON.stringify(value) : value}</code>`;
const md = text => text.replace(/`([^`]+)`/g, '<code>$1</code>');

export function clusterPage({ step, grid, check, caution, note, warning, added }) {
  const settings = groups.map(group => `<h3 id="settings-${group.title.toLowerCase().replace(/[^a-z]+/g, '-').replace(/-$/, '')}">${group.title}</h3>`
    + grid(['Setting (<code>execution.…</code>)', 'Default', 'What it does'],
      group.settings.map(s => [`<code>${s.key}</code><br>${s.label}`, shown(s.default), md(s.help)]))
      .replace('<table>', '<table class="setting-table">')).join('');

  const body = `<p>Use a Slurm cluster when a study is too large for one machine. BulkSeq offers two ways to run there, and both use the same workflow, parameters and outputs as a local run; only where each step executes changes. Everything is driven from the cluster’s Linux login node with the <code>bulkseq</code> command; the desktop interface prepares the project and its settings.</p>`
  + added('0.37.0', 'Single-allocation mode, every Slurm executor option as a project setting, per-step walltimes and placement, split download and compute stages, an Apptainer image definition, launch checks against the partition’s limits, and site recipes for TRUBA and İTÜ UHeM.')
  + step('modes', '1. Choose how to use the scheduler', 'Per-step jobs suit clusters that bill the cores a job asks for. Where a centre allocates or bills whole nodes, or refuses small jobs, run the whole workflow in one allocation instead.',
    warning('On a centre that bills whole nodes, per-step jobs charge every one-thread step as a full node. UHeM’s Altay documentation states that a one-core job occupies the 128-core node and is billed accordingly, with a minimum charge of 64 CPU-hours. A study of a few dozen steps per sample can then cost many times the work it does.'),
    check('Ask your centre how jobs are charged (requested cores, used cores or whole nodes) and whether partitions set a minimum core count.'))
  + grid(['', 'Per-step jobs', 'One allocation'], [
    ['Command', '<code>bulkseq run --exec-profile slurm</code>', '<code>bulkseq slurm-script --submit</code>'],
    ['How it runs', 'Snakemake stays on the login node and submits each step as its own job with <code>sbatch</code>.', 'One batch job takes one node and runs the workflow there with the local executor, sized from the allocation.'],
    ['Suits', 'Per-core accounting; studies larger than one node; many samples in parallel across nodes.', 'Whole-node billing (UHeM Altay), partitions with a core minimum (TRUBA), a login node that may not host a multi-day process.'],
    ['Internet', 'Online steps run on the login node.', 'Downloads run on the login node first; online analysis steps run there afterwards unless compute nodes have internet.'],
    ['Limit', 'The login node must keep Snakemake running for the whole study.', 'The study must fit one node’s memory and the partition’s walltime; a longer run is continued by submitting again.'],
  ])
  + step('requirements', '2. Check what the cluster must provide', 'Confirm each item with your site before installing anything.',
    check('Ask the support desk whether a process may run on a login node for the length of a study, and where a project of this size should live.'))
  + grid(['Requirement', 'Why BulkSeq needs it', 'How to check'], [
    ['A Linux login node with <code>sbatch</code>, <code>squeue</code> and, ideally, <code>sacct</code>', 'Jobs are submitted and followed from there. A Windows or macOS workstation cannot submit.', '<code>command -v sbatch squeue sacct</code>'],
    ['glibc 2.28 or newer on login and compute nodes', 'The pinned environment needs it (RHEL/Rocky 8 or newer). Older hosts get an unpinned environment, which is not the validated one.', '<code>ldd --version | head -1</code>'],
    ['A filesystem shared by login and compute nodes, at the same path', 'Every job reads the project folder and the environment by their login-node paths.', 'Ask the site; scratch and project filesystems usually qualify.'],
    ['Space for the environment and the project', 'The environment needs about 15 GB while installing and about 7.6 GB afterwards. A project holds the reads, the genome index and the alignments (section 6).', '<code>df -h</code>; UHeM: <code>projelerim</code>'],
    ['Outbound internet on the login node, or inputs staged in advance', 'Downloads, GEO retrieval, enrichment, STRING and the MSigDB Hallmark set need it.', '<code>curl -sI https://ftp.ebi.ac.uk</code>'],
    ['A partition whose nodes fit the largest step', 'STAR indexing and alignment ask for 24 GB each by default; large genomes need far more (section 6).', '<code>sinfo -o "%P %m %c %l"</code>'],
    ['A way to keep a session alive', 'Per-step mode needs Snakemake running on the login node.', '<code>tmux</code> or <code>screen</code>, where the site allows them'],
  ])
  + step('install', '3. Install on the login node', 'Clone the release you intend to cite and install the analysis environment with the setup script, which serves native Linux as well as WSL2. Point <code>MAMBA_ROOT_PREFIX</code> at storage with room for it when the home directory is small; the setup writes the same value into your shell start-up file, and the workflow reads it in every job. Then install the command into a virtual environment built on the environment’s own Python, which avoids the desktop interface’s packages.',
    check('The setup output’s <em>Python imports</em> line ends with <code>snakemake-executor-plugin-slurm=2.8.0</code>, and <code>bulkseq version</code> prints this release.'),
    note('Use the source checkout on a cluster, not the AppImage (it needs FUSE and a newer glibc) and not the desktop interface.'))
  + `<pre><code>git clone --branch v0.37.0 https://github.com/tunabirgun/bulkseq-studio.git
cd bulkseq-studio
export MAMBA_ROOT_PREFIX=/path/with/space/micromamba   # optional; default ~/micromamba
bash scripts/setup_wsl_bioenv.sh bulkseq full
"\${MAMBA_ROOT_PREFIX:-$HOME/micromamba}/envs/bulkseq/bin/python" -m venv --system-site-packages ~/bulkseq-cli
~/bulkseq-cli/bin/pip install --no-deps -e .
export PATH="$HOME/bulkseq-cli/bin:$PATH"
bulkseq version</code></pre>`
  + `<h3 id="container">Sites that forbid conda: the Apptainer image</h3><p>Where a centre forbids conda environments on its filesystems (TRUBA does on <code>/arf</code>), build one image on a machine where you have root and copy it over. The image holds the same pinned environment, installed by the same setup script, and the command line. Every <code>bulkseq</code> command then runs through the image: <code>apptainer run bulkseq.sif &lt;command&gt;</code>. The job script binds the project folder, the temporary directory and the folders of the reference and FASTQ files the project names into the container; add others with <code>execution.container_binds</code>. Apptainer binds only your home directory, <code>/tmp</code> and the current folder by default, so a login-node command that reads files elsewhere needs <code>--bind</code> or <code>APPTAINER_BINDPATH</code>.</p><pre><code>apptainer build bulkseq.sif packaging/apptainer/bulkseq.def     # on your own Linux machine
alias bulkseq="apptainer run --bind /arf/scratch /path/to/bulkseq.sif"
bulkseq config set -C PATH execution.container_image /path/to/bulkseq.sif
bulkseq slurm-script -C PATH                   # writes the job script
bulkseq run -C PATH --stage prefetch --cores 2  # downloads, on the login node
sbatch PATH/config/slurm_allocation.sbatch
bulkseq run -C PATH --cores 2                   # after the job: online steps, figures, reports</code></pre>`
  + note('Declare the software or the image in your project application where the centre requires it (TRUBA and UHeM both do).')
  + step('project', '4. Create or copy the project', 'Create the project on the cluster with <code>bulkseq project create</code> and <code>bulkseq config set</code>, or with <code>bulkseq-benchmark create</code> for a bundled dataset, or prepare it in the desktop interface and copy the folder. Paths in a copied project must exist on the cluster: edit the FASTQ paths in the sample sheet (<code>config/samples.tsv</code>) and re-point any custom reference with <code>bulkseq config set</code> before the first run. A structured value is given as YAML or JSON.',
    check('<code>bulkseq check -C PATH</code> exits 0 and <code>bulkseq run -C PATH --mode dry-run</code> lists the steps you expect.'))
  + `<pre><code>bulkseq project create --name wheat_drought --workdir /path/to/projects
bulkseq config set -C PATH reference.genome_fasta /path/to/genome.fa
bulkseq config set -C PATH deseq2.contrasts '[{"name": "drought_vs_control", "factor": "condition", "numerator": "drought", "denominator": "control"}]'
bulkseq-benchmark list                # bundled datasets
bulkseq-benchmark create --benchmark ID --workdir /path/to/projects</code></pre>`
  + step('settings', '5. Every cluster setting', 'All site settings live in the project’s <code>config/config.yaml</code>, under <code>execution</code>, never in the copied workflow: an edit under <code>workflow/</code> changes the recorded workflow digest and the project then refuses to run. Set them with <code>bulkseq config set -C PATH execution.&lt;setting&gt; &lt;value&gt;</code>, or in the desktop interface under Compute resources → Cluster (Slurm) settings. Put <code>--</code> before a value that starts with <code>--</code>.',
    note('A change that only affects scheduling (anything under <code>execution</code>, <code>resources</code>, <code>rule_threads</code>, <code>rule_memory_gb</code> or <code>rule_runtime_min</code>) keeps the configuration file’s timestamp, so Snakemake does not plan finished steps again, and a changed thread count, memory or walltime reruns nothing. The one exception is <code>rule_memory_gb.star_index</code> at 35 GB or more, which changes the STAR command (section 6) and rebuilds the index and every alignment once.'))
  + `<pre><code>bulkseq config set -C PATH execution.slurm_partition defq
bulkseq config set -C PATH execution.slurm_account myproject
bulkseq config set -C PATH execution.slurm_extra -- --mail-type=FAIL
bulkseq config show -C PATH --section execution</code></pre>`
  + settings
  + step('per-step', '6. Per-step threads, memory, walltime and placement', 'Each heavy step declares its own threads, memory and walltime; every executor reads them. Change one with <code>bulkseq config set -C PATH rule_memory_gb.star_index 160</code>, <code>rule_threads.star_align 16</code> or <code>rule_runtime_min.star_index 1440</code>. A step without a walltime gets <code>execution.default_runtime_min</code>; one without memory gets the larger of <code>execution.default_mem_mb</code> and twice its input size. To send one step to another partition, QOS or node type, set <code>execution.rule_resources.&lt;step&gt;.&lt;field&gt;</code> with field <code>slurm_partition</code>, <code>slurm_account</code>, <code>qos</code>, <code>constraint</code>, <code>clusters</code> or <code>slurm_extra</code>.',
    caution('Save Resources in the desktop interface resets per-step threads and memory to the computer it runs on. Set the cluster values afterwards, in the Cluster (Slurm) settings table or with <code>bulkseq config set</code>.'),
    check('A retried job asks for the attempt number times its walltime, and times its memory when <code>execution.max_mem_mb</code> is set. Set <code>max_runtime_min</code> and <code>max_mem_mb</code> to the partition’s limits so a retry stays schedulable.'))
  + grid(['Step', 'Default walltime (<code>rule_runtime_min</code>)', 'Default memory (<code>rule_memory_gb</code>)'], [
    ['<code>star_index</code>', '720 min', '24 GB'], ['<code>hisat2_index</code>', '720 min', '16 GB'], ['<code>salmon_index</code>', '480 min', '16 GB'],
    ['<code>star_align</code>', '360 min', '24 GB'], ['<code>hisat2_align</code>', '360 min', '8 GB'], ['<code>salmon_quant</code>', '240 min', '8 GB'],
    ['<code>sortmerna</code>', '360 min', '12 GB'], ['<code>featurecounts</code>', '240 min', '8 GB'], ['<code>deseq2</code>', '240 min', '12 GB'],
  ]).replace('<table>', '<table class="key-table">')
  + `<h3 id="large-genomes">Large genomes</h3><p>Maize (about 2.2 Gb) and bread wheat (about 14.5 Gb) need far more than the defaults. On the bundled reference catalogue, STAR’s index build for maize peaked at 57.9 GB on the reference PC; wheat could not be indexed in 64 GB. The STAR manual asks for at least 32 GB for the 3.1 Gb human genome, roughly 10 bytes per base, which suggests on the order of 150 GB for wheat; treat that as an estimate to confirm on a first run, not a measurement. BulkSeq passes STAR a genome-generation limit of 90% of <code>rule_memory_gb.star_index</code> whenever that is above STAR’s 31 GB default, that is from 35 GB, so raising the memory is enough. Because the flag changes the STAR command, a project that already holds an index built with 35 GB or more rebuilds it once after this release. Aligning to a wheat STAR index also holds the whole index in memory, one alignment at a time per node; Salmon and HISAT2 need much less. Bread wheat and barley chromosomes exceed the 2<sup>29</sup>−1 bp (about 537 Mb) a BAI index can address, so their BAMs are indexed as CSI automatically; another genome with such a sequence needs <code>reference.bam_index csi</code>, and the reference check stops the run with that instruction before alignment.</p>`
  + grid(['Genome', 'Suggested setting', 'Basis'], [
    ['Maize, STAR', '<code>rule_memory_gb.star_index 72</code>, <code>star_align 40</code>', 'Index peak measured at 57.9 GB on the reference PC under STAR’s default limit; 72 GB gives STAR a 64.8 GB limit with headroom. Alignment is estimated from the index size'],
    ['Wheat, STAR', '<code>rule_memory_gb.star_index 200</code>, <code>star_align 180</code>, walltimes of a day or more', 'Estimated from the STAR manual’s human figure; not measured'],
    ['Wheat, Salmon', 'Defaults, with more walltime for <code>salmon_index</code>', 'The route the benchmark plan uses for wheat on one PC'],
  ])
  + caution('Plan disk for the index too. A STAR index occupies several times the genome size on disk, with more while it is built; for wheat that is on the order of a hundred GB or more (an estimate). Check the quota first.')
  + step('per-step-run', '7. Run with one job per step', 'Start the run inside <code>tmux</code> or <code>screen</code> so it survives a disconnect. Before submitting, BulkSeq checks the scheduler commands, the executor plugin and the account, and compares every step’s threads, memory and walltime with the largest node and the time limit of its partition (from <code>sinfo</code>), naming each step that cannot fit. Steps that need the internet run on the login node with at most <code>execution.local_cores</code> cores; all other steps go to compute nodes.',
    check('Run a dry run first and read its warnings: <code>bulkseq run -C PATH --exec-profile slurm --mode dry-run</code>.'))
  + `<pre><code>tmux new -s bulkseq
bulkseq check -C PATH
bulkseq run -C PATH --exec-profile slurm --mode dry-run
bulkseq run -C PATH --exec-profile slurm
bulkseq print-command -C PATH --exec-profile slurm    # the exact Snakemake command, without running it</code></pre>`
  + step('allocation', '8. Run in one allocation', '<code>bulkseq slurm-script</code> writes <code>config/slurm_allocation.sbatch</code> from the project’s settings: one node, the whole node unless <code>allocation_cpus</code> and <code>allocation_mem_gb</code> say otherwise, <code>allocation_time_min</code> of walltime, and the partition, account, QOS, node features and extra options above. With <code>--submit</code> it first runs the download stage on the login node, then submits the script. Inside the job the workflow sizes itself from the allocation and runs in resume mode. When compute nodes have no internet it leaves out the online analysis steps (enrichment, annotation transfer, STRING, MSigDB) and everything that depends on them: their figures, the check summary and the reports.',
    check('When the job has ended, finish on the login node with <code>bulkseq run -C PATH --cores 2</code>, which runs only what is still missing: the online steps, then the figures, check summary and reports. Where the login node may not run the R steps, run <code>bulkseq run -C PATH --stage online --cores 2</code> there and submit the script once more as <code>sbatch config/slurm_allocation.sbatch all</code> for the rest.'),
    caution('A study longer than the partition’s walltime stops when the job does. Submit the script again: the job unlocks the project and resumes from the steps already finished.'))
  + `<pre><code>bulkseq config set -C PATH execution.slurm_partition defq
bulkseq config set -C PATH execution.slurm_account myproject
bulkseq config set -C PATH execution.allocation_time_min 10080
bulkseq slurm-script -C PATH --submit
squeue --me
tail -f PATH/logs/slurm/allocation-JOBID.log
bulkseq run -C PATH --cores 2          # afterwards, on the login node</code></pre>`
  + `<p>The stages can also be run by hand. <code>--stage prefetch</code> runs only the downloads; <code>--stage compute</code> runs everything except the online analysis steps and what depends on them; <code>--stage online</code> runs only those online steps; without <code>--stage</code> a run does whatever is still missing. <code>--cores</code> and <code>--memory-gb</code> size a local run for one launch without changing the project.</p><pre><code>bulkseq run -C PATH --stage prefetch --cores 2
bulkseq run -C PATH --stage compute --cores 128 --memory-gb 240
bulkseq run -C PATH --stage online --cores 2
bulkseq run -C PATH --cores 2</code></pre>`
  + step('interact', '9. Follow, inspect and control a run', 'Every job BulkSeq submits is an ordinary Slurm job, so the site’s own commands work on it. Per-step jobs are named by the run’s identifier and carry the step name in their comment; the one-allocation job is named <code>bulkseq-&lt;project&gt;</code>.',
    note('BulkSeq keeps every scheduler log. A cluster, split-stage or in-allocation run also mirrors its own output into <code>logs/bulkseq_run_&lt;time&gt;_&lt;profile&gt;_&lt;stage&gt;.log</code>, so the record survives a closed terminal.'))
  + grid(['To', 'Use'], [
    ['List your jobs and their states', '<code>squeue --me</code>; UHeM: <code>isler</code>'],
    ['See why a job is pending', '<code>squeue --me -o "%i %j %T %r"</code> (reasons such as <code>Resources</code>, <code>AssocGrpCPUMinutesLimit</code>)'],
    ['Inspect a job’s request and node', '<code>scontrol show job JOBID</code>'],
    ['Accounting after a job ends (state, time, peak memory)', '<code>sacct -j JOBID -o JobID,JobName%30,State,Elapsed,MaxRSS,ReqMem,AllocCPUS</code>'],
    ['Live memory of a running job', '<code>sstat -j JOBID.batch -o MaxRSS,AveCPU</code>'],
    ['Efficiency of finished jobs', '<code>seff JOBID</code> where installed, or <code>execution.slurm_efficiency_report true</code> for <code>logs/slurm/efficiency_report_&lt;run id&gt;.csv</code>'],
    ['Read what a job printed', '<code>logs/slurm/rule_&lt;step&gt;/[&lt;sample&gt;/]&lt;jobid&gt;.log</code>; one allocation: <code>logs/slurm/allocation-&lt;jobid&gt;.log</code>'],
    ['Read a tool’s own log', '<code>logs/&lt;step&gt;.log</code>, as in a local run'],
    ['Hold or release a queued job', '<code>scontrol hold JOBID</code> / <code>scontrol release JOBID</code>'],
    ['Cancel jobs', 'Ctrl-C in the running <code>bulkseq run</code> (it cancels every job it submitted), or <code>scancel JOBID</code> / <code>scancel --me</code>'],
    ['Debug one step interactively', '<code>srun -p PARTITION -A ACCOUNT -c 4 --mem 8G --pty bash</code>, then run the command from <code>bulkseq print-command</code> with <code>--until STEP</code>'],
    ['See partitions and their limits', '<code>sinfo -s</code>, <code>sinfo -o "%P %m %c %l"</code>; UHeM: <code>spart</code>'],
    ['See your accounts and quotas', '<code>sacctmgr show assoc user=$USER</code>; UHeM: <code>projelerim</code>'],
  ])
  + step('resume', '10. Disconnects, stopping and resuming', 'Closing the terminal, a dropped SSH session or <code>kill</code> stops a per-step run the same way Ctrl-C does: Snakemake cancels the jobs it submitted, so none is left running unattended. To survive a disconnect, run inside <code>tmux</code> or <code>screen</code>, or use one allocation. Resume with <code>--mode resume</code>, which unlocks the project and repeats incomplete steps. A failed job is submitted again <code>execution.retries</code> times with more walltime; a genuine tool error fails again and stops that branch while independent steps continue.',
    check('Before resuming, BulkSeq warns when you still have jobs in the queue. Cancel any that belong to the stopped run with <code>scancel</code>, or two jobs would write the same files.'),
    note('Updating BulkSeq part-way through a study re-synchronises the project’s workflow on its next run. Pin one release for a whole benchmark and record it.'))
  + `<pre><code>bulkseq run -C PATH --exec-profile slurm --mode resume</code></pre><p>The exit status is that of a local run: 0 on success, 2 for a usage error, 3 when the project, its configuration or this host cannot be used, 5 when the workflow fails and 130 when interrupted. <a href="codes.html#cli">Exit codes and statuses</a> lists each with what to do next.</p>`
  + `<h2 id="sites">11. Site recipes</h2><p>These recipes follow each centre’s public user documentation as it read in November 2026. Partition names, limits and policies change; confirm them with the commands shown and with the support desk.</p>`
  + `<h3 id="uhem">İTÜ UHeM (Altay)</h3><p>Altay’s CPU nodes (<code>defq</code>, 7 days; <code>bigjobq</code>, 3 days) have 128 cores and 256 GB each and are allocated and billed as whole nodes; the GPU partitions’ nodes have 512 GB or 1 TB but bill their GPUs too. An account (<code>-A</code>, a project from <code>projelerim</code>) is required. The login node may download (the wiki uses it for <code>wget</code> and <code>rclone</code>) but kills programs that use much CPU or memory. Compute-node internet is not documented, so BulkSeq assumes none. Use one allocation per study.</p><pre><code>bulkseq config set -C PATH execution.slurm_partition defq
bulkseq config set -C PATH execution.slurm_account PROJECT          # from projelerim
bulkseq config set -C PATH execution.allocation_time_min 10080       # defq: 7 days
bulkseq config set -C PATH execution.max_runtime_min 10080
bulkseq slurm-script -C PATH --submit
bulkseq run -C PATH --cores 2                                        # after the job, on altay</code></pre>`
  + check('Ask UHeM whether compute nodes reach the internet (then set <code>execution.compute_nodes_have_internet true</code>), how large <code>/tmp</code> is on <code>defq</code> nodes, and how long a light process may run on the login node.')
  + `<h3 id="truba">TRUBA (ARF)</h3><p>ARF partitions set a minimum core count per job (orfoz 56, hamsi 28, barbun 20; the 4-hour debug partition accepts 1) and tie memory to cores (for example at most about 2.3 GB per core on orfoz). The longest walltime is 3 days. Conda and pip installs on <code>/arf</code> are forbidden, so there is no <code>bulkseq</code> on the host: every command below runs through the Apptainer image (section 3). Scratch (<code>/arf/scratch/$USER</code>) is cleaned after at most 30 days; copy <code>results/</code>, <code>checks/</code>, <code>config/</code> and <code>logs/</code> to your home directory when a run ends. Heavy or long processes on the login nodes are not allowed.</p><pre><code>alias bulkseq="apptainer run --bind /arf/scratch /arf/home/$USER/bulkseq.sif"
bulkseq config set -C PATH execution.slurm_partition orfoz
bulkseq config set -C PATH execution.slurm_account ACCOUNT
bulkseq config set -C PATH execution.container_image /arf/home/$USER/bulkseq.sif
bulkseq config set -C PATH execution.allocation_cpus 112
bulkseq config set -C PATH execution.allocation_mem_gb 250
bulkseq config set -C PATH execution.allocation_time_min 4320
bulkseq slurm-script -C PATH
bulkseq run -C PATH --stage prefetch --cores 2       # downloads, on the login node
sbatch PATH/config/slurm_allocation.sbatch
bulkseq run -C PATH --cores 2                        # after the job: online steps, figures, reports</code></pre><p>For per-step jobs on TRUBA instead, set <code>execution.min_cpus_per_job</code> to the partition’s minimum (56 on orfoz); every job then reserves at least that many cores, which are charged whether a step uses them or not.</p>`
  + check('Ask TRUBA whether a micromamba environment counts as a forbidden conda install, whether compute nodes reach the internet, and whether core-hours are charged on requested or used cores.')
  + `<h2 id="troubleshooting">12. Troubleshooting</h2>`
  + grid(['What you see', 'Cause', 'What to do'], [
    ['<code>\'sbatch\' is not on PATH</code>, exit 3', 'The command runs on a workstation, or Slurm’s commands are not loaded.', 'Run it on the login node; load the site’s Slurm module if it has one.'],
    ['<code>executor plugin … cannot be imported</code>, exit 3', 'The environment predates 0.36.0 or is incomplete.', 'Rerun <code>bash scripts/setup_wsl_bioenv.sh bulkseq full</code> from this release’s checkout.'],
    ['The setup says it installs from the floating spec, not the lock', 'The host’s glibc is older than 2.28, or it is not linux-64.', 'Use the Apptainer image, whose environment is the pinned one.'],
    ['<code>Invalid account or account/partition combination</code>', 'The account is missing, wrong or closed.', 'Set <code>execution.slurm_account</code> (UHeM: a project marked open in <code>projelerim</code>).'],
    ['A launch warning names a step that asks for more than the partition’s nodes', 'That step’s memory, CPUs or walltime exceed the partition.', 'Lower it, or place the step elsewhere with <code>execution.rule_resources.&lt;step&gt;.slurm_partition</code>.'],
    ['Jobs stay <code>PENDING</code> with reason <code>Resources</code> or a memory limit', 'A job asks for more than any free node offers.', 'Lower <code>rule_memory_gb</code> or <code>rule_threads</code> for that step, or choose another partition.'],
    ['Jobs pend with <code>AssocGrpCPUMinutesLimit</code> or <code>AssocGrpCpuLimit</code>', 'The project’s core-hour quota or concurrent-core limit is reached.', 'Wait, lower <code>execution.jobs</code>, or ask the centre for more.'],
    ['A partition refuses jobs below a core count', 'The partition has a minimum per job (TRUBA).', 'Set <code>execution.min_cpus_per_job</code>, or use one allocation.'],
    ['A job ends <code>TIMEOUT</code>', 'The step needed longer than its walltime.', 'Raise <code>rule_runtime_min.&lt;step&gt;</code> or <code>execution.default_runtime_min</code>, then resume.'],
    ['A job ends <code>OUT_OF_MEMORY</code>', 'The step needed more than it requested.', 'Raise <code>rule_memory_gb.&lt;step&gt;</code>, or <code>execution.default_mem_mb</code> for a step without one, then resume.'],
    ['<code>limitGenomeGenerateRAM … is too small</code> from STAR', 'The index build needs more memory than the step reserves.', 'Raise <code>rule_memory_gb.star_index</code>; the STAR limit follows it.'],
    ['<code>samtools index</code> fails with a region too large for BAI', 'A chromosome exceeds 512 Mb and the genome was not recognised as long.', 'Set <code>reference.bam_index csi</code>.'],
    ['A download or enrichment step cannot connect', 'The host running it has no outbound access.', 'Keep <code>network_rules_on_submit_host</code> true and run from a login node with internet, or stage inputs in advance.'],
    ['Output files are reported missing after a job succeeded', 'The shared filesystem showed them late.', 'Raise <code>execution.latency_wait_s</code>.'],
    ['<code>slurm_extra may not set --qos</code>', 'The executor or a dedicated setting manages that option.', 'Use <code>execution.slurm_qos</code> (likewise <code>slurm_constraint</code>, <code>slurm_clusters</code>).'],
    ['The project refuses to run after an edit under <code>workflow/</code>', 'The recorded workflow digest no longer matches.', 'Restore the copied workflow and put the setting in <code>execution</code> instead.'],
  ])
  + `<h2 id="kubernetes">13. Kubernetes</h2><p>The <code>kubernetes</code> profile is a starting point, not a supported route. Its executor plugin is not part of the environment, and every pod would need a container image carrying the full tool stack. <code>bulkseq run --exec-profile kubernetes</code> exits 3 while the plugin is missing; it cannot detect a missing image. Read <code>workflow/profiles/site/kubernetes/config.yaml</code> before attempting it.</p>`
  + `<h2 id="verified">14. What has been verified</h2><p>Both modes were run on a single-node Slurm 23.11 test cluster with job accounting, under WSL2 (Ubuntu 24.04), on a synthetic 3-versus-3 paired-end project. Per-step mode submitted 53 jobs, all of which completed; <code>sacct</code> shows the configured partition, account, QOS, job-name prefix and the two-CPU minimum on every job, and the step placed through <code>rule_resources</code> carried its extra option. Single-allocation mode ran the whole offline part of the same project in one job and a login-node run finished the remainder; a script submitted from another directory logged into the project, a job stopped part-way resumed when submitted again, and the online stage followed by a job submitted with <code>all</code> finished a project. The count matrix and the DESeq2 table of the local, per-step and single-allocation runs are byte-identical. A hangup signal to a per-step run cancelled its 14 queued jobs. A project finished under 0.36.0, upgraded, saved by the interface and given new scheduling settings had nothing to rerun. The revised lock installed in that environment, changing only the two header packages, and solves in a dry run for glibc 2.28, 2.34 and 2.39. CSI indexing under the <code>.bai</code> name was checked with samtools 1.22.1 and pysam on a 600 Mb synthetic sequence. In 0.36.0 the same test cluster ran synthetic FASTQ and count-matrix projects in 54 and 16 jobs with results identical to local runs. No production cluster, including İTÜ UHeM and TRUBA, has been used: their behaviour here is taken from their public documentation. The Apptainer image is built and self-tested by the Environment CI workflow only, never run under a cluster’s Apptainer. Kubernetes execution has not been tested.</p>`
  + check('Before a full study, run a small project under your site’s policy and confirm in <code>sacct</code> and <code>logs/slurm/</code> that its jobs reached the scheduler and completed.');

  return {slug: 'cluster', title: 'Run on a Slurm cluster', group: 'Run and automate',
    summary: 'Run the workflow on Slurm, one job per step or in one allocation, with every site setting kept in the project.',
    body};
}
