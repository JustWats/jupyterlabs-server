"""Managed single-job execution for CPU, CuPy, or PyTorch statistics."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import cloudpickle
import psutil
from .hardware import GIB, assess
from .safety import AnalysisLease, positive_number, available_ram


def stop_group(process):
    # Kill only the dedicated job's process group, including its child workers.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_analysis(function, *args, backend='cpu', device=0, ram_gib=None,
                 vram_gib=None, gpu_fraction=0.50, gpu_reserve_gib=1.0,
                 reserve_gib=1.0, threads=1, timeout_s=900,
                 max_result_mib=64, **kwargs):
    """Run a trusted callable in a disposable process with a live RAM watchdog.

    RAM is a sampled process-tree RSS ceiling; Docker's aggregate RAM/CPU caps
    are the hard boundary. GPU caps apply only to the selected allocator.
    Return small CPU-side summaries; save large results explicitly to disk.
    """
    if backend not in ('cpu', 'cupy', 'torch'):
        raise ValueError('backend must be cpu, cupy, or torch')
    if type(device) is not int or device < 0 or type(threads) is not int or threads < 1:
        raise ValueError('device must be a nonnegative CUDA ordinal and threads a positive integer')
    for name, value in [('timeout_s', timeout_s), ('max_result_mib', max_result_mib), ('gpu_fraction', gpu_fraction)]:
        positive_number(value, name)
    for name, value in [('reserve_gib', reserve_gib), ('gpu_reserve_gib', gpu_reserve_gib)]:
        positive_number(value, name, allow_zero=True)
    if gpu_fraction > 0.80:
        raise ValueError('gpu_fraction cannot exceed 0.80')
    if vram_gib is not None:
        positive_number(vram_gib, 'vram_gib')
    if ram_gib is not None:
        positive_number(ram_gib, 'ram_gib')
    with AnalysisLease() as lease:
        hardware = assess()
        memory = hardware['memory']
        safe = max(0, min(int(memory['effective_total_bytes'] * 0.70),
                          memory['available_bytes'] - int(reserve_gib * GIB)))
        budget = safe if ram_gib is None else int(ram_gib * GIB)
        if budget < 128 * 1024**2 or budget > safe:
            raise MemoryError('Requested analysis RAM exceeds current capacity after reserve; reduce ram_gib or free memory')
        if threads > max(1, int(hardware['cpu']['effective_cores'])):
            raise ValueError('threads exceeds effective CPU capacity')
        env = dict(os.environ)
        for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS'):
            env[key] = str(threads)
        if backend == 'cpu':
            env['CUDA_VISIBLE_DEVICES'] = ''
        config = dict(backend=backend, device=device, ram_bytes=budget,
                      vram_gib=vram_gib, gpu_fraction=gpu_fraction,
                      gpu_reserve_gib=gpu_reserve_gib, threads=threads,
                      max_result_bytes=min(int(max_result_mib * 1024**2), budget // 4),
                      timeout_s=timeout_s)
        with tempfile.TemporaryDirectory(prefix='lab-analysis-') as directory:
            root = Path(directory)
            (root / 'config.json').write_text(json.dumps(config))
            with (root / 'input.pkl').open('wb') as output:
                class LimitedWriter:
                    def write(self, data):
                        if output.tell() + len(data) > config['max_result_bytes']:
                            raise ValueError('Input exceeds transfer limit; pass a dataset path instead')
                        return output.write(data)
                cloudpickle.dump((function, args, kwargs), LimitedWriter())
            with (root / 'stderr.txt').open('wb') as errors:
                process = subprocess.Popen([sys.executable, '-m', 'labkit.worker', directory],
                    env=env, stdout=subprocess.DEVNULL, stderr=errors,
                    start_new_session=True, pass_fds=(lease.file.fileno(),))
                deadline = time.monotonic() + timeout_s
                try:
                    while process.poll() is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f'Analysis exceeded {timeout_s} seconds and was stopped')
                        try:
                            parent = psutil.Process(process.pid)
                            processes = [parent] + parent.children(recursive=True)
                            rss = 0
                            for child in processes:
                                try:
                                    rss += child.memory_info().rss
                                except psutil.NoSuchProcess:
                                    pass
                            if rss > budget:
                                raise MemoryError('Analysis exceeded its RAM budget and was stopped')
                        except psutil.NoSuchProcess:
                            if process.poll() is None:
                                raise RuntimeError('Cannot monitor the live analysis process; job was stopped rather than running without RAM monitoring') from None
                        if available_ram() < int(reserve_gib * GIB):
                            raise MemoryError('Available RAM fell below the reserve; analysis was stopped')
                        time.sleep(0.20)
                    if process.returncode != 0:
                        raise RuntimeError('Analysis failed: ' + (root / 'stderr.txt').read_text(errors='replace')[-2000:])
                    with (root / 'output.pkl').open('rb') as result:
                        return cloudpickle.load(result)
                finally:
                    # Also clean up descendants left by a callable that returned.
                    stop_group(process)
