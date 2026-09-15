# Validation record

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
