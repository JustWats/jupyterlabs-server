import subprocess
from types import SimpleNamespace
import pytest
from labkit.hardware import GIB, cgroup_limits, cgroup_dirs, gpu_inventory
from labkit.runtime import plan
from labkit.launch import seed


def machine(gib=1024, cpus=256, available=None):
    return {"cpu": {"effective_cores": cpus},
            "memory": {"effective_total_bytes": gib * GIB,
                       "available_bytes": (gib if available is None else available) * GIB},
            "gpu": {"devices": []}}


@pytest.mark.parametrize("gib,cpus", [(8, 4), (64, 32), (1024, 256), (2048, 512)])
def test_large_and_small_servers(gib, cpus):
    result = plan(machine(gib, cpus), {"preset": "throughput"})
    assert result["workers"] * result["threads_per_worker"] <= cpus
    assert result["workers"] * result["memory_per_worker_bytes"] <= gib * GIB * 0.8
    assert result["memory_budget_bytes"] <= (gib - 2) * GIB


def test_one_tib_exact_budget():
    result = plan(machine(), {"preset": "throughput", "memory_gib": 768})
    assert result["memory_budget_bytes"] == 768 * GIB
    assert result["workers"] == 121


def test_busy_host_and_fractional_quota():
    result = plan(machine(1024, 2.5, 8), {})
    assert result["memory_budget_bytes"] == 6 * GIB
    assert result["workers"] == 1
    assert result["threads_per_worker"] == 1


@pytest.mark.parametrize("settings", [{"workers": 200}, {"memory_gib": 900},
    {"cpu_fraction": 0}, {"memory_fraction": 1.1}, {"reserve_gib": -1},
    {"threads_per_worker": True}, {"preset": "typo"}, {"typo": 1},
    {"memory_gib": float("nan")}, {"gpu_memory_fraction": float("inf")}])
def test_invalid_or_oversubscribed(settings):
    with pytest.raises(ValueError):
        plan(machine(), settings)


def test_low_memory_fails_without_allocation():
    with pytest.raises(ValueError, match="Insufficient"):
        plan(machine(2, 4), {})


def test_cgroup_v2_ancestor_headroom_and_quota(tmp_path):
    parent, child = tmp_path, tmp_path / "child"
    child.mkdir()
    for path, cap, usage, quota in [(parent, 16, 12, "150000 100000"), (child, 32, 2, "400000 100000")]:
        (path / "memory.max").write_text(str(cap * GIB))
        (path / "memory.current").write_text(str(usage * GIB))
        (path / "cpu.max").write_text(quota)
    result = cgroup_limits([("cgroup2", "", child), ("cgroup2", "", parent)])
    assert result == {"memory_limit_bytes": 16 * GIB, "memory_headroom_bytes": 4 * GIB, "cpu_quota": 1.5}


def test_v1_and_unlimited(tmp_path):
    (tmp_path / "memory.limit_in_bytes").write_text(str(8 * GIB))
    (tmp_path / "memory.usage_in_bytes").write_text(str(3 * GIB))
    (tmp_path / "cpu.cfs_quota_us").write_text("250000")
    (tmp_path / "cpu.cfs_period_us").write_text("100000")
    result = cgroup_limits([("cgroup", "memory", tmp_path), ("cgroup", "cpu", tmp_path)])
    assert result["memory_headroom_bytes"] == 5 * GIB
    assert result["cpu_quota"] == 2.5
    (tmp_path / "memory.limit_in_bytes").write_text(str(2**63 - 4096))
    assert cgroup_limits([("cgroup", "memory", tmp_path)])["memory_limit_bytes"] is None
    (tmp_path / "memory.max").write_text("max")
    (tmp_path / "cpu.max").write_text("max 100000")
    assert cgroup_limits([("cgroup2", "", tmp_path)])["cpu_quota"] is None


def test_mount_resolution_nested_and_private(tmp_path):
    (tmp_path / "child").mkdir()
    mount = f"25 24 0:22 /slice {tmp_path} rw - cgroup2 cgroup rw"
    found = cgroup_dirs(mount, "0::/slice/child")
    assert found == [("cgroup2", "", tmp_path / "child"), ("cgroup2", "", tmp_path)]
    assert cgroup_dirs(mount, "0::/") == [("cgroup2", "", tmp_path)]


def test_nvidia_multi_gpu_and_advisory_budget(monkeypatch):
    monkeypatch.setattr("labkit.hardware.shutil.which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr("labkit.hardware.subprocess.run", lambda *a, **kw: SimpleNamespace(
        stdout='0, GPU-a, H100, 81920, 71680, 580.0\n1, GPU-b, RTX 4070, 12288, 8192, 580.0\n'))
    inventory = gpu_inventory()
    assert inventory["devices"][0]["total_bytes"] == 80 * GIB
    hardware = machine()
    hardware["gpu"] = inventory
    result = plan(hardware, {})
    assert result["gpu_budgets"][0]["advisory_budget_bytes"] == 64 * GIB
    assert result["gpu_budgets"][1]["advisory_budget_bytes"] == 8 * GIB


def test_nvidia_absent_or_timeout(monkeypatch):
    monkeypatch.setattr("labkit.hardware.shutil.which", lambda _: None)
    assert gpu_inventory()["status"] == "unavailable"
    monkeypatch.setattr("labkit.hardware.shutil.which", lambda _: "nvidia-smi")
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("nvidia-smi", 10)
    monkeypatch.setattr("labkit.hardware.subprocess.run", timeout)
    assert gpu_inventory()["status"] == "error"


def test_seed_preserves_user_edits(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "lab_settings.py").write_text("original")
    seed(source, target)
    (target / "lab_settings.py").write_text("user edit")
    seed(source, target)
    assert (target / "lab_settings.py").read_text() == "user edit"
