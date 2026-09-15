# JupyterLab Server

A single-user JupyterLab container with hardware assessment, editable Python
presets, a setup notebook, and an optional local Dask CPU worker pool.

## Start in two commands

Requires Docker on a Linux x86-64 host. Once the GitHub Actions image publication
has completed and the GHCR package is public:

```bash
docker run -d --name jupyterlab --restart unless-stopped -p 127.0.0.1:8888:8888 --shm-size=2g -v jupyterlab-home:/home/jovyan ghcr.io/justwats/jupyterlabs-server:latest
docker logs jupyterlab
```

Open the token URL from the logs, using `http://localhost:8888/lab` as the address.
The first command pulls the image automatically. A fresh volume receives
`00-Setup.ipynb`, `lab_settings.py`, and `setup_lab.py`. Settings, notebooks,
the generated token, and user-installed Python packages persist in the volume.
Never delete that volume unless you intend to delete its contents.

For NVIDIA GPU access, add `--gpus all` to the first command. The host needs
a working NVIDIA driver and NVIDIA Container Toolkit configured for Docker.
The container cannot install host drivers or expose devices to itself.

For a remote server, establish `ssh -L 8888:127.0.0.1:8888 USER@SERVER` from
your workstation, then use localhost in your browser. Publishing to localhost
keeps the notebook off the public network. Token authentication is enabled.
Do not share the token. This is a single-user server, not JupyterHub.

## Start from source before image publication

Inside this downloaded project directory:

```bash
docker compose up -d --build
docker compose logs lab
```

For NVIDIA, use `docker compose -f compose.yaml -f compose.gpu.yaml up -d --build`
instead of the first command. After image publication, use `up -d --pull always`
instead of `--build`. Compose requires Docker Compose v2.

## Configuration inside Jupyter

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

For example, on an otherwise idle 1 TiB machine with 256 effective logical CPUs:

```python
SETTINGS = {
    'preset': 'throughput',
    'memory_gib': 768,
    'reserve_gib': 32,
    'max_workers': 128,
    'threads_per_worker': 2,
    'blas_threads': 1,
}
```

This plans 121 workers with a combined 768 GiB memory budget if the reported
available capacity permits it. These are capacity-based presets, not benchmarked
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
The default container has no explicit Docker CPU/RAM cap. `--shm-size=2g` is a
shared-memory ceiling, not a preallocation or the container's total RAM limit.

Dask worker memory limits are best effort, using Dask's spill/pause/restart
behavior; they are not a hard cap on an arbitrary notebook process. Spill uses
`work/.dask-spill` and consumes disk. Separate clusters/kernels can oversubscribe
one another. GPU budgets do not reserve VRAM, change framework allocators, or
infer a safe model batch size. This package does not perform NUMA placement.

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
- Compose accepts `LAB_IMAGE`, `LAB_PORT`, `LAB_BIND`, and `LAB_SHM_SIZE`.
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
