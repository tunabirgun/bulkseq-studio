import { readFileSync } from 'node:fs';

// Every `execution` setting with its label, help text and default, exported from
// app/core/cluster_settings.py (tests/test_cluster_hardening.py keeps the two identical).
const groups = JSON.parse(readFileSync(new URL('./cluster-settings.json', import.meta.url), 'utf8'));

const shown = value => value === '' ? '<em>empty</em>' : `<code>${typeof value === 'object' ? JSON.stringify(value) : value}</code>`;
const md = text => text.replace(/`([^`]+)`/g, '<code>$1</code>');

export function clusterReferencePage({ step, grid, check, caution, note, warning }) {
  const settings = groups.map(group => `<h3 id="settings-${group.title.toLowerCase().replace(/[^a-z]+/g, '-').replace(/-$/, '')}">${group.title}</h3>`
    + grid(['Setting (<code>execution.…</code>)', 'Default', 'What it does'],
      group.settings.map(s => [`<code>${s.key}</code><br>${s.label}`, shown(s.default), md(s.help)]))
      .replace('<table>', '<table class="setting-table">')).join('');

  const contents = [
    ['settings', 'Every cluster setting'],
    ['per-step', 'Per-step threads, memory, walltime and placement'],
    ['large-genomes', 'Large genomes'],
    ['stages', 'Run stages'],
    ['commands', 'Follow, inspect and control a run'],
    ['resume', 'Disconnects, stopping and resuming'],
    ['troubleshooting', 'Troubleshooting'],
    ['kubernetes', 'Kubernetes'],
    ['verified', 'What has been verified'],
  ].map(([id, title]) => `<li><a href="#${id}">${title}</a></li>`).join('');

  const body = `<p>The guided walkthrough for running BulkSeq on a Slurm cluster is on <a href="cluster.html">Run on a Slurm cluster</a>. This page is for looking things up: every setting and its default, the resources each step asks for, the Slurm commands that work on a run, and what to do when something goes wrong.</p>`
  + `<ul class="page-contents">${contents}</ul>`
  + step('settings', 'Every cluster setting', 'Use this section to find a setting’s name, default and effect. All site settings live in the project’s <code>config/config.yaml</code>, under <code>execution</code>, never in the copied workflow: an edit under <code>workflow/</code> changes the recorded workflow digest and the project then refuses to run. Set them with <code>bulkseq config set -C PATH execution.&lt;setting&gt; &lt;value&gt;</code>, or in the desktop interface under Analysis setup, Compute resources, Cluster (Slurm) settings. A value that starts with <code>--</code>, such as <code>--mail-type=FAIL</code>, needs <code>--</code> in front of it so the command does not read it as an option. A structured value (a list or a mapping) is given as YAML or JSON in quotes. In the desktop interface, Save Cluster Settings checks these settings and the per-step table and writes them to the project; Copy Cluster Commands copies the login-node commands that check and run the project (<code>bulkseq check</code>, a dry run, <code>bulkseq run --exec-profile slurm</code> and <code>bulkseq slurm-script --submit</code>).',
    note('A change that only affects scheduling (anything under <code>execution</code>, <code>resources</code>, <code>rule_threads</code>, <code>rule_memory_gb</code> or <code>rule_runtime_min</code>) keeps the configuration file’s timestamp, so Snakemake does not plan finished steps again, and a changed thread count, memory or walltime reruns nothing. The one exception is <code>rule_memory_gb.star_index</code> at 35 GB or more, which changes the STAR command (see <a href="#large-genomes">Large genomes</a>) and rebuilds the index and every alignment once.'),
    check('<code>bulkseq config show -C PATH --section execution</code> prints the values the project now holds.'))
  + `<pre><code>bulkseq config set -C PATH execution.slurm_partition PARTITION
bulkseq config set -C PATH execution.slurm_account myproject
bulkseq config set -C PATH execution.slurm_extra -- --mail-type=FAIL
bulkseq config set -C PATH execution.rule_resources '{"star_align": {"slurm_partition": "bigmem"}}'
bulkseq config show -C PATH --section execution</code></pre>`
  + settings
  + step('per-step', 'Per-step threads, memory, walltime and placement', 'Use this section to size one step or send it to another partition. Each heavy step declares its own threads, memory and walltime; every executor reads them. Change one with <code>bulkseq config set -C PATH rule_memory_gb.star_index 160</code>, <code>rule_threads.star_align 16</code> or <code>rule_runtime_min.star_index 1440</code>. A step without a walltime gets <code>execution.default_runtime_min</code>; one without memory gets the larger of <code>execution.default_mem_mb</code> and twice its input size. To send one step to another partition, QOS or node type, set <code>execution.rule_resources.&lt;step&gt;.&lt;field&gt;</code> with field <code>slurm_partition</code>, <code>slurm_account</code>, <code>qos</code>, <code>constraint</code>, <code>clusters</code> or <code>slurm_extra</code>. Some step keys cover several workflow rules, so one setting sizes all of them: <code>fastqc</code> (raw and trimmed reports), <code>fastp</code> (also the Trim Galore and Trimmomatic rules, single-end and paired-end), <code>sortmerna</code> (also its index and the RiboDetector rules) and <code>deseq2</code> (also the limma, voom and edgeR differential-expression rules).',
    caution('Save Resources in the desktop interface resets per-step threads and memory to the computer it runs on. Set the cluster values afterwards, in the Cluster (Slurm) settings table or with <code>bulkseq config set</code>.'),
    check('A retried job asks for the attempt number times its walltime, and times its memory when <code>execution.max_mem_mb</code> is set. Set <code>max_runtime_min</code> and <code>max_mem_mb</code> to the partition’s limits so a retry stays schedulable.'))
  + grid(['Step', 'Default walltime (<code>rule_runtime_min</code>)', 'Default memory (<code>rule_memory_gb</code>)'], [
    ['<code>star_index</code>', '720 min', '24 GB'], ['<code>hisat2_index</code>', '720 min', '16 GB'], ['<code>salmon_index</code>', '480 min', '16 GB'],
    ['<code>star_align</code>', '360 min', '24 GB'], ['<code>hisat2_align</code>', '360 min', '8 GB'], ['<code>salmon_quant</code>', '240 min', '8 GB'],
    ['<code>sortmerna</code>', '360 min', '12 GB'], ['<code>featurecounts</code>', '240 min', '8 GB'], ['<code>deseq2</code>', '240 min', '12 GB'],
  ]).replace('<table>', '<table class="key-table">')
  + `<h2 id="large-genomes">Large genomes</h2><p>Use this section when the genome is larger than a typical mammalian one. Maize (about 2.2 Gb) and bread wheat (about 14.5 Gb) need far more than the defaults. On the bundled reference catalogue, STAR’s index build for maize peaked at 57.9 GB on the reference PC; wheat could not be indexed in 64 GB. The STAR manual asks for at least 32 GB for the 3.1 Gb human genome, roughly 10 bytes per base, which suggests on the order of 150 GB for wheat; treat that as an estimate to confirm on a first run, not a measurement. BulkSeq passes STAR a genome-generation limit of 90% of <code>rule_memory_gb.star_index</code> whenever that is above STAR’s 31 GB default, that is from 35 GB, so raising the memory is enough. Because the flag changes the STAR command, a project that already holds an index built with 35 GB or more rebuilds it once after this release. Aligning to a wheat STAR index also holds the whole index in memory, one alignment at a time per node; Salmon and HISAT2 need much less. Bread wheat and barley chromosomes exceed the 2<sup>29</sup>−1 bp (about 537 Mb) a BAI index can address, so their BAMs are indexed as CSI automatically; another genome with such a sequence needs <code>reference.bam_index csi</code>, and the reference check stops the run with that instruction before alignment.</p>`
  + grid(['Genome', 'Suggested setting', 'Basis'], [
    ['Maize, STAR', '<code>rule_memory_gb.star_index 72</code>, <code>star_align 40</code>', 'Index peak measured at 57.9 GB on the reference PC under STAR’s default limit; 72 GB gives STAR a 64.8 GB limit with headroom. Alignment is estimated from the index size'],
    ['Wheat, STAR', '<code>rule_memory_gb.star_index 200</code>, <code>star_align 180</code>, walltimes of a day or more', 'Estimated from the STAR manual’s human figure; not measured'],
    ['Wheat, Salmon', 'Defaults, with more walltime for <code>salmon_index</code>', 'The route the benchmark plan uses for wheat on one PC'],
  ])
  + caution('Plan disk for the index too. A STAR index occupies several times the genome size on disk, with more while it is built; for wheat that is on the order of a hundred GB or more (an estimate). Check the quota first.')
  + `<h2 id="stages">Run stages</h2><p>Use stages when only part of a run can happen on a given host, for example downloads on the login node and computation elsewhere. <code>--stage prefetch</code> runs only the downloads. <code>--stage compute</code> runs everything except the online analysis steps (enrichment, annotation transfer, STRING and MSigDB) and what depends on them: their figures, the check summary and the reports. <code>--stage online</code> runs only those online steps. <code>--stage all</code>, or no <code>--stage</code>, does whatever is still missing. A staged run uses the local executor, so give <code>--stage</code> only with the default <code>--exec-profile local</code>; with another profile the command stops with an error. <code>--cores</code> and <code>--memory-gb</code> size a local run for one launch without changing the project. Inside the single-allocation job the stage is the script’s first argument: <code>sbatch config/slurm_allocation.sbatch all</code> runs every step, including the online ones.</p><pre><code>bulkseq run -C PATH --stage prefetch --cores 2
bulkseq run -C PATH --stage compute --cores 128 --memory-gb 240
bulkseq run -C PATH --stage online --cores 2
bulkseq run -C PATH --cores 2</code></pre>`
  + step('commands', 'Follow, inspect and control a run', 'Use this section while a run is in progress or after it ended. Every job BulkSeq submits is an ordinary Slurm job, so the site’s own commands work on it. Per-step jobs are named by the run’s identifier and carry the step name in their comment; the one-allocation job is named <code>bulkseq-&lt;project&gt;</code>.',
    note('BulkSeq keeps every scheduler log. A cluster, split-stage or in-allocation run also mirrors its own output into <code>logs/bulkseq_run_&lt;time&gt;_&lt;profile&gt;_&lt;stage&gt;.log</code>, so the record survives a closed terminal.'))
  + grid(['To', 'Use'], [
    ['List your jobs and their states', '<code>squeue --me</code>'],
    ['See why a job is pending', '<code>squeue --me -o "%i %j %T %r"</code> (reasons such as <code>Resources</code>, <code>AssocGrpCPUMinutesLimit</code>)'],
    ['Inspect a job’s request and node', '<code>scontrol show job JOBID</code>'],
    ['Accounting after a job ends (state, time, peak memory)', '<code>sacct -j JOBID -o JobID,JobName%30,State,Elapsed,MaxRSS,ReqMem,AllocCPUS</code>'],
    ['Live memory of a running job', '<code>sstat -j JOBID.batch -o MaxRSS,AveCPU</code>'],
    ['Efficiency of finished jobs', '<code>seff JOBID</code> where installed, or <code>execution.slurm_efficiency_report true</code> for <code>logs/slurm/efficiency_report_&lt;run id&gt;.csv</code>'],
    ['Read what a job printed', '<code>logs/slurm/rule_&lt;step&gt;/[&lt;sample&gt;/]&lt;jobid&gt;.log</code>; one allocation: <code>logs/slurm/allocation-&lt;jobid&gt;.log</code>'],
    ['Read a tool’s own log', '<code>logs/&lt;step&gt;.log</code>, as in a local run'],
    ['Read the run log', '<code>logs/bulkseq_run_&lt;time&gt;_&lt;profile&gt;_&lt;stage&gt;.log</code>'],
    ['Hold or release a queued job', '<code>scontrol hold JOBID</code> / <code>scontrol release JOBID</code>'],
    ['Cancel jobs', 'Ctrl-C in the running <code>bulkseq run</code> (it cancels every job it submitted), or <code>scancel JOBID</code> / <code>scancel --me</code>'],
    ['Debug one step interactively', '<code>srun -p PARTITION -A ACCOUNT -c 4 --mem 8G --pty bash</code>, then run the command from <code>bulkseq print-command</code> with <code>--until STEP</code>'],
    ['See partitions and their limits', '<code>sinfo -s</code>, <code>sinfo -o "%P %m %c %l"</code>'],
    ['See your accounts and quotas', '<code>sacctmgr show assoc user=$USER</code>, or your centre’s own account command'],
  ])
  + step('resume', 'Disconnects, stopping and resuming', 'Use this section after a dropped connection, an interruption or a failed job. Closing the terminal, a dropped SSH session or <code>kill</code> stops a per-step run the same way Ctrl-C does: Snakemake cancels the jobs it submitted, so none is left running unattended. To survive a disconnect, run inside <code>tmux</code> or <code>screen</code>, or use one allocation. Resume with <code>--mode resume</code>, which unlocks the project and repeats incomplete steps. A failed job is submitted again <code>execution.retries</code> times with more walltime; a genuine tool error fails again and stops that branch while independent steps continue.',
    check('Before resuming, BulkSeq warns when you still have jobs in the queue. Cancel any that belong to the stopped run with <code>scancel</code>, or two jobs would write the same files.'),
    note('Updating BulkSeq part-way through a study re-synchronises the project’s workflow on its next run. Pin one release for a whole benchmark and record it.'))
  + `<pre><code>bulkseq run -C PATH --exec-profile slurm --mode resume</code></pre><p>The exit status is that of a local run: 0 on success, 2 for a usage error, 3 when the project, its configuration or this host cannot be used, 5 when the workflow fails and 130 when interrupted. <a href="codes.html#cli">Exit codes and statuses</a> lists each with what to do next.</p>`
  + `<h2 id="troubleshooting">Troubleshooting</h2><p>Use this table when a launch warning, a job state or an error message does not match what you expected. Find the message in the first column.</p>`
  + grid(['What you see', 'Cause', 'What to do'], [
    ['<code>\'sbatch\' is not on PATH</code>, exit 3', 'The command runs on a workstation, or Slurm’s commands are not loaded.', 'Run it on the login node; load the site’s Slurm module if it has one.'],
    ['<code>executor plugin … cannot be imported</code>, exit 3', 'The environment predates 0.36.0 or is incomplete.', 'Rerun <code>bash scripts/setup_wsl_bioenv.sh bulkseq full</code> from this release’s checkout.'],
    ['The setup says it installs from the floating spec, not the lock', 'The host’s glibc is older than 2.28, or it is not linux-64.', 'Use the Apptainer image, whose environment is the pinned one.'],
    ['<code>Invalid account or account/partition combination</code>', 'The account is missing, wrong or closed.', 'Set <code>execution.slurm_account</code> to an open project.'],
    ['A launch warning names a step that asks for more than the partition’s nodes', 'That step’s memory, CPUs or walltime exceed the partition.', 'Lower it, or place the step elsewhere with <code>execution.rule_resources.&lt;step&gt;.slurm_partition</code>.'],
    ['Jobs stay <code>PENDING</code> with reason <code>Resources</code> or a memory limit', 'A job asks for more than any free node offers.', 'Lower <code>rule_memory_gb</code> or <code>rule_threads</code> for that step, or choose another partition.'],
    ['Jobs pend with <code>AssocGrpCPUMinutesLimit</code> or <code>AssocGrpCpuLimit</code>', 'The project’s core-hour quota or concurrent-core limit is reached.', 'Wait, lower <code>execution.jobs</code>, or ask the centre for more.'],
    ['A partition refuses jobs below a core count', 'The partition has a minimum per job.', 'Set <code>execution.min_cpus_per_job</code>, or use one allocation.'],
    ['A job ends <code>TIMEOUT</code>', 'The step needed longer than its walltime.', 'Raise <code>rule_runtime_min.&lt;step&gt;</code> or <code>execution.default_runtime_min</code>, then resume.'],
    ['A job ends <code>OUT_OF_MEMORY</code>', 'The step needed more than it requested.', 'Raise <code>rule_memory_gb.&lt;step&gt;</code>, or <code>execution.default_mem_mb</code> for a step without one, then resume.'],
    ['<code>limitGenomeGenerateRAM … is too small</code> from STAR', 'The index build needs more memory than the step reserves.', 'Raise <code>rule_memory_gb.star_index</code>; the STAR limit follows it.'],
    ['<code>samtools index</code> fails with a region too large for BAI', 'A chromosome exceeds 512 Mb and the genome was not recognised as long.', 'Set <code>reference.bam_index csi</code>.'],
    ['A download or enrichment step cannot connect', 'The host running it has no outbound access.', 'Keep <code>network_rules_on_submit_host</code> true and run from a login node with internet, or stage inputs in advance.'],
    ['Output files are reported missing after a job succeeded', 'The shared filesystem showed them late.', 'Raise <code>execution.latency_wait_s</code>.'],
    ['<code>slurm_extra may not set --qos</code>', 'The executor or a dedicated setting manages that option.', 'Use <code>execution.slurm_qos</code> (likewise <code>slurm_constraint</code>, <code>slurm_clusters</code>).'],
    ['The project refuses to run after an edit under <code>workflow/</code>', 'The recorded workflow digest no longer matches.', 'Restore the copied workflow and put the setting in <code>execution</code> instead.'],
  ])
  + `<h2 id="kubernetes">Kubernetes</h2><p>The <code>kubernetes</code> profile is a starting point, not a supported route. Its executor plugin is not part of the environment, and every pod would need a container image carrying the full tool stack. <code>bulkseq run --exec-profile kubernetes</code> exits 3 while the plugin is missing; it cannot detect a missing image. Read <code>workflow/profiles/site/kubernetes/config.yaml</code> before attempting it.</p>`
  + `<h2 id="verified">What has been verified</h2><p>Use this section to see what the cluster support has and has not been tested against. Both modes were run on a single-node Slurm 23.11 test cluster with job accounting, under WSL2 (Ubuntu 24.04), on a synthetic 3-versus-3 paired-end project. Per-step mode submitted 53 jobs, all of which completed; <code>sacct</code> shows the configured partition, account, QOS, job-name prefix and the two-CPU minimum on every job, and the step placed through <code>rule_resources</code> carried its extra option. Single-allocation mode ran the whole offline part of the same project in one job and a login-node run finished the remainder; a script submitted from another directory logged into the project, a job stopped part-way resumed when submitted again, and the online stage followed by a job submitted with <code>all</code> finished a project. The count matrix and the DESeq2 table of the local, per-step and single-allocation runs are byte-identical. A hangup signal to a per-step run cancelled its 14 queued jobs. A project finished under 0.36.0, upgraded, saved by the interface and given new scheduling settings had nothing to rerun. The revised lock installed in that environment, changing only the two header packages, and solves in a dry run for glibc 2.28, 2.34 and 2.39. CSI indexing under the <code>.bai</code> name was checked with samtools 1.22.1 and pysam on a 600 Mb synthetic sequence. In 0.36.0 the same test cluster ran synthetic FASTQ and count-matrix projects in 54 and 16 jobs with results identical to local runs. No production cluster has been used. The Apptainer image is built and self-tested by the Environment CI workflow only, never run under a cluster’s Apptainer. Kubernetes execution has not been tested.</p>`
  + check('Before a full study, run a small project under your site’s policy and confirm in <code>sacct</code> and <code>logs/slurm/</code> that its jobs reached the scheduler and completed.');

  return {slug: 'cluster-reference', title: 'Cluster settings and commands', group: 'Reference',
    summary: 'Every cluster setting with its default, per-step resources, the Slurm commands that work on a BulkSeq run, and troubleshooting.',
    body};
}
