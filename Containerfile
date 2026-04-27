# syntax=docker/dockerfile:1.7

# ---- stage 1: build frontend assets ----
# Pinned to $BUILDPLATFORM (the runner's native arch) so multi-arch builds
# don't run npm under QEMU emulation — that combination has been observed
# to hang for >5 minutes on linux/arm64 in CI. The output of this stage is
# a directory of static JS/CSS/PNG files that are byte-identical regardless
# of build host architecture, so the COPY --from=assets in the runtime
# stage works unchanged for every target platform.
FROM --platform=$BUILDPLATFORM docker.io/library/node:22-alpine AS assets
WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY scripts/build-assets.mjs ./scripts/build-assets.mjs
RUN mkdir -p cptv/static && node scripts/build-assets.mjs

# ---- stage 2: resolve python deps ----
# WORKDIR matches the runtime path so console-script shebangs (e.g.
# /app/.venv/bin/uvicorn) point at a path that exists in the runtime
# image. Otherwise they bake in the build-stage path and `exec uvicorn`
# fails at startup with "No such file or directory".
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS pydeps
WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- stage 3: runtime ----
FROM docker.io/library/python:3.12-slim-bookworm AS runtime

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        mtr-tiny \
        libcap2-bin \
        ca-certificates \
        tini \
        iproute2 \
        iputils-ping \
        curl \
    && setcap cap_net_raw+ep /usr/bin/mtr-packet \
    && apt-get purge -y --auto-remove libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

# The base image ships pip 25.0.1, which has CVE-2025-8869 / CVE-2026-1703.
# We run uvicorn from the uv-managed venv, so pip isn't used at runtime — but
# trivy still scans the system site-packages. Upgrade to a fixed version.
# hadolint ignore=DL3013
RUN python3 -m pip install --no-cache-dir --upgrade 'pip>=25.3' \
    && useradd --system --create-home --uid 10001 cptv
WORKDIR /app

COPY --from=pydeps /app/.venv /app/.venv
COPY --from=assets /build/cptv/static /app/cptv/static
COPY cptv/ /app/cptv/
COPY pyproject.toml /app/pyproject.toml

# GeoLite2 databases are NOT baked into the image (MaxMind EULA prohibits
# redistribution in public images). Mount them at runtime via a volume —
# see README.md for the systemd timer that keeps them fresh.

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    CPTV_GEOIP_CITY_DB=/app/vendor/geolite2/GeoLite2-City.mmdb \
    CPTV_GEOIP_ASN_DB=/app/vendor/geolite2/GeoLite2-ASN.mmdb

USER cptv
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "cptv.main:app", "--host", "0.0.0.0", "--port", "8000"]
