import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import nbformat
import pytest
from nbclient import NotebookClient
from labkit import assess, configure, start_cluster
from labkit.launch import seed

ROOT = Path(__file__).resolve().parents[1]


def prepare_workspace(tmp_path, monkeypatch):
    seed(ROOT / "starter", tmp_path)
    monkeypatch.setenv("LAB_WORKSPACE", str(tmp_path))
    # Keep the integration workload small independently of this runner's RAM.
    (tmp_path / "lab_settings.py").write_text("SETTINGS = {'workers': 1, 'threads_per_worker': 1, 'reserve_gib': 0, 'memory_gib': 0.5, 'min_worker_gib': 0.25}\n")
    return tmp_path


@pytest.mark.process_integration
def test_real_worker(tmp_path, monkeypatch):
    prepare_workspace(tmp_path, monkeypatch)
    report = configure(tmp_path / "lab_settings.py")
    cluster, client = start_cluster(report)
    try:
        from labkit import run_analysis
        with pytest.raises(RuntimeError, match='Another managed'):
            run_analysis(lambda: None)
        assert client.submit(sum, [1, 2, 3]).result(timeout=30) == 6
        workers = client.scheduler_info()["workers"]
        assert len(workers) == 1
        assert next(iter(workers.values()))["memory_limit"] == 512 * 1024**2
    finally:
        client.close()
        cluster.close()


@pytest.mark.process_integration
def test_notebook(tmp_path, monkeypatch):
    prepare_workspace(tmp_path, monkeypatch)
    notebook = nbformat.read(tmp_path / "00-Setup.ipynb", as_version=4)
    nbformat.validate(notebook)
    NotebookClient(notebook, timeout=60, kernel_name="python3", resources={"metadata": {"path": str(tmp_path)}}).execute()
    assert json.loads((tmp_path / "hardware_report.json").read_text())["plan"]["workers"] == 1


def test_notebook_cells_in_process(tmp_path, monkeypatch):
    from IPython.core.interactiveshell import InteractiveShell
    prepare_workspace(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    shell = InteractiveShell()
    for name in ('00-Setup.ipynb', '01-Managed-Analysis.ipynb'):
        notebook = nbformat.read(tmp_path / name, as_version=4)
        nbformat.validate(notebook)
        for cell in notebook.cells:
            if cell.cell_type == "code":
                result = shell.run_cell(cell.source)
                assert result.success, str(result.error_in_exec or result.error_before_exec)
    assert json.loads((tmp_path / "hardware_report.json").read_text())["plan"]["workers"] == 1


def test_jupyter_http_authentication(tmp_path):
    token = "integration-test-token-with-32-chars"
    token_path = tmp_path / "token"
    token_path.write_text(token)
    workspace = tmp_path / "work"
    seed(ROOT / "starter", workspace)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, LAB_TOKEN_PATH=str(token_path), LAB_WORKSPACE=str(workspace),
               JUPYTER_RUNTIME_DIR=str(tmp_path / "runtime"), JUPYTER_CONFIG_DIR=str(tmp_path / "config"))
    with (tmp_path / "server.log").open("w") as log:
        server = subprocess.Popen([sys.executable, "-m", "jupyterlab", "--allow-root",
            f"--config={ROOT / 'jupyter_server_config.py'}", f"--ServerApp.port={port}",
            "--ServerApp.ip=127.0.0.1"], env=env, stdout=log, stderr=log)
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise AssertionError((tmp_path / "server.log").read_text())
                try:
                    if urllib.request.urlopen(base + "/login", timeout=1).status == 200:
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.2)
            else:
                raise AssertionError("Server failed to start")
            try:
                urllib.request.urlopen(base + "/api/contents")
                raise AssertionError("Unauthenticated access succeeded")
            except urllib.error.HTTPError as error:
                assert error.code in (401, 403)
            request = urllib.request.Request(base + "/api/contents", headers={"Authorization": "token " + token})
            result = json.load(urllib.request.urlopen(request))
            assert "00-Setup.ipynb" in [item["name"] for item in result["content"]]
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
