# Validation record

## Resource protection qualification (0.2.0)

[Run 35032593977](https://github.com/JustWats/jupyterlabs-server/actions/runs/35032593977)
qualified source commit `d8ac315ccd869df96ceff1631a09800d24fc6cd7`:

- Full Python suite: **43 passed**, including managed-job timeout, RAM budget
  exhaustion, subsequent-job recovery, and mutual exclusion with Dask pools.
- All four Compose configurations passed validation.
- Docker image built and actual Compose deployment passed health/authentication
  and persistence checks, including assertions on 4 GiB RAM, 2 CPUs, disabled
  swap, 1,024 PIDs, and a bounded spill tmpfs.
- An oversized managed analysis process was stopped; a subsequent job returned
  successfully and the authenticated Jupyter HTTP endpoint remained responsive.
- GPU selection, live-free-memory budgeting, and PyTorch/CuPy allocator API calls
  were tested with simulated devices. No physical GPU execution test was run.

The resource protections reduce host exhaustion risk. They do not isolate GPU
compute percentages or guarantee immunity to host disk exhaustion, driver
faults, unrelated host workloads, or arbitrary code bypassing Python helpers.
See README.md for hard Docker limits versus sampled/framework-level guards.

## GitHub CI qualification

[Run 34970151332](https://github.com/JustWats/jupyterlabs-server/actions/runs/34970151332)
qualified source commit `535e5736c4a11660dffeb164ef1a39e0947db7a2` on
2026-09-15 using an Ubuntu GitHub-hosted runner:

- Full Python suite: **27 passed**, including a real Dask worker and execution
  through a separate Jupyter kernel.
- Docker Compose CPU and NVIDIA configurations: passed schema validation.
- Docker image build: passed.
- GHCR publication: passed. Published `latest` and the full source-commit tag.
- Published image digest: `sha256:404267c4750730cfa35b6d64a1f2ad8bbe6b977d5babca2af13f22e43eae2d3f`.
- Container smoke: passed health, UID 1000 execution, token authentication,
  enforced 4 GiB / 2 CPU limits, and persistent settings across restart.

Physical NVIDIA GPU execution and 1 TiB physical hardware remain untested.
Large-memory and GPU inventory cases use synthetic fixtures as detailed below.

The following sections preserve the earlier local runtime results; their
process and Docker limitations were resolved by the CI qualification above.

Date: 2026-09-15. Python 3.12, Linux x86-64 development runtime.

## Passed

`python -m pytest -q -m 'not process_integration'`

Result: **25 passed, 2 deselected**.

Coverage includes:

- Capacity planning for 8 GiB, 64 GiB, 1 TiB, and 2 TiB configurations, up to
  512 effective CPUs. Large-server tests use synthetic inventory fixtures;
  they do not claim physical testing on those machines.
- v1/v2 cgroup parsing, visible ancestor memory headroom and quota limits,
  fractional CPU quotas, busy-host available memory, and unlimited sentinels.
- Invalid options and over-budget explicit worker/memory choices.
- Simulated NVIDIA multi-device VRAM, unavailable utilities, and query timeouts.
- Starter-file initialization preserving edits.
- All setup notebook cells executed in an in-process IPython shell.
- Actual JupyterLab HTTP startup with unauthenticated contents denied and
  authenticated contents access accepted.

Dependency consistency (`pip check`), YAML parsing, shell syntax, and Git
whitespace checks also passed. The resolved dependencies are in requirements.lock.

## Not qualified in this runtime

The two process integration tests were attempted and failed here:

- A Dask spawned worker failed when psutil reported `NoSuchProcess` for its
  own process PID during import. A normal directly launched child process could
  initialize psutil, but the Dask spawned-worker path did not.
- The separate Jupyter kernel failed during ZeroMQ initialization with
  `Operation not permitted`; local network interface discovery also reported
  that error. In-process notebook execution succeeded.

These tests remain enabled in the default full suite and the GitHub Actions
workflow. They have not been changed to skip automatically or report success.

Docker is not installed in this development runtime. The Dockerfile has not
been built here, and container-specific health, persistence, non-root execution,
GPU passthrough, and enforced Docker CPU/RAM limit tests have not run. The
workflow requires its container smoke test before publication. No physical
GPU computation or VRAM allocation test has been performed.

## Publication

Repository: https://github.com/JustWats/jupyterlabs-server

The results above record local pre-publication validation. The GitHub Actions
workflow requires the complete test suite and container smoke test before
publishing an image. Check the repository Actions page for current CI results
and publication status.
