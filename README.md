# JupyterLab Server

A single-user JupyterLab container with hardware assessment, editable Python
presets, a setup notebook, and an optional local Dask CPU worker pool.

## Start with Docker Compose

Requires Docker Engine and Docker Compose v2 on a Linux x86-64 host.
The published image is public; no GitHub login or source build is needed.
Download just the Compose file, start JupyterLab, then retrieve its login token:

```bash
curl -fsSLO https://raw.githubusercontent.com/JustWats/jupyterlabs-server/main/compose.yaml
docker compose up -d
docker compose logs lab
```

If you already downloaded or cloned this repository, only the last two commands
are needed. `docker compose up -d` pulls the image when needed and starts it.
From another machine on the same network, open `http://<server-ip>:9999/lab`
and paste the token from the logs into the login page. On the Docker host itself,
use `http://localhost:9999/lab`. Jupyter's own log URLs show its internal port
8888; use the Docker host's IP and published port 9999 in your browser.

A fresh volume receives `00-Setup.ipynb`, `lab_settings.py`, `setup_lab.py`,
`01-Managed-Analysis.ipynb`, and `analysis_settings.py`. New starter files are
also added to existing volumes when missing, without replacing edited files.
Settings, notebooks, the generated token, and user-installed Python packages
persist in the `justwats-lab_lab-home` volume. `docker compose down` stops and
removes the container while preserving the volume. `docker compose down -v`
deletes the volume and its contents.

### NVIDIA GPU access

Download the base file and GPU override in one command:

```bash
curl -fsSLO https://raw.githubusercontent.com/JustWats/jupyterlabs-server/main/compose.yaml -O https://raw.githubusercontent.com/JustWats/jupyterlabs-server/main/compose.gpu.yaml
docker compose -f compose.yaml -f compose.gpu.yaml up -d
docker compose logs lab
```

The host needs a working NVIDIA driver and NVIDIA Container Toolkit configured
for Docker. The container cannot install host drivers or expose devices to itself.
Use both `-f` arguments for subsequent GPU updates and container recreation.

### Remote access and Compose settings

Compose publishes TCP port 9999 on all host IPv4 interfaces by default, forwarding
it to Jupyter's internal port 8888. Same-subnet clients can connect directly;
no SSH tunnel or extra Compose override is needed. Token authentication remains
enabled. This is a single-user server, not JupyterHub.

The host firewall and any network ACLs must permit client traffic to TCP 9999.
Compose cannot override an independently configured firewall or subnet isolation.

Optional settings can go in a `.env` file beside `compose.yaml`:

```dotenv
LAB_IMAGE=ghcr.io/justwats/jupyterlabs-server:latest
LAB_BIND=0.0.0.0
LAB_PORT=9999
LAB_SHM_SIZE=2gb
LAB_MEMORY_LIMIT=4g
LAB_CPU_LIMIT=2.0
LAB_PIDS_LIMIT=1024
LAB_GPU_DEVICE=0
```

To restrict listening to one interface, set `LAB_BIND` to its IP address.
For host-only access, set `LAB_BIND=127.0.0.1`. An existing `.env` or exported
`LAB_BIND`/`LAB_PORT` value overrides the defaults even after updating compose.yaml.
Use HTTPS through a reverse proxy when access must traverse an untrusted network.
Python resource settings remain in `lab_settings.py` inside JupyterLab.

If migrating from the earlier `docker run` example, stop the old `jupyterlab`
container before sharing its home volume. To reuse its notebooks, add
`name: jupyterlab-home` under the top-level `volumes: lab-home:` entry before
starting Compose. Otherwise Compose creates its own separate home volume.

### Updates and optional source builds

To update an existing Compose deployment from localhost:8888 to LAN access on
port 9999, download the current Compose file and recreate the service:

```bash
curl -fsSLO https://raw.githubusercontent.com/JustWats/jupyterlabs-server/main/compose.yaml
docker compose up -d --force-recreate
docker compose logs lab
```

The existing Compose home volume and login token are preserved. If you set
`LAB_BIND` or `LAB_PORT` previously, update those overrides as well. For NVIDIA,
include `-f compose.yaml -f compose.gpu.yaml` in the recreation command.

Update the published image and recreate the service with
`docker compose up -d --pull always`. Existing notebooks and settings persist.
For NVIDIA, include `-f compose.yaml -f compose.gpu.yaml` in that command.

The default Compose file only uses the published image. To build a modified
source checkout, include the build override:

```bash
docker compose -f compose.yaml -f compose.build.yaml up -d --build
docker compose logs lab
```

For a local GPU build, add `-f compose.gpu.yaml` after the build override.

## Configuration inside Jupyter

### Protect the host and select an analysis backend

Compose now enforces a **4 GiB total RAM limit**, a **2-CPU quota**, **no swap**,
and a **1,024 process/thread limit** by default. These limits cover Jupyter,
every kernel, terminals, and all their workers together. They apply even when
a notebook ignores the Python helper. Logs rotate at 10 MiB with three files.
Dask spill uses a 512 MiB tmpfs charged against the RAM cap, avoiding unbounded
spill writes onto the host disk. User notebooks and arbitrary output files on
the persistent home volume still need filesystem quotas if a hard disk limit
is required.

At startup the image refuses missing Docker CPU/RAM caps or a RAM allowance
that leaves less than both 20% and 2 GiB of the visible host/VM outside Jupyter.
The CPU quota must leave at least 25% of visible host/VM CPU capacity. These
checks do not reserve resources against unrelated applications on the host.
The default allocation requires at least about 6 GiB RAM and 3 logical CPUs;
reduce `.env` limits for smaller machines. Kernel/cgroup support is required.

To assign more capacity on a large server, edit the host `.env`, then recreate:

```dotenv
# Example for a 1 TiB, 256-logical-CPU host with sufficient free capacity:
LAB_MEMORY_LIMIT=768g
LAB_CPU_LIMIT=192
LAB_PIDS_LIMIT=4096
```

```bash
docker compose up -d --pull always --force-recreate
```

The notebook worker budget must fit *inside* this container allowance. For
example, a 768 GiB container does not support a 768 GiB Dask worker budget plus
Jupyter overhead. Keep `memory_gib=None` to derive the inner budget automatically.
Existing deployments must download the new Compose file **and** pull the new
image to gain all protections. Changing only Python settings cannot set Docker
limits. Direct `docker run` users must provide `--memory`, `--memory-swap` equal
to that memory value, `--cpus`, and `--pids-limit` explicitly.

Open **01-Managed-Analysis.ipynb** for the guarded execution path. Edit
`analysis_settings.py` to select `cpu`, `cupy`, or `torch` and set job budgets:

```python
from labkit import run_analysis

def statistics():
    import numpy as np
    values = np.random.default_rng(42).normal(size=100_000)
    return {'mean': float(values.mean()), 'std': float(values.std())}

result = run_analysis(statistics, backend='cpu', ram_gib=1, threads=1)
```

The runner starts a disposable process, checks its process-tree RSS and live
available RAM every 0.2 seconds, and kills the job's process group on budget
exhaustion, timeout, or interruption. Default timeout is 15 minutes. The worker
also has an expiry timer if its parent kernel disappears. A shared home lock
allows one managed job **or** one managed Dask pool at a time across kernels.
Closing the Dask cluster releases its lock. Separate Docker home volumes have
separate locks, so assign their aggregate host budgets yourself.

Pass dataset paths instead of large in-memory arguments. Transfer input/output
is capped at the smaller of 64 MiB (configurable) and one quarter of the job RAM
budget. Worker-created files, including stderr, have that same per-file limit.
Worker stdout is discarded; return a small summary to the notebook. Large
files should be handled through a separate, deliberately budgeted workflow.

The NVIDIA Compose override exposes **one host GPU**, selected with
`LAB_GPU_DEVICE` (index, GPU UUID, or a host-provisioned MIG device ID).
`device=0` in Python refers to the first device visible *inside* the container.
Install the chosen GPU framework and import it inside the analysis callable.
The worker queries current free/total VRAM on that CUDA device before running
user code, including the actual slice when using MIG. No CPU or alternate-GPU
fallback occurs if the selected backend is unavailable.

GPU jobs default to at most **50% of visible VRAM**, while leaving **1 GiB of
currently free VRAM** outside the requested budget. `vram_gib` can request a
smaller explicit ceiling; `gpu_fraction` cannot exceed 0.80. PyTorch's caching
allocator or CuPy's default memory pool receives the resulting limit before
the callable loads. Return host-side data using `.cpu()` or `.get()`; CUDA
objects cannot be returned to the notebook. Process exit releases its GPU
context and allocations.

**Limits of the protection:** RAM monitoring is sampled and can be outrun by a
rapid allocation. Docker provides the aggregate hard boundary, and an OOM can
still kill a kernel or container. Framework GPU limits exclude CUDA context
overhead and allocations made through other libraries/custom allocators. They
do **not** impose a percentage GPU compute-utilization limit. Use a dedicated
GPU or host-configured MIG slice for GPU resource isolation. Arbitrary notebook
code can bypass Python helpers; this is not a security sandbox or a guarantee
against driver faults, host disk exhaustion, or other applications exhausting
the host. CPU jobs should use chunked datasets; GPU jobs should use batches.

### Optional Dask pool presets

1. Open `00-Setup.ipynb` and run the first cell.
2. Edit `lab_settings.py`, save it, and rerun `%run setup_lab.py`.
3. Optionally set `START_CLUSTER = True` in the notebook to start CPU workers.

The setup writes `hardware_report.json`; startup writes `hardware_inventory.json`.
Neither operation allocates a workload. A cluster starts only when requested.
Run setup in each kernel that needs the settings. Close existing pools before
reconfiguring and recreating them. Apply settings before numerical imports when
possible; supported already-loaded BLAS pools are updated with threadpoolctl.

| Setting | Meaning |
| --- | --- |
| `preset` | `conservative`, `balanced`, or `throughput` |
| `cpu_fraction` | Fraction of effective CPU capacity for worker planning |
| `memory_fraction` | Fraction of effective RAM available to the pool budget |
| `reserve_gib` | Currently available RAM to leave outside the worker budget |
| `memory_gib` | Optional total pool budget in GiB; `None` selects automatically |
| `workers` | Optional explicit process count, validated against the budget |
| `max_workers` | Upper bound on automatically selected processes |
| `threads_per_worker` | Dask threads per process |
| `blas_threads` | Native BLAS/OpenMP thread setting per process |
| `min_worker_gib` | Minimum budget per worker when calculating worker count |
| `gpu_memory_fraction` | Advisory fraction of each reported GPU's total VRAM |

| Preset | CPU fraction | RAM fraction | Worker ceiling | Threads/worker |
| --- | ---: | ---: | ---: | ---: |
| conservative | 0.50 | 0.50 | 16 | 2 |
| balanced | 0.75 | 0.70 | 32 | 2 |
| throughput | 0.95 | 0.80 | 128 | 2 |

Defaults reserve 2 GiB and use one BLAS thread. Explicit overrides win over a
preset. The planner checks its memory budget against both total capacity and
current availability after the reserve. It fails clearly if less than one
worker fits; Jupyter itself still starts on smaller systems. Lower the reserve
and minimum worker budget for a small container.

For example, after assigning 768 GiB and 192 CPUs of a 1 TiB / 256-CPU host
to this container:

```python
SETTINGS = {
    'preset': 'throughput',
    'memory_gib': None,
    'reserve_gib': 32,
    'max_workers': 128,
    'threads_per_worker': 2,
    'blas_threads': 1,
}
```

This plans up to 91 workers with a combined 614.4 GiB memory budget if the
reported available capacity permits it. These are capacity-based presets, not benchmarked
optimal settings. Raise BLAS threads and lower process counts for suitable dense
linear algebra workloads; use processes for Python CPU work that is GIL-bound.
Single-kernel Python code does not automatically become parallel.

## What hardware assessment measures

- `/proc/meminfo` memory totals and current availability, using Python integers
  without a 32-bit size ceiling. Synthetic tests cover 1 TiB and 2 TiB servers.
- Container cgroup v1/v2 memory limits, usage, and CPU quota; visible ancestor
  constraints; CPU affinity, logical CPUs, and physical core/socket topology.
- Per-device NVIDIA name, UUID, driver, total VRAM, and current free VRAM through
  `nvidia-smi`. An unavailable query is distinct from verified absence of a GPU.
- Workspace filesystem capacity and free space.

Docker Desktop reports the resources assigned to its Linux VM, which may be
less than the physical host. Invisible ancestor cgroup constraints cannot be
measured. Host contention and availability change after the snapshot.
MIG slices and framework `CUDA_VISIBLE_DEVICES` may differ from the physical
GPU report. AMD/Intel GPU VRAM assessment is not implemented in this version.

No privileged mode, Docker socket mount, host filesystem mount, or host IPC is
needed. Python knobs cannot increase Docker limits. Configure `--memory`,
`--cpus`, `--cpuset-cpus`, or Compose equivalents on the host when needed.
The default Compose caps are described above. `shm_size=2gb` is a shared-memory
ceiling, not a preallocation; shared-memory use counts against container RAM.

Dask worker memory limits are best effort: target 55%, spill 65%, pause 75%,
and terminate/restart a worker at 85% of its budget. Each pool refreshes its
hardware assessment before starting. Compose directs spill into bounded tmpfs;
outside Compose, the fallback is `work/.dask-spill`, with a 512 MiB managed-spill
budget split across workers. The inventory report's GPU budgets remain advisory;
use `run_analysis` for supported allocator limits. No automatic model batch-size
inference or NUMA placement is performed.

## Included packages and optional GPU frameworks

JupyterLab, IPython kernel, NumPy, Pandas, SciPy, Matplotlib, PyArrow, Dask
Distributed, psutil, threadpoolctl, and ipywidgets. `requirements.lock` pins
the resolved Python dependency versions. `requirements.txt` records intended
version ranges. Build-installed versions are also recorded at
`/opt/build/installed-packages.txt` inside the image.

GPU monitoring works without a bundled CUDA toolkit. Install your chosen
CUDA-enabled framework following its official driver/version instructions.
Framework compute support is not established by a successful `nvidia-smi` query.
For persistent extras use `%pip install --user PACKAGE` in a notebook and restart
the kernel. User-installed versions can override image packages; record them
for reproducibility. The base image provides no compiler or CUDA development
toolkit. Add those in a derived image if your workload requires compilation.

## Operations

- Restart: `docker restart jupyterlab` or `docker compose restart lab`.
- Recover login token: `docker exec jupyterlab cat /home/jovyan/.local/share/labkit/token`.
- Set a token: provide a 16+ character `JUPYTER_TOKEN`, or mount a secret file
  and point `JUPYTER_TOKEN_FILE` to it. Empty tokens are rejected.
- Upgrade: pull a new image, replace the container, and reuse the same volume.
  Starter files are copied only when absent, so user edits are preserved.
  New starter examples remain available at `/opt/lab-starter` inside the image.
- Back up the entire home volume before upgrades. Bind-mounted homes must be
  writable by UID/GID 1000. Named volumes initialize ownership automatically.
- Compose settings are listed in `.env.example`, including CPU/RAM/PID limits
  and the selected NVIDIA device.
- Use an image digest for repeatable deployment instead of the moving `latest`
  tag. The Debian/Python base tag and OS packages are resolved at build time.

## GitHub and GHCR publication

Intended repository: `JustWats/jupyterlabs-server`, default branch `main`.
The supplied workflow runs Python integration tests, builds the image, and
tests container authentication, non-root execution, persistence, and imposed
CPU/RAM limits before a publication job can run. PRs never publish.
Pushes to main publish `latest` and a full commit SHA tag; version tags publish
their own image tags. Publication targets `ghcr.io/<owner>/<repository>`.
The image target is `linux/amd64`; other architectures are not CI-qualified.

No personal access token is stored in this project. GitHub Actions uses its
short-lived `GITHUB_TOKEN` with package-write permission. GitHub may initially
create the GHCR package as private even for a public source repository. Set the
package visibility to public for anonymous pulls; otherwise authenticate to
GHCR with a credential permitted to read packages. Organization/account rules
can prevent Actions or package publishing.

If using another repository name, update the default image reference in Compose
and these examples. The workflow derives its publication name automatically.

## Verification

```bash
python -m pip install -r requirements.lock pytest
python -m pip install --no-deps .
python -m pytest -q
```

With Docker available: `docker build -t lab:test .`, then
`bash scripts/smoke-container.sh lab:test`.

## Primary documentation

- [Docker GPU access](https://docs.docker.com/compose/how-tos/gpu-support/)
- [NVIDIA Container Toolkit installation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Jupyter token authentication](https://jupyter-server.readthedocs.io/en/latest/operators/security.html)
- [Dask worker memory behavior](https://distributed.dask.org/en/stable/worker-memory.html)
- [GitHub container image publication](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GHCR package visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
- [Docker resource limits](https://docs.docker.com/engine/containers/resource_constraints/)
- [PyTorch CUDA allocator implementation](https://github.com/pytorch/pytorch/blob/main/torch/cuda/memory.py)
- [CuPy pool limits and exclusions](https://docs.cupy.dev/en/stable/user_guide/memory.html)
- [NVIDIA MIG isolation](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/introduction.html)
