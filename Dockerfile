################################################################################
# Reckora API — production image
#
# Multi-stage so the runtime layer is small and cache-friendly. Stage 1 (deps)
# installs the locked dependency tree into a venv; stage 2 (runtime) copies the
# venv + source and starts uvicorn against the FastAPI factory.
################################################################################

# ─── stage 1: dependencies ────────────────────────────────────────────────────
FROM python:3.12-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# uv handles the lockfile; the `--no-install-project` pass installs only deps so
# the layer caches across application source changes.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# System libs needed by Pillow (zlib, jpeg), reportlab, Playwright host deps for
# screenshot capture *if* the operator opts into screenshots from the API.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        libjpeg62-turbo \
        zlib1g \
        libfreetype6 \
        libpng16-16 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ─── stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    RECKORA_DB_PATH=/data/reckora.db \
    RECKORA_API_SCREENSHOTS_DIR=/data/screenshots \
    RECKORA_API_SCREENSHOTS_URL_PREFIX=/screenshots

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        libjpeg62-turbo \
        zlib1g \
        libfreetype6 \
        libpng16-16 \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1000 reckora \
 && useradd  --system --uid 1000 --gid reckora --home-dir /app reckora \
 && mkdir -p /data /data/screenshots \
 && chown -R reckora:reckora /app /data

COPY --from=deps /opt/venv /opt/venv
COPY --chown=reckora:reckora pyproject.toml README.md LICENSE ./
COPY --chown=reckora:reckora src ./src
COPY --chown=reckora:reckora apps/api ./apps/api

# Install the project itself (editable-equivalent) so the ``reckora`` and
# ``reckora-api`` console scripts land on PATH.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /usr/local/bin/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python --no-deps .

USER reckora

EXPOSE 8000

# tini reaps zombie processes (matters for Playwright subprocess churn) and
# keeps SIGTERM handling sane on docker stop.
ENTRYPOINT ["tini", "--"]

# uvicorn against the FastAPI factory. Bind to all interfaces inside the
# container; only the reverse proxy (Caddy) is exposed to the public network.
CMD ["uvicorn", "reckora_api.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"
