export function clusterPage({ step, grid, check, caution, note, warning, added, imageFigure }) {
  const ref = anchor => `cluster-reference.html#${anchor}`;
  const DIR = '~/projects/my_project';

  const body = `<p>This page shows how to run a BulkSeq study on a Slurm cluster, a shared computer system that runs jobs from a queue. It is written for researchers who have used the desktop app but never a cluster. It explains the few terms you need, walks through a complete first run, and lists the centre policies that change how you set it up. The workflow, parameters and outputs are those of a run on your own computer; only the machine that runs each step changes. Every setting, command and limit is listed on <a href="cluster-reference.html">Cluster settings and commands</a>.</p>`
  + added('0.37.0', 'Single-allocation mode, every Slurm option as a project setting, an Apptainer image and launch checks against partition limits.')

  + `<h2 id="concepts">What a cluster is</h2><p>A cluster is many computers (nodes) that share storage. You do not run heavy work yourself: you describe a job, and a scheduler called Slurm runs it on a free node when its turn comes.</p>`
  + grid(['Term', 'Meaning'], [
    ['Login node', 'The computer you connect to and type commands on. It is shared with other users, so it is for light work only: copying, downloading, starting jobs.'],
    ['Compute node', 'A computer that runs jobs. You do not log in to it; Slurm starts your job there.'],
    ['Slurm', 'The queue software. It decides which job runs where and when.'],
    ['Job and <code>sbatch</code>', 'A job is one request for CPUs, memory and time. <code>sbatch</code> is the command that hands a job script to the queue.'],
    ['Partition', 'A queue. Each partition has its own type of node and its own time limit.'],
    ['Walltime', 'The longest time a job may run. Slurm stops the job when it is reached.'],
    ['Account', 'The project charged for the CPU time your jobs use. Most centres require one.'],
    ['Terminal and SSH', 'A terminal is a window where you type commands. SSH connects that window to the login node, so the commands run there.'],
  ])
  + imageFigure('cluster-overview.svg', 'How a cluster run is organised',
    'A flow diagram in three boxes from left to right. Your computer, running the desktop app, prepares the project, sets the cluster settings and reads the results. An arrow labelled copy project leads to the cluster login node, which runs the bulkseq commands, the downloads, the online steps such as enrichment and STRING, and the finishing run. An arrow labelled sbatch leads to the compute nodes, which run trimming, alignment, counting, DESeq2 and figures. A return arrow labelled copy results back leads from the login node to your computer.',
    'A cluster run. You prepare the project on your computer and copy it to the login node, which submits the job. The compute node does the heavy steps. You copy the results back.')

  + `<h2 id="modes">Which way to run</h2><p>BulkSeq can use the scheduler in two ways. <strong>One allocation</strong> reserves one node and runs the whole study inside that single job. <strong>One job per step</strong> submits every workflow step as its own job. Answer two questions.</p><ol><li>Does your centre bill or allocate whole nodes, or refuse small jobs? Then use one allocation.</li><li>Is the study larger than one node’s memory or time limit, or does your centre bill per core? Then use one job per step.</li></ol>`
  + grid(['', 'One allocation', 'One job per step'], [
    ['Command', '<code>bulkseq slurm-script --submit</code>', '<code>bulkseq run --exec-profile slurm</code>'],
    ['Where Snakemake runs', 'Inside the job, on the compute node.', 'On the login node, for the whole study.'],
    ['Limit', 'The study must fit one node and one walltime. A longer run continues when you submit it again.', 'Your session on the login node must stay open.'],
  ])
  + warning('On a centre that bills whole nodes, one job per step charges every one-thread step as a full node, often with a minimum charge per job. A study of many small steps can then cost many times the work it does.')
  + `<p>This page follows the one-allocation route in detail. The other route is in <a href="#per-step-run">Run one job per step instead</a>.</p>`

  + `<h2 id="requirements">Before you start</h2><p>You need an account on the cluster, with the login details your centre sends when you register (often a VPN plus SSH). The cluster itself must offer the items below. If you are unsure, ask the support desk; you can also check them yourself after you first log in (Step 3), where each command prints a path or a version when the item is present.</p>`
  + grid(['The cluster must offer', 'Check on the login node'], [
    ['A Linux login node with the Slurm commands <code>sbatch</code> and <code>squeue</code>', '<code>command -v sbatch squeue sacct</code>'],
    ['A recent Linux system library (glibc 2.28 or later) on login and compute nodes', '<code>ldd --version | head -1</code>'],
    ['Storage that login and compute nodes share at the same path, for the project and the software', 'Ask the support desk; project and scratch storage usually qualify.'],
    ['About 15 GB of free space while the software installs (about 7.6 GB afterwards), plus space for the project', '<code>df -h ~</code>, or your centre’s quota command'],
    ['Internet on the login node, or the input files copied in beforehand', '<code>curl -sI https://ftp.ebi.ac.uk</code>'],
    ['A way to keep a session running after you disconnect', '<code>command -v tmux</code> (Step 3 shows how to use it)'],
  ])
  + check('A Windows or macOS computer cannot submit jobs itself. You prepare the project there, but the run is started from the cluster’s login node.')

  + `<h2 id="first-run">Your first cluster run, step by step</h2><p>The steps below use one allocation. Replace the words in capitals with your own values: <code>USER</code> is your user name on the cluster, <code>LOGIN_NODE</code> the login node’s address, <code>PARTITION</code> a CPU partition and <code>ACCOUNT</code> the project charged for the jobs. Your registration details give the first two; Step 3 shows how to look up the other two. The project folder on the cluster is <code>${DIR}</code>; use the storage your centre recommends for projects instead if it differs.</p>`
  + step('prepare', 'Step 1. Prepare the project on your computer', 'In the desktop app, create the project, add the samples and the comparison, and run the pre-run checks. Preparing it here lets you see the design in the interface before anything runs. A study whose reads are downloaded from ENA or SRA accessions copies easily. If the reads are FASTQ files on your computer, edit the paths in <code>config/samples.tsv</code> now so they point where you will copy the files on the cluster, for example <code>' + DIR + '/fastq/S1_R1.fastq.gz</code>.',
    check('The Pre-run checks page reports no failure.'),
    note('You can also create a project on the cluster with <code>bulkseq project create</code>, or a bundled example study with <code>bulkseq-benchmark create --benchmark ID --workdir DIR</code>.'))
  + step('settings', 'Step 2. Set the cluster settings', 'In the app, open Analysis setup, then Compute resources, and expand <em>Cluster (Slurm) settings</em> at the bottom. Under <em>Where jobs go</em>, set Partition and Account. Under <em>Single-allocation mode</em>, leave CPUs and Memory at 0, which means the whole node, and set Walltime (min) to the partition’s time limit, for example <code>10080</code> for 7 days. Under <em>Job sizes and limits</em>, set Walltime cap (min) to the same limit. Click <strong>Save Cluster Settings</strong>. If you do not know the partition or account yet, leave them empty and set them in Step 5.',
    check('No error message appears. The settings are stored in the project’s <code>config/config.yaml</code>.'),
    caution('Do not click <strong>Save Resources</strong> on the same page afterwards: it resets each step’s threads and memory to the limits of this computer. If you need it, click it first, then save the cluster settings.'))
  + step('connect', 'Step 3. Connect and install once', 'Start the VPN client your centre provides, if it uses one. Then open a terminal: on Windows, open PowerShell from the Start menu; on macOS or Linux, open Terminal. <code>ssh</code> and <code>scp</code> are built in. Type the <code>ssh</code> line below, answer <code>yes</code> the first time, and type your password (nothing is shown as you type). The prompt then shows the cluster’s name: every command you type now runs on the login node. Start <code>tmux</code> so a dropped connection does not stop the installation (after a drop, log in again and type <code>tmux attach -t bulkseq</code>), then install BulkSeq. The <code>echo</code> line makes the <code>bulkseq</code> command available at every later login. This is needed only once; later projects reuse it.',
    check('The installation ends with a <em>Python imports</em> line that includes <code>snakemake-executor-plugin-slurm=2.8.0</code>, and <code>bulkseq version</code> prints <code>BulkSeq Studio 0.37.0</code>.'),
    note('Type the lines one at a time; text after <code>#</code> is a comment. The setup script is the general Linux installer despite <code>wsl</code> in its name, and it downloads several GB. Set <code>MAMBA_ROOT_PREFIX</code>, the folder that holds the analysis software, only if your home folder has less than about 15 GB free. Use this source installation on a cluster, not the AppImage, which needs FUSE and a newer system library.'))
  + `<pre><code>ssh USER@LOGIN_NODE
tmux new -s bulkseq
git clone --branch v0.37.0 https://github.com/tunabirgun/bulkseq-studio.git
cd bulkseq-studio
export MAMBA_ROOT_PREFIX=/path/with/room/micromamba   # optional, see the note
bash scripts/setup_wsl_bioenv.sh bulkseq full
"\${MAMBA_ROOT_PREFIX:-$HOME/micromamba}/envs/bulkseq/bin/python" -m venv --system-site-packages ~/bulkseq-cli
~/bulkseq-cli/bin/pip install --no-deps -e .
echo 'export PATH="$HOME/bulkseq-cli/bin:$PATH"' &gt;&gt; ~/.bashrc
source ~/.bashrc
bulkseq version
sinfo -s                                    # partitions: choose PARTITION
sacctmgr show assoc user=$USER format=account   # accounts: choose ACCOUNT</code></pre>`
  + step('copy', 'Step 4. Copy the project to the cluster', 'Open a second terminal on your computer, not on the login node, and go to the folder that holds the project, for example <code>cd C:\\Users\\you\\BulkSeq</code>. Copy the project folder to the cluster, and the FASTQ files too if Step 1 pointed the sample sheet at them.',
    check('On the login node, <code>ls ' + DIR + '/config</code> lists <code>config.yaml</code> and <code>samples.tsv</code>.'),
    caution('Paths in the project that still point at your computer do not exist on the cluster. If you need to fix one there, <code>nano ' + DIR + '/config/samples.tsv</code> opens the sample sheet in a simple editor (Ctrl-O saves, Ctrl-X exits).'))
  + `<pre><code>ssh USER@LOGIN_NODE mkdir -p projects                   # once: create the folder
scp -r my_project USER@LOGIN_NODE:projects/             # copy the project
rsync -av my_project/ USER@LOGIN_NODE:projects/my_project/   # macOS, Linux or WSL: repeatable copy</code></pre>`
  + step('submit', 'Step 5. Check and submit', 'On the login node, inside <code>tmux</code>, check the project and submit it. <code>bulkseq slurm-script --submit</code> writes the job script <code>config/slurm_allocation.sbatch</code>, downloads the inputs on the login node, submits the script as one job that takes a whole node, and prints the job number. The downloads can take hours; if the connection drops, log in again and type <code>tmux attach -t bulkseq</code>. Once the job number is printed, you can log out: the job keeps running. The <code>config set</code> lines are needed only for values you did not save in Step 2.',
    check('<code>bulkseq check</code> reports no failure, and the submit command prints <code>Submitted job 123456. Follow it with: …</code>.'),
    caution('Nothing compares the one-allocation job with the partition before it is submitted. Check with <code>sinfo -o "%P %m %c %l"</code> (partition, memory per node in MB, CPUs per node, time limit) that the node and the walltime you chose fit.'))
  + `<pre><code>bulkseq config set -C ${DIR} execution.slurm_partition PARTITION
bulkseq config set -C ${DIR} execution.slurm_account ACCOUNT
bulkseq config set -C ${DIR} execution.allocation_time_min 10080
bulkseq config set -C ${DIR} execution.max_runtime_min 10080
bulkseq check -C ${DIR}
bulkseq slurm-script -C ${DIR} --submit</code></pre>`
  + step('follow', 'Step 6. Follow the job', 'List your jobs, then watch the job’s log. Replace <code>JOBID</code> with the number Step 5 printed; the submit message also prints this exact command. Press Ctrl-C to stop watching; the job keeps running, and you can log out and check again later.',
    check('<code>squeue --me</code> shows your job as <code>PD</code> (pending, waiting for a node) and then <code>R</code> (running). The log grows as steps finish. When the job no longer appears in the list, it has ended.'))
  + `<pre><code>squeue --me
tail -f ${DIR}/logs/slurm/allocation-JOBID.log</code></pre>`
  + step('finish', 'Step 7. Finish on the login node', 'When the job has ended, run the workflow once more on the login node. It runs only what is still missing. Compute nodes are assumed to have no internet, so the online steps (enrichment, annotation transfer, STRING, MSigDB) and the figures, check summary and reports that depend on them run here.',
    check('The command ends without an error and <code>results/reports</code> holds the report.'),
    note('If your centre stops programs that use much CPU or memory on the login node, run only the online steps there with <code>bulkseq run -C ' + DIR + ' --stage online --cores 2</code>, then submit the rest as a job with <code>sbatch ' + DIR + '/config/slurm_allocation.sbatch all</code>.'))
  + `<pre><code>bulkseq run -C ${DIR} --cores 2</code></pre>`
  + step('results', 'Step 8. Copy the results back and open them', 'In the terminal on your computer, copy back <code>results/</code>, <code>checks/</code>, <code>config/</code> and <code>logs/</code>. The rest of the project (downloaded reads, alignments, the genome index) is large and can be regenerated. Open the project in the desktop app.',
    check('The Figures and tables page and the Reports page show the results of the run.'))
  + `<pre><code>scp -r USER@LOGIN_NODE:projects/my_project/results my_project/   # repeat for checks, config and logs</code></pre>`

  + `<h2 id="gui">Cluster settings in the desktop app</h2><p>On the <em>Compute resources</em> page, under Analysis setup, a collapsed section called <em>Cluster (Slurm) settings</em> holds the settings for cluster runs. It stores them in the project’s <code>config/config.yaml</code>; it does not connect to a cluster or submit anything. A run on your own computer ignores these settings, except the per-step threads and memory in the table, which every run uses.</p>`
  + imageFigure('cluster-panel.png', 'Cluster (Slurm) settings panel',
    'Two groups of the Cluster (Slurm) settings section. Where jobs go shows Partition set to compute and Account set to myproject, with empty fields for quality of service, node features, cluster, reservation, excluded nodes and other sbatch options. Single-allocation mode shows CPUs 0, Memory 0 and Walltime 10080 minutes, with empty container fields.',
    'The two groups a first run needs: <em>Where jobs go</em> (partition and account) and <em>Single-allocation mode</em> (0 CPUs and 0 GB mean the whole node; walltime in minutes). The other three groups and the per-step table sit between and below them.')
  + `<p>The settings are in five groups. <em>Where jobs go</em> holds the partition, account and other placement options. <em>Job sizes and limits</em> holds default memory, walltime limits and retries. <em>Executor behaviour</em> holds fine-grained Slurm options. <em>Internet access</em> says whether compute nodes can reach the internet. <em>Single-allocation mode</em> holds the size of the one job and the container image. The table below them sets threads, memory and walltime for each step; a blank placement cell uses the run-wide value. Every setting is described in the <a href="${ref('settings')}">settings reference</a>.</p>`
  + `<ul><li><strong>Save Cluster Settings</strong> checks the values and writes them to the project.</li><li><strong>Copy Cluster Commands</strong> copies the login-node commands that check and run this project to the clipboard.</li></ul>`

  + `<h2 id="per-step-run">Run one job per step instead</h2><p>Use this on a centre that bills per core, or for a study too large for one node. Snakemake stays on the login node and submits every step as its own job, so start it inside <code>tmux</code>; if the session closes, the run stops and cancels its jobs. Before submitting, BulkSeq compares each step’s threads, memory and walltime with the partition and names any step that cannot fit. Below, <code>${DIR}</code> is the project folder as in the steps above.</p>`
  + `<pre><code>tmux new -s bulkseq
bulkseq check -C ${DIR}
bulkseq run -C ${DIR} --exec-profile slurm --mode dry-run     # read its warnings
bulkseq run -C ${DIR} --exec-profile slurm
bulkseq run -C ${DIR} --exec-profile slurm --mode resume      # after an interruption
bulkseq print-command -C ${DIR} --exec-profile slurm         # the exact Snakemake command, not run</code></pre>`
  + check('The dry run lists the steps you expect and no warning about partition limits. Detach from <code>tmux</code> with Ctrl-b then d, and return with <code>tmux attach -t bulkseq</code>.')
  + `<p>Per-step sizing, following and cancelling jobs, and resuming are described in the <a href="${ref('per-step')}">per-step reference</a>, the <a href="${ref('commands')}">command table</a> and <a href="${ref('resume')}">Disconnects, stopping and resuming</a>.</p>`

  + `<h2 id="help">If something goes wrong</h2>`
  + grid(['What you see', 'What to do'], [
    ['<code>bulkseq: command not found</code> after logging in again', 'Run <code>source ~/.bashrc</code>, or repeat the <code>echo … &gt;&gt; ~/.bashrc</code> line from Step 3.'],
    ['The job stays <code>PD</code> (pending)', 'Run <code>squeue --me -o "%i %j %T %r"</code> and read the reason. <code>Resources</code> or <code>Priority</code> means wait. A request larger than any node needs smaller values: <code>execution.allocation_cpus</code>, <code>allocation_mem_gb</code> or <code>allocation_time_min</code> for one allocation, or another partition.'],
    ['The job ends <code>TIMEOUT</code>', 'The study needed more than the walltime. Submit again with <code>bulkseq slurm-script -C ' + DIR + ' --submit</code>; it resumes from the finished steps. Raise <code>execution.allocation_time_min</code> if the partition allows it.'],
    ['The job ends <code>OUT_OF_MEMORY</code>', 'A step needed more memory than it requested. Raise that step’s memory, for example <code>bulkseq config set -C ' + DIR + ' rule_memory_gb.star_align 48</code> (the step name is in the log), then submit again.'],
    ['<code>Invalid account or account/partition combination</code>', 'Set <code>execution.slurm_account</code> to an open project; <code>sacctmgr show assoc user=$USER format=account</code> lists yours.'],
    ['Your connection dropped during a download or a per-step run', 'Log in again and type <code>tmux attach -t bulkseq</code>. Without <code>tmux</code>, a per-step run stops and cancels its jobs: start <code>tmux</code>, then run with <code>--mode resume</code>. A submitted allocation job is not affected.'],
    ['A download or enrichment step cannot connect', 'That step needs the internet. Run it from the login node, or copy the inputs in beforehand.'],
  ])
  + `<p>More problems and their fixes are in the <a href="${ref('troubleshooting')}">troubleshooting reference</a>. Large genomes such as maize and wheat need far more memory than the defaults; see <a href="${ref('large-genomes')}">Large genomes</a>.</p>`

  + `<h2 id="policies">Centre policies to check</h2><p>Clusters differ in how they charge for and limit jobs. Read your centre’s user guide for these points before the first run; each one maps to a setting.</p>`
  + grid(['If your centre…', 'Then'], [
    ['allocates or bills whole nodes, or charges a minimum per job', 'Use one allocation, so the study is charged as one job.'],
    ['sets a minimum number of cores per job on a partition', 'Use one allocation, or for one job per step set <code>execution.min_cpus_per_job</code> to that minimum. Every job then reserves at least that many cores, charged whether a step uses them or not.'],
    ['ties memory to cores (a maximum memory per core)', 'Request enough CPUs for the memory you need: <code>execution.allocation_cpus</code> with <code>allocation_mem_gb</code>, or per-step threads with memory.'],
    ['limits walltime per partition', 'Set <code>execution.allocation_time_min</code> and <code>execution.max_runtime_min</code> to that limit. A study that needs longer continues when you submit the job script again.'],
    ['forbids conda or pip installs on its filesystems', 'Use the Apptainer image described below.'],
    ['cleans scratch storage after a number of days', 'Copy <code>results/</code>, <code>checks/</code>, <code>config/</code> and <code>logs/</code> to permanent storage when a run ends.'],
    ['limits CPU or memory use on login nodes', 'Keep <code>execution.local_cores</code> low and use the online-only finishing route from Step 7.'],
    ['gives compute nodes internet access', 'Set <code>execution.compute_nodes_have_internet true</code>; the job then runs the online steps itself and no finishing run is needed.'],
    ['requires software to be declared in your project application', 'Declare the BulkSeq environment or the Apptainer image there.'],
  ])
  + `<h3 id="container">Sites that forbid conda: the Apptainer image</h3><p>Apptainer (formerly Singularity) is a container tool common on clusters. An image is one file that holds a complete software environment. Where a centre forbids conda environments on its filesystems, build the image on a Linux machine where you have administrator rights and copy it to the cluster. It holds the same pinned environment, installed by the same setup script, and the <code>bulkseq</code> command. Every <code>bulkseq</code> command then runs through the image, here through an alias. In the block below, <code>slurm-script</code> writes the job script, the <code>prefetch</code> stage downloads the inputs on the login node, <code>sbatch</code> submits the job, and the last line finishes the run after it. The image has no Slurm commands, so submit the job script with <code>sbatch</code> on the login node instead of <code>--submit</code>. The job script binds the project folder, the temporary folder and the folders of the reference and FASTQ files into the container; add others with <code>execution.container_binds</code>. On the login node, Apptainer sees only your home folder, <code>/tmp</code> and the current folder unless you add <code>--bind</code>.</p><pre><code>apptainer build bulkseq.sif packaging/apptainer/bulkseq.def   # on your Linux machine
scp bulkseq.sif USER@LOGIN_NODE:                               # to the cluster
alias bulkseq="apptainer run --bind /path/to/your/scratch $HOME/bulkseq.sif"
bulkseq config set -C ${DIR} execution.container_image $HOME/bulkseq.sif
bulkseq slurm-script -C ${DIR}
bulkseq run -C ${DIR} --stage prefetch --cores 2
sbatch ${DIR}/config/slurm_allocation.sbatch
bulkseq run -C ${DIR} --cores 2                                # after the job</code></pre>`

  + `<h2 id="tested">What has been tested</h2><p>Both modes were run on a single-node Slurm test cluster under WSL2 with synthetic projects, and their count matrices and differential-expression tables were byte-identical to a run on the same computer without Slurm. No production cluster has been used. Before a full study, run a small project under your centre’s policy. The details are in <a href="${ref('verified')}">What has been verified</a>.</p>`;

  return {slug: 'cluster', title: 'Run on a Slurm cluster', group: 'Run and automate',
    summary: 'A step-by-step guide to running a BulkSeq study on a Slurm cluster, for people who have not used one before.',
    body};
}
