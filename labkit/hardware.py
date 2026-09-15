"""Read-only Linux inventory. Never allocates a test workload or changes the host."""
import csv
import io
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

GIB = 1024**3


def read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return ""


def positive(value):
    try:
        result = int(value)
        return result if result > 0 else None
    except (ValueError, TypeError):
        return None


def unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def cgroup_dirs(mountinfo=None, membership=None):
    """Resolve this process's groups, including visible limiting ancestors.

    Mount roots can differ from membership paths with a private cgroup namespace.
    Only ancestors visible inside the container can be inspected.
    """
    membership = read("/proc/self/cgroup") if membership is None else membership
    mountinfo = read("/proc/self/mountinfo") if mountinfo is None else mountinfo
    groups = {}
    for line in membership.splitlines():
        _, controllers, path = line.split(":", 2)
        for controller in controllers.split(","):
            groups[controller] = path
    result = []
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields, tail = left.split(), right.split()
        if tail[0] not in ("cgroup", "cgroup2"):
            continue
        root, mount = Path(unescape(fields[3])), Path(unescape(fields[4]))
        controllers = [""] if tail[0] == "cgroup2" else tail[2].split(",")
        for controller in controllers:
            if controller not in groups:
                continue
            group = Path(groups[controller])
            try:
                rel = group.relative_to(root)
            except ValueError:
                # A private namespace reports paths relative to the mount root.
                rel = Path(str(group).lstrip("/"))
            if ".." in rel.parts:
                continue
            current = mount / rel
            if not current.is_dir():
                current = mount
            while True:
                entry = (tail[0], controller, current)
                if entry not in result:
                    result.append(entry)
                if current == mount:
                    break
                current = current.parent
    return result


def cgroup_limits(dirs=None):
    limits, headrooms, quotas = [], [], []
    for kind, controller, path in cgroup_dirs() if dirs is None else dirs:
        if kind == "cgroup2" or controller == "memory":
            cap = positive(read(path / ("memory.max" if kind == "cgroup2" else "memory.limit_in_bytes")))
            usage_text = read(path / ("memory.current" if kind == "cgroup2" else "memory.usage_in_bytes"))
            # v1's unlimited sentinel is near signed 64-bit maximum.
            if cap is not None and cap < 2**60:
                limits.append(cap)
                if usage_text.isdigit():
                    headrooms.append(max(0, cap - int(usage_text)))
        if kind == "cgroup2":
            parts = read(path / "cpu.max").split()
            if len(parts) == 2 and positive(parts[0]) and positive(parts[1]):
                quotas.append(int(parts[0]) / int(parts[1]))
        elif controller == "cpu":
            quota, period = positive(read(path / "cpu.cfs_quota_us")), positive(read(path / "cpu.cfs_period_us"))
            if quota and period:
                quotas.append(quota / period)
    return {"memory_limit_bytes": min(limits) if limits else None,
            "memory_headroom_bytes": min(headrooms) if headrooms else None,
            "cpu_quota": min(quotas) if quotas else None}


def gpu_inventory():
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"status": "unavailable", "devices": [], "detail": "NVIDIA tools/devices are not exposed. This does not prove the host has no GPU."}
    try:
        process = subprocess.run([executable,
            "--query-gpu=index,uuid,name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10, check=True)
        devices = []
        for row in csv.reader(io.StringIO(process.stdout)):
            if not row:
                continue
            index, uuid, name, total, free, driver = (value.strip() for value in row)
            def mib(value):
                try:
                    return int(float(value) * 1024**2)
                except ValueError:
                    return None
            devices.append(dict(index=int(index), uuid=uuid, name=name,
                total_bytes=mib(total), free_bytes=mib(free), driver=driver))
        return {"status": "ok", "devices": devices,
                "detail": "Physical-device NVML report; MIG slices and framework CUDA visibility may differ. VRAM is not pooled."}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"status": "error", "devices": [], "detail": f"NVIDIA query failed ({type(error).__name__}); CPU operation remains available."}


def assess(workspace="."):
    memory = {}
    for line in read("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
    if not memory:
        raise RuntimeError("Hardware assessment requires Linux /proc (run inside the supplied container).")
    limits = cgroup_limits()
    logical = os.cpu_count() or 1
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(logical))
    cores, packages = set(), set()
    for cpu in affinity:
        base = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        package, core = read(base / "physical_package_id"), read(base / "core_id")
        if package and core:
            cores.add((package, core))
            packages.add(package)
    effective = min(len(affinity), limits["cpu_quota"] or len(affinity))
    total = memory["MemTotal"]
    cap = min(total, limits["memory_limit_bytes"] or total)
    available = min(cap, memory.get("MemAvailable", memory.get("MemFree", 0)))
    if limits["memory_headroom_bytes"] is not None:
        available = min(available, limits["memory_headroom_bytes"])
    disk = shutil.disk_usage(workspace)
    return {"platform": platform.platform(), "architecture": platform.machine(),
        "cpu": {"logical_visible": logical, "affinity_count": len(affinity),
                "physical_cores_in_affinity": len(cores) or None,
                "sockets_in_affinity": len(packages) or None, "effective_cores": effective,
                "quota_cores": limits["cpu_quota"]},
        "memory": {"proc_total_bytes": total, "effective_total_bytes": cap,
                   "available_bytes": available, "cgroup_limit_bytes": limits["memory_limit_bytes"]},
        "gpu": gpu_inventory(),
        "storage": {"path": str(Path(workspace).resolve()), "total_bytes": disk.total, "free_bytes": disk.free},
        "notes": ["/proc may describe a host or Docker Desktop VM. Effective resources include visible cgroup limits and CPU affinity.",
                  "Snapshots are not reservations. Hidden ancestor limits and competing workloads can reduce actual capacity."]}
