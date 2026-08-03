# syntax=docker/dockerfile:1.7

FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS python-base

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
