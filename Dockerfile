# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip build \
    && python -m build --wheel --outdir /wheels \
    && python -m pip wheel --wheel-dir /wheels /wheels/probabilistic_ai_incident_commander-*.whl

FROM ${PYTHON_IMAGE} AS runtime

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

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels probabilistic-ai-incident-commander==${VERSION} \
    && rm -rf /wheels

WORKDIR /opt/paic
COPY --chown=root:root specs ./specs
COPY --chown=root:root configs ./configs
COPY --chown=root:root schemas ./schemas

USER 10001:10001

ENTRYPOINT ["paic"]
CMD ["summary", "--spec-dir", "/opt/paic/specs"]
