import json
import math
import os
import runpy
from pathlib import Path
from .hardware import GIB, assess

PRESETS = {
    "balanced": dict(cpu_fraction=0.75, memory_fraction=0.70, threads_per_worker=2, max_workers=32, blas_threads=1),
    "conservative": dict(cpu_fraction=0.50, memory_fraction=0.50, threads_per_worker=2, max_workers=16, blas_threads=1),
    "throughput": dict(cpu_fraction=0.95, memory_fraction=0.80, threads_per_worker=2, max_workers=128, blas_threads=1),
}


def plan(hardware, settings):
    name = settings.get("preset", "balanced")
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: {name}")
    options = dict(PRESETS[name], reserve_gib=2.0, memory_gib=None, workers=None,
                   min_worker_gib=1.0, gpu_memory_fraction=0.80)
    unknown = set(settings) - set(options) - {"preset"}
    if unknown:
        raise ValueError(f"Unknown settings: {sorted(unknown)}")
    options.update({k: v for k, v in settings.items() if k != "preset"})
    for key in ("cpu_fraction", "memory_fraction", "gpu_memory_fraction"):
        value = options[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise ValueError(f"{key} must be > 0 and <= 1")
    for key in ("threads_per_worker", "max_workers", "blas_threads", "workers"):
        value = options[key]
        if key == "workers" and value is None:
            continue
        if type(value) is not int or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("reserve_gib", "min_worker_gib", "memory_gib"):
        value = options[key]
        if key == "memory_gib" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (key != "reserve_gib" and value == 0):
            raise ValueError(f"Invalid {key}")
    cpu_budget = max(1, math.floor(hardware["cpu"]["effective_cores"] * options["cpu_fraction"]))
    threads = min(cpu_budget, options["threads_per_worker"])
    memory = hardware["memory"]
    safe_memory = max(0, min(int(memory["effective_total_bytes"] * options["memory_fraction"]),
                             memory["available_bytes"] - int(options["reserve_gib"] * GIB)))
    budget = int(options["memory_gib"] * GIB) if options["memory_gib"] is not None else safe_memory
    if budget > safe_memory:
        raise ValueError("memory_gib exceeds the current budget; reduce it or explicitly adjust memory_fraction/reserve_gib")
    memory_workers = math.floor(budget / (options["min_worker_gib"] * GIB))
    if memory_workers < 1:
        raise ValueError("Insufficient available RAM after reserve; lower reserve_gib/min_worker_gib or free memory")
    maximum = min(max(1, cpu_budget // threads), options["max_workers"], memory_workers)
    workers = options["workers"] if options["workers"] is not None else maximum
    if workers > maximum:
        raise ValueError(f"workers exceeds this plan's CPU/RAM/max_workers budget ({maximum})")
    gpu_budgets = []
    for gpu in hardware["gpu"]["devices"]:
        total, free = gpu["total_bytes"], gpu["free_bytes"]
        gpu_budgets.append({"uuid": gpu["uuid"], "advisory_budget_bytes":
            int(min(total * options["gpu_memory_fraction"], free)) if total is not None and free is not None else None})
    return {"preset": name, "cpu_budget": cpu_budget, "workers": workers,
            "threads_per_worker": threads, "blas_threads": min(options["blas_threads"], cpu_budget),
            "memory_budget_bytes": budget, "memory_per_worker_bytes": budget // workers,
            "gpu_budgets": gpu_budgets, "options": options}


def configure(config_path=None, write_report=True):
    workspace = Path(os.environ.get("LAB_WORKSPACE", "/home/jovyan/work"))
    config_path = Path(config_path) if config_path else workspace / "lab_settings.py"
    # This is deliberately executable Python, like any notebook in this single-user workspace.
    settings = runpy.run_path(str(config_path))["SETTINGS"]
    hardware = assess(config_path.parent)
    selected = plan(hardware, settings)
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        os.environ[key] = str(selected["blas_threads"])
    # Apply to already-loaded supported BLAS libraries, too. Retain the controller.
    global _thread_controller
    from threadpoolctl import threadpool_limits
    _thread_controller = threadpool_limits(limits=selected["blas_threads"])
    report = {"hardware": hardware, "plan": selected}
    if write_report:
        target = config_path.parent / "hardware_report.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(target)
    return report


def start_cluster(report):
    """Explicit opt-in: starts CPU Dask processes, never GPU or model workers."""
    from distributed import Client, LocalCluster
    from .safety import AnalysisLease
    from distributed.core import Status
    import dask
    lease = AnalysisLease()
    workspace = Path(os.environ.get("LAB_WORKSPACE", "/home/jovyan/work"))
    class LeasedCluster(LocalCluster):
        def close(self, *args, **kwargs):
            result = super().close(*args, **kwargs)
            if self.status == Status.closed:
                lease.close()
            return result
    try:
        selected = plan(assess(workspace), dict(preset=report['plan']['preset'], **report['plan']['options']))
        spill = Path(os.environ.get('LAB_SPILL_DIR', str(workspace / '.dask-spill')))
        spill.mkdir(parents=True, exist_ok=True)
        with dask.config.set({'distributed.worker.memory.target': 0.55,
                              'distributed.worker.memory.spill': 0.65,
                              'distributed.worker.memory.pause': 0.75,
                              'distributed.worker.memory.terminate': 0.85,
                              'distributed.worker.memory.max-spill': (512 * 1024**2) // selected['workers']}):
            cluster = LeasedCluster(n_workers=selected["workers"],
                threads_per_worker=selected["threads_per_worker"],
                memory_limit=selected["memory_per_worker_bytes"], processes=True,
                host="127.0.0.1", dashboard_address=None, local_directory=str(spill))
        try:
            client = Client(cluster)
        except Exception:
            cluster.close()
            raise
    except Exception:
        lease.close()
        raise
    return cluster, client
