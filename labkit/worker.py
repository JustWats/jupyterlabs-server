"""Implementation detail of run_analysis. One process, one selected backend."""
import json
import os
import resource
import signal
import sys
from pathlib import Path
import cloudpickle
from .safety import gpu_budget


def configure_backend(config):
    backend, device = config['backend'], config['device']
    if backend == 'cpu':
        return lambda: None
    if backend == 'torch':
        import torch
        if not torch.cuda.is_available() or device >= torch.cuda.device_count():
            raise RuntimeError('Requested PyTorch CUDA device is unavailable; no CPU fallback was performed')
        torch.set_num_threads(config['threads'])
        torch.cuda.set_device(device)
        free, total = torch.cuda.mem_get_info(device)
        budget = gpu_budget(total, free, config['gpu_fraction'], config['gpu_reserve_gib'], config['vram_gib'])
        torch.cuda.memory.set_per_process_memory_fraction(budget / total, device)
        return lambda: torch.cuda.synchronize(device)
    import cupy
    if device >= cupy.cuda.runtime.getDeviceCount():
        raise RuntimeError('Requested CuPy CUDA device is unavailable; no CPU fallback was performed')
    cupy.cuda.Device(device).use()
    free, total = cupy.cuda.runtime.memGetInfo()
    budget = gpu_budget(total, free, config['gpu_fraction'], config['gpu_reserve_gib'], config['vram_gib'])
    cupy.get_default_memory_pool().set_limit(size=budget)
    return cupy.cuda.runtime.deviceSynchronize


def main():
    root = Path(sys.argv[1])
    config = json.loads((root / 'config.json').read_text())
    # Bound each worker-written file (including stderr and transfer output).
    size = config['max_result_bytes']
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Bound this entire dedicated process group if the notebook parent vanishes.
    signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    signal.alarm(max(1, int(config['timeout_s']) + 1))
    synchronize = configure_backend(config)
    with (root / 'input.pkl').open('rb') as source:
        function, args, kwargs = cloudpickle.load(source)
    result = function(*args, **kwargs)
    synchronize()
    # Results must be host-side objects; GPU tensors retain device allocations
    # and may initialize CUDA in the notebook during unpickling.
    import pickle
    class HostResultPickler(cloudpickle.CloudPickler):
        def persistent_id(self, obj):
            module = type(obj).__module__.split('.')[0]
            if module == 'cupy' or (module == 'torch' and getattr(obj, 'is_cuda', False)):
                raise ValueError('Return CPU-side results: use .get() for CuPy or .cpu() for PyTorch')
            return None
    with (root / 'output.pkl').open('wb') as output:
        HostResultPickler(output, protocol=pickle.HIGHEST_PROTOCOL).dump(result)


if __name__ == '__main__':
    main()
