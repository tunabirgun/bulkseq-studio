from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time


root = Path(sys.argv[1])
phase = sys.argv[2]
if not phase.replace("_", "").isalnum():
    raise SystemExit("Phase name must be alphanumeric")
root.mkdir(parents=True, exist_ok=True)
memory = {}
for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
    key, _, value = line.partition(":")
    memory[key] = int(value.strip().split()[0]) * 1024
available = memory["MemAvailable"]
disk = shutil.disk_usage(root)
gib = 1024 ** 3


def paging() -> tuple[int, int]:
    values = {}
    for line in Path("/proc/vmstat").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition(" ")
        if key in {"pswpin", "pswpout"}:
            values[key] = int(value)
    return values["pswpin"], values["pswpout"]


first_paging = paging()
time.sleep(1)
last_paging = paging()
active_paging = sum(after - before for before, after in zip(first_paging, last_paging))
cpu_pairs = set()
for block in Path("/proc/cpuinfo").read_text(encoding="ascii").split("\n\n"):
    fields = {key.strip(): value for line in block.splitlines() if ":" in line
              for key, value in (line.split(":", 1),)}
    if "physical id" in fields and "core id" in fields:
        cpu_pairs.add((fields["physical id"].strip(), fields["core id"].strip()))
usable_cpus = len(os.sched_getaffinity(0))
cpu_max = Path("/sys/fs/cgroup/cpu.max")
if cpu_max.is_file():
    quota, period = cpu_max.read_text(encoding="ascii").split()[:2]
    if quota != "max":
        usable_cpus = min(usable_cpus, max(1, int(quota) // int(period)))
profile = {
    "logical_cpus": os.cpu_count(),
    "physical_cpu_cores": len(cpu_pairs) or os.cpu_count(),
    "usable_cpu_capacity": usable_cpus,
    "total_memory_gib": round(memory["MemTotal"] / gib, 2),
    "available_memory_gib": round(available / gib, 2),
    "swap_total_gib": round(memory["SwapTotal"] / gib, 2),
    "swap_free_gib": round(memory["SwapFree"] / gib, 2),
    "active_paging_pages_in_one_second": active_paging,
    "free_disk_gib": round(disk.free / gib, 2),
    "planned_parallel_jobs": 1,
    "estimated_peak_memory_gib": 2,
    "estimated_output_gib": 0.5,
}
profiles = root / "profiles"
profiles.mkdir(exist_ok=True)
(profiles / f"{phase}.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
if (not profile["logical_cpus"] or usable_cpus < 1 or available < 3 * gib or
        disk.free < 1 * gib or active_paging > 100):
    raise SystemExit(f"Full smoke does not fit the runner: {profile}")
print(f"One job at a time; {profile['available_memory_gib']} GiB available RAM, "
      f"{profile['free_disk_gib']} GiB free disk, {profile['logical_cpus']} logical CPUs")
