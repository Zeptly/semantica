# syntax=docker/dockerfile:1
# zeptly-semantica-api — production image.
#
# Build context is this directory (Railway "Root Directory" = zeptly-semantica-api).

ARG PYTHON_IMAGE=python:3.12-slim

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
# Optional build secret "build_ca": an extra CA bundle for builds behind a
# TLS-intercepting proxy. Never written to any image layer; absent on Railway.
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
 && pip install --no-deps --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
 && python -c "import importlib.metadata as m; assert m.version('semantica') == '0.6.0'" \
 && python -c "from semantica.graph_store import GraphStore; import falkordb, fastapi, uvicorn" \
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

RUN groupadd --system --gid 10001 semantica \
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

# Deterministic start: validates config, then serves on 0.0.0.0:${PORT}.
ENTRYPOINT ["python", "-m", "app"]
