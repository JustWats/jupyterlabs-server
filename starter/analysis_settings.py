"""Settings for run_analysis; independent of the optional Dask pool settings.

Docker hard limits live in the host's compose.yaml/.env. Python cannot raise
them. A selected GPU backend must be installed and available; no fallback.
"""
ANALYSIS = {
    'backend': 'cpu',  # cpu | cupy | torch
    'device': 0,       # CUDA ordinal inside the container, not host GPU index
    'ram_gib': None,   # Auto: <=70% of effective RAM and below free RAM-reserve
    'reserve_gib': 1.0,
    'threads': 1,
    'timeout_s': 900,
    'vram_gib': None,
    'gpu_fraction': 0.50,
    'gpu_reserve_gib': 1.0,
    'max_result_mib': 64,
}
