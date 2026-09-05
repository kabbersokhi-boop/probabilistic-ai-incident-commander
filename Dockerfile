# syntax=docker/dockerfile:1.7

FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS python-base

# DLA-4726-1: the digest-pinned Python base still carries the superseded
# Bookworm ca-certificates package.  Download the exact Debian security update
# and verify its published checksum before installing it in both build stages.
ARG CA_CERTIFICATES_VERSION=20250419~deb12u1
ARG CA_CERTIFICATES_SHA256=62b08a77d985d4253894b1f69aebda5925034ca4e294add364167fad8cb64a44
ARG LIBPCRE2_VERSION=10.42-1+deb12u1
ARG LIBPCRE2_SHA256=81c5502941118a24d47af69a17b8b0b9548d75cc6d72b3eb3fe01047b46fa10e
RUN apt-get update \
    && apt-get download "ca-certificates=${CA_CERTIFICATES_VERSION}" \
    && apt-get download "libpcre2-8-0=${LIBPCRE2_VERSION}" \
    && echo "${CA_CERTIFICATES_SHA256}  ca-certificates_${CA_CERTIFICATES_VERSION}_all.deb" \
        | sha256sum --check --strict \
    && echo "${LIBPCRE2_SHA256}  libpcre2-8-0_${LIBPCRE2_VERSION}_amd64.deb" \
        | sha256sum --check --strict \
    && dpkg --install \
        "ca-certificates_${CA_CERTIFICATES_VERSION}_all.deb" \
        "libpcre2-8-0_${LIBPCRE2_VERSION}_amd64.deb" \
    && rm -f \
        "ca-certificates_${CA_CERTIFICATES_VERSION}_all.deb" \
        "libpcre2-8-0_${LIBPCRE2_VERSION}_amd64.deb" \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY requirements.lock requirements-build.lock ./
COPY src ./src

RUN mkdir -p /build-wheels /runtime-wheels \
    && python -m pip wheel --require-hashes --no-deps --wheel-dir /build-wheels -r requirements-build.lock \
    && python -m pip wheel --require-hashes --no-deps --wheel-dir /runtime-wheels -r requirements.lock \
    && python -m pip install --no-index --no-cache-dir --require-hashes --no-deps \
         --find-links=/build-wheels -r requirements-build.lock \
    && python -m build --no-isolation --wheel --outdir /runtime-wheels \
    && test "$(find /runtime-wheels -maxdepth 1 -type f -name 'probabilistic_ai_incident_commander-*.whl' | wc -l)" = 1

FROM python-base AS runtime

ARG VCS_REF=unknown
ARG VERSION=0.12.0

LABEL org.opencontainers.image.title="Probabilistic AI Incident Commander" \
      org.opencontainers.image.description="Evidence-grounded incident investigation and governed remediation CLI" \
      org.opencontainers.image.source="https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV HOME=/home/paic \
    PATH=/home/paic/.local/bin:${PATH} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

RUN groupadd --gid 10001 paic \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/paic --shell /usr/sbin/nologin paic

COPY --from=builder /runtime-wheels /wheels
COPY requirements.lock /build-requirements.lock
RUN python -m pip install --no-index --no-cache-dir --require-hashes --no-deps \
         --find-links=/wheels -r /build-requirements.lock \
    && python -m pip install --no-index --no-cache-dir --no-deps \
         /wheels/probabilistic_ai_incident_commander-"${VERSION}"-*.whl \
    && rm -rf /wheels /build-requirements.lock

WORKDIR /opt/paic
COPY --chown=root:root specs ./specs
COPY --chown=root:root configs ./configs
COPY --chown=root:root schemas ./schemas

USER 10001:10001

ENTRYPOINT ["paic"]
CMD ["summary", "--spec-dir", "/opt/paic/specs"]
