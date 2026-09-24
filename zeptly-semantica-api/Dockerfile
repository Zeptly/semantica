# syntax=docker/dockerfile:1
# zeptly-semantica-api — production image.
#
# Build context is this directory (Railway "Root Directory" = zeptly-semantica-api).

# Pinned by digest (python 3.12.14, Debian slim). Update deliberately: pull
# the new tag, record its digest here, rebuild, re-run the test suite.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

# ---------------------------------------------------------------------------
# Builder: install hash-pinned dependencies into an isolated virtualenv.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.lock /tmp/requirements.lock
# --require-hashes: every artefact (including semantica==0.6.0) is verified.
# --no-deps: the lock is the complete closure; semantica's declared ML
#   dependencies are intentionally excluded (see scripts/lock.sh).
# --only-binary: wheels only, so no compiler toolchain is needed.
RUN pip install --no-deps --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
 && python -c "import importlib.metadata as m; assert m.version('semantica') == '0.6.0'" \
 && python -c "from semantica.graph_store import GraphStore; import falkordb, fastapi, uvicorn" \
 && python -m pip uninstall --yes --quiet pip \
 && rm -f /opt/venv/bin/semantica* /opt/venv/bin/fastapi /opt/venv/bin/activate* /opt/venv/bin/Activate.ps1 \
 && find /opt/venv -name '__pycache__' -prune -exec rm -rf {} +

COPY app /opt/service/app
# Pre-compile bytecode so the runtime never needs to write .pyc files.
RUN python -m compileall -q /opt/venv/lib /opt/service/app

# ---------------------------------------------------------------------------
# Runtime: slim Python, no build tools, non-root, read-only-friendly.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/service \
    SEMANTICA_ENV=production \
    PORT=8080

# Remove the base image's installers: nothing is installed at runtime.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* \
           /usr/local/lib/python3.12/site-packages/setuptools* \
           /usr/local/lib/python3.12/site-packages/wheel* \
           /usr/local/bin/pip* /usr/local/bin/idle* /usr/local/bin/pydoc* \
 && groupadd --system --gid 10001 semantica \
 && useradd --system --uid 10001 --gid semantica --no-create-home \
      --home-dir /nonexistent --shell /usr/sbin/nologin semantica

# Owned by root and not writable by the runtime user.
COPY --from=builder --chown=root:root /opt/venv /opt/venv
COPY --from=builder --chown=root:root /opt/service /opt/service

WORKDIR /opt/service
USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'), timeout=3)"]

# Deterministic start: validates config, then serves on [::]:${PORT}
# (dual-stack; falls back to 0.0.0.0 if IPv6 is unavailable).
ENTRYPOINT ["python", "-m", "app"]
