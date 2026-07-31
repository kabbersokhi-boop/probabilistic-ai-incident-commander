from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.container_base_policy import BasePolicyError, validate_base_policy

DIGEST = "sha256:" + "a" * 64


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "registry": "docker.io",
        "repository": "library/python",
        "python_series": "3.12.13",
        "variant": "slim-bookworm",
        "external_stage": "python-base",
        "internal_stages": ["builder", "runtime"],
    }


def _dockerfile() -> str:
    return (
        f"FROM docker.io/library/python:3.12.13-slim-bookworm@{DIGEST} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM python-base AS runtime\n"
    )


def _write(
    tmp_path: Path,
    dockerfile: str | None = None,
    policy: object | None = None,
) -> tuple[Path, Path]:
    dockerfile_path = tmp_path / "Dockerfile"
    policy_path = tmp_path / "policy.json"
    dockerfile_path.write_text(dockerfile or _dockerfile(), encoding="utf-8")
    policy_path.write_text(
        json.dumps(_policy() if policy is None else policy),
        encoding="utf-8",
    )
    return dockerfile_path, policy_path


def test_valid_policy_is_deterministic(tmp_path: Path) -> None:
    dockerfile, policy = _write(tmp_path)
    first = validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    second = validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    assert first == second
    assert first["base"]["digest"] == DIGEST


@pytest.mark.parametrize(
    "image",
    [
        "docker.io/library/python:3.12.13-slim-bookworm",
        "docker.io/library/python:3.12.13-slim-bookworm@sha256:abc",
        "docker.io/library/python:3.12.13-slim-bookworm@sha256:" + "A" * 64,
    ],
)
def test_rejects_missing_or_incomplete_digest(tmp_path: Path, image: str) -> None:
    dockerfile, policy = _write(
        tmp_path,
        f"FROM {image} AS python-base\nFROM python-base AS builder\nFROM python-base AS runtime\n",
    )
    with pytest.raises(BasePolicyError):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


@pytest.mark.parametrize("tag", ["3.13.0-slim-bookworm", "3.12.13-slim-trixie"])
def test_rejects_wrong_series_or_variant(tmp_path: Path, tag: str) -> None:
    dockerfile, policy = _write(
        tmp_path,
        f"FROM docker.io/library/python:{tag}@{DIGEST} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM python-base AS runtime\n",
    )
    with pytest.raises(BasePolicyError):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


def test_rejects_build_argument_indirection(tmp_path: Path) -> None:
    dockerfile, policy = _write(
        tmp_path,
        f"ARG PYTHON_IMAGE=docker.io/library/python:3.12.13-slim-bookworm@{DIGEST}\n"
        "FROM ${PYTHON_IMAGE} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM python-base AS runtime\n",
    )
    with pytest.raises(BasePolicyError, match="indirection"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


def test_rejects_unexpected_stage_graph(tmp_path: Path) -> None:
    dockerfile, policy = _write(
        tmp_path,
        f"FROM docker.io/library/python:3.12.13-slim-bookworm@{DIGEST} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM alpine:3 AS helper\n"
        "FROM python-base AS runtime\n",
    )
    with pytest.raises(BasePolicyError):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


def test_rejects_overbroad_policy(tmp_path: Path) -> None:
    policy = _policy()
    policy["allow_any_digest"] = True
    dockerfile, policy_path = _write(tmp_path, policy=policy)
    with pytest.raises(BasePolicyError, match="keys mismatch"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)
