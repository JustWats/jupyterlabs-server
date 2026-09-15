"""Edit in JupyterLab, save, then rerun setup_lab.py to apply to this kernel.

Close existing Dask clients/clusters before applying and recreating their pool.
No restart is needed for assessment; native library settings are best applied
before imports. These settings never change Docker or host limits.
"""
SETTINGS = {
    "preset": "balanced",  # conservative | balanced | throughput
    # Uncomment any overrides. None means automatic where shown.
    # "cpu_fraction": 0.75,
    # "memory_fraction": 0.70,
    "reserve_gib": 2.0,  # Keep this much currently available RAM outside pool.
    "memory_gib": None,  # Total Dask pool budget, not a per-worker setting.
    "workers": None,
    # "max_workers": 32,  # Raise for large servers after measuring workload.
    # "threads_per_worker": 2,
    # "blas_threads": 1,  # Avoid each worker spawning a full BLAS thread pool.
    "min_worker_gib": 1.0,
    "gpu_memory_fraction": 0.80,  # Advisory per physical GPU; allocates nothing.
}
