"""Resource admission checks, not a security sandbox for arbitrary code."""
import fcntl
import math
import os
from pathlib import Path
from .hardware import GIB, cgroup_limits, read


def available_ram():
    values = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in read('/proc/meminfo').splitlines()}
    available = values.get('MemAvailable', values.get('MemFree', 0))
    headroom = cgroup_limits()['memory_headroom_bytes']
    return min(available, headroom) if headroom is not None else available


def positive_number(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} is out of range")
    return value


def validate_host_limits(hardware):
    """Require explicit caps and leave visible host/VM headroom at startup."""
    memory, cpu = hardware['memory'], hardware['cpu']
    cap, quota = memory['cgroup_limit_bytes'], cpu['quota_cores']
    if cap is None or quota is None:
        raise RuntimeError('Explicit Docker RAM and CPU limits are required. Use the current compose.yaml.')
    host_total = memory['proc_total_bytes']
    reserve = max(2 * GIB, int(host_total * 0.20))
    if cap > host_total - reserve:
        raise RuntimeError('LAB_MEMORY_LIMIT is too large for this host/VM; leave at least 20% and 2 GiB outside Jupyter.')
    if quota > max(0.5, cpu['logical_visible'] * 0.75):
        raise RuntimeError('LAB_CPU_LIMIT must leave at least 25% of visible host/VM CPU capacity outside Jupyter.')


class AnalysisLease:
    """One managed job or Dask pool per shared Jupyter home, across kernels."""
    def __init__(self):
        directory = Path(os.environ.get('LAB_LOCK_DIR', str(Path.home() / '.local/share/labkit')))
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / 'analysis.lock').open('a+')
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError('Another managed analysis or Dask pool is active. Close it before starting another.') from None

    def close(self):
        # Do not LOCK_UN: a worker inherits the same open file description and
        # must keep the lease if its parent notebook kernel disappears.
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def gpu_budget(total, free, fraction, reserve_gib, requested_gib=None):
    positive_number(fraction, 'gpu_fraction')
    positive_number(reserve_gib, 'gpu_reserve_gib', allow_zero=True)
    if fraction > 0.80:
        raise ValueError('gpu_fraction cannot exceed 0.80 in the managed runner')
    safe = min(int(total * fraction), max(0, free - int(reserve_gib * GIB)))
    requested = safe if requested_gib is None else int(positive_number(requested_gib, 'vram_gib') * GIB)
    if requested <= 0 or requested > safe:
        raise MemoryError('Selected GPU lacks the requested free VRAM plus reserve; job was not started')
    return requested
