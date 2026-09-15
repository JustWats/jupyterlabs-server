import os
import sys
import time
from types import SimpleNamespace
import pytest
from labkit.safety import AnalysisLease, gpu_budget, validate_host_limits
from labkit.hardware import GIB
from labkit import run_analysis
from labkit.worker import configure_backend


def host(cap=4, quota=2, total=16, cpus=8):
    return {'memory': {'cgroup_limit_bytes': cap * GIB if cap else None, 'proc_total_bytes': total * GIB},
            'cpu': {'quota_cores': quota, 'logical_visible': cpus}}


def test_host_requires_explicit_limits_and_headroom():
    validate_host_limits(host())
    validate_host_limits(host(cap=768, total=1024, quota=192, cpus=256))
    for invalid in [host(cap=None), host(quota=None), host(cap=15), host(quota=8), host(total=4)]:
        with pytest.raises(RuntimeError):
            validate_host_limits(invalid)


def test_lease_blocks_overlap_and_releases(tmp_path, monkeypatch):
    monkeypatch.setenv('LAB_LOCK_DIR', str(tmp_path))
    with AnalysisLease():
        with pytest.raises(RuntimeError, match='Another managed'):
            AnalysisLease()
    with AnalysisLease():
        pass


def test_gpu_budget_uses_current_free_memory_and_reserve():
    assert gpu_budget(80 * GIB, 12 * GIB, .5, 2) == 10 * GIB
    assert gpu_budget(80 * GIB, 70 * GIB, .5, 2) == 40 * GIB
    with pytest.raises(MemoryError):
        gpu_budget(80 * GIB, 12 * GIB, .5, 2, 11)
    with pytest.raises(MemoryError):
        gpu_budget(80 * GIB, GIB, .5, 2)
    with pytest.raises(ValueError):
        gpu_budget(80 * GIB, 70 * GIB, .9, 1)


def gpu_config(backend):
    return dict(backend=backend, device=0, gpu_fraction=.5,
                gpu_reserve_gib=1, vram_gib=2, threads=1)


def test_torch_selected_device_allocator_cap(monkeypatch):
    calls = []
    cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1,
        set_device=lambda d: calls.append(('device', d)), mem_get_info=lambda d: (6 * GIB, 8 * GIB),
        memory=SimpleNamespace(set_per_process_memory_fraction=lambda f, d: calls.append(('cap', f, d))),
        synchronize=lambda d: calls.append(('sync', d)))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=cuda, set_num_threads=lambda n: None))
    configure_backend(gpu_config('torch'))()
    assert calls == [('device', 0), ('cap', .25, 0), ('sync', 0)]
    cuda.device_count = lambda: 0
    with pytest.raises(RuntimeError, match='no CPU fallback'):
        configure_backend(gpu_config('torch'))


def test_cupy_selected_device_allocator_cap(monkeypatch):
    calls = []
    fake = SimpleNamespace(cuda=SimpleNamespace(
        Device=lambda d: SimpleNamespace(use=lambda: calls.append(('device', d))),
        runtime=SimpleNamespace(getDeviceCount=lambda: 1,
            memGetInfo=lambda: (6 * GIB, 8 * GIB), deviceSynchronize=lambda: None)),
        get_default_memory_pool=lambda: SimpleNamespace(set_limit=lambda size: calls.append(('cap', size))))
    monkeypatch.setitem(sys.modules, 'cupy', fake)
    configure_backend(gpu_config('cupy'))()
    assert calls == [('device', 0), ('cap', 2 * GIB)]


@pytest.mark.parametrize('kwargs', [{'backend':'invalid'}, {'threads':0}, {'timeout_s':float('nan')},
    {'gpu_fraction':1}, {'ram_gib':-1}, {'device':True}, {'reserve_gib':-1}])
def test_invalid_job_settings(kwargs):
    with pytest.raises(ValueError):
        run_analysis(lambda: None, **kwargs)


def test_reject_oversized_ram_before_launch(tmp_path, monkeypatch):
    monkeypatch.setenv('LAB_LOCK_DIR', str(tmp_path))
    with pytest.raises(MemoryError):
        run_analysis(lambda: None, ram_gib=10**9)


@pytest.mark.process_integration
def test_managed_job_success_timeout_and_ram_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('LAB_LOCK_DIR', str(tmp_path))
    assert run_analysis(lambda x: x + 1, 4, ram_gib=.25, reserve_gib=0) == 5
    with pytest.raises(TimeoutError):
        run_analysis(time.sleep, 10, timeout_s=.25, ram_gib=.25, reserve_gib=0)
    def allocate():
        buffer = bytearray(256 * 1024**2)
        time.sleep(5)
        return len(buffer)
    with pytest.raises(MemoryError):
        run_analysis(allocate, ram_gib=.125, reserve_gib=0)
    # The failed jobs release the lease, so a new job must succeed.
    assert run_analysis(lambda: 'alive', ram_gib=.25, reserve_gib=0) == 'alive'


def test_transfer_limit_rejects_large_input(tmp_path, monkeypatch):
    monkeypatch.setenv('LAB_LOCK_DIR', str(tmp_path))
    with pytest.raises(ValueError, match='Input exceeds'):
        run_analysis(len, b'x' * 2 * 1024**2, max_result_mib=1, reserve_gib=0)


def test_unobservable_job_fails_closed(tmp_path, monkeypatch):
    import psutil
    monkeypatch.setenv('LAB_LOCK_DIR', str(tmp_path))
    def inaccessible(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr('labkit.analysis.psutil.Process', inaccessible)
    with pytest.raises(RuntimeError, match='Cannot monitor'):
        run_analysis(time.sleep, 10, ram_gib=.25, reserve_gib=0)
    with AnalysisLease():
        pass
