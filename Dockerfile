# Build output is versioned by commit SHA in GHCR. Pin the digest when deploying.
FROM python:3.12-slim-bookworm
ARG SOURCE_URL=https://github.com/JustWats/jupyterlabs-server
LABEL org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.title="JupyterLab Server" \
      org.opencontainers.image.description="JupyterLab with container-aware hardware assessment and Python presets"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LAB_WORKSPACE=/home/jovyan/work \
    PATH=/home/jovyan/.local/bin:$PATH \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends tini git ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/bash jovyan
COPY requirements.lock /opt/build/requirements.lock
RUN pip install --no-cache-dir -r /opt/build/requirements.lock \
    && pip freeze > /opt/build/installed-packages.txt
COPY pyproject.toml /opt/labkit/pyproject.toml
COPY labkit /opt/labkit/labkit
RUN pip install --no-cache-dir --no-deps /opt/labkit
COPY starter /opt/lab-starter
COPY jupyter_server_config.py /opt/lab-config/jupyter_server_config.py
RUN mkdir -p /home/jovyan/work && chown -R 1000:1000 /home/jovyan
USER 1000:1000
WORKDIR /home/jovyan/work
EXPOSE 8888
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/login', timeout=3).close()"]
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "labkit.launch"]
