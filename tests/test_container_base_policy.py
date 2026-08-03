from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.container_base_policy import BasePolicyError, main, validate_base_policy

DIGEST = "sha256:" + "a" * 64


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "registry": "docker.io",
        "repository": "library/python",
        "python_series": "3.12",
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


def _assert_rejected(tmp_path: Path, dockerfile_text: str) -> None:
    dockerfile, policy = _write(tmp_path, dockerfile_text)
    with pytest.raises(BasePolicyError):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


def test_valid_policy_is_deterministic(tmp_path: Path) -> None:
    dockerfile, policy = _write(tmp_path)
    first = validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    second = validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    assert first == second
    assert first["base"]["digest"] == DIGEST
    assert first["base"]["python_version"] == "3.12.13"
    assert first["policy"]["python_series"] == "3.12"


def test_accepts_reviewed_patch_refresh_within_series(tmp_path: Path) -> None:
    dockerfile, policy = _write(
        tmp_path,
        f"FROM docker.io/library/python:3.12.14-slim-bookworm@{DIGEST} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM python-base AS runtime\n",
    )
    evidence = validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    assert evidence["base"]["python_version"] == "3.12.14"


def test_rejects_missing_or_incomplete_digest(tmp_path: Path) -> None:
    images = (
        "docker.io/library/python:3.12.13-slim-bookworm",
        "docker.io/library/python:3.12.13-slim-bookworm@sha256:abc",
        "docker.io/library/python:3.12.13-slim-bookworm@sha256:" + "A" * 64,
    )
    for image in images:
        _assert_rejected(
            tmp_path,
            f"FROM {image} AS python-base\n"
            "FROM python-base AS builder\n"
            "FROM python-base AS runtime\n",
        )


def test_rejects_wrong_series_or_variant(tmp_path: Path) -> None:
    tags = ("3.13.0-slim-bookworm", "3.12.13-slim-trixie", "3.12-slim-bookworm")
    for tag in tags:
        _assert_rejected(
            tmp_path,
            f"FROM docker.io/library/python:{tag}@{DIGEST} AS python-base\n"
            "FROM python-base AS builder\n"
            "FROM python-base AS runtime\n",
        )


def test_rejects_build_argument_indirection(tmp_path: Path) -> None:
    dockerfile, policy = _write(
        tmp_path,
        f"ARG PYTHON_IMAGE=docker.io/library/python:3.12.13-slim-bookworm@{DIGEST}\n"
        "FROM ${PYTHON_IMAGE} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM python-base AS runtime\n",
    )
    with pytest.raises(BasePolicyError, match="direct"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


def test_rejects_unexpected_stage_graph(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        f"FROM docker.io/library/python:3.12.13-slim-bookworm@{DIGEST} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM alpine:3 AS helper\n"
        "FROM python-base AS runtime\n",
    )


def test_rejects_duplicate_stage_names(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        f"FROM docker.io/library/python:3.12.13-slim-bookworm@{DIGEST} AS python-base\n"
        "FROM python-base AS builder\n"
        "FROM python-base AS builder\n",
    )


def test_rejects_overbroad_policy(tmp_path: Path) -> None:
    policy = _policy()
    policy["allow_any_digest"] = True
    dockerfile, policy_path = _write(tmp_path, policy=policy)
    with pytest.raises(BasePolicyError, match="keys mismatch"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)


def test_rejects_malformed_policy_values(tmp_path: Path) -> None:
    for key, value in (
        ("python_series", "3.12.13"),
        ("variant", "slim bookworm"),
        ("repository", "Library/Python"),
        ("external_stage", "python base"),
    ):
        policy = _policy()
        policy[key] = value
        dockerfile, policy_path = _write(tmp_path, policy=policy)
        with pytest.raises(BasePolicyError, match=f"policy {key}"):
            validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)


def test_rejects_non_regular_inputs(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    dockerfile_directory = tmp_path / "Dockerfile"
    dockerfile_directory.mkdir()
    with pytest.raises(BasePolicyError, match="regular file"):
        validate_base_policy(
            dockerfile_path=dockerfile_directory,
            policy_path=policy_path,
        )


def test_rejects_malformed_policy_documents(tmp_path: Path) -> None:
    dockerfile, policy_path = _write(tmp_path)
    cases = (
        ("not-json", "invalid policy JSON"),
        ("[]", "policy must be a JSON object"),
    )
    for body, message in cases:
        policy_path.write_text(body, encoding="utf-8")
        with pytest.raises(BasePolicyError, match=message):
            validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)

    policy = _policy()
    policy.pop("variant")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(BasePolicyError, match="keys mismatch"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)

    for value, message in (
        (2, "schema_version"),
        ([], "internal_stages"),
    ):
        policy = _policy()
        policy["schema_version" if value == 2 else "internal_stages"] = value
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with pytest.raises(BasePolicyError, match=message):
            validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)

    policy = _policy()
    policy["internal_stages"] = ["builder", "builder"]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(BasePolicyError, match="unique"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)

    policy = _policy()
    policy["internal_stages"] = ["builder", "python-base"]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(BasePolicyError, match="external_stage"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy_path)


def test_rejects_malformed_and_unexpected_dockerfile_graphs(tmp_path: Path) -> None:
    dockerfile, policy = _write(tmp_path, "FROM not-a-valid-instruction extra\n")
    with pytest.raises(BasePolicyError, match="malformed FROM"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    dockerfile.write_text("# comment\n", encoding="utf-8")
    with pytest.raises(BasePolicyError, match="contain FROM"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    dockerfile.write_text("FROM python:3.12\n", encoding="utf-8")
    with pytest.raises(BasePolicyError, match="every FROM"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)
    dockerfile.write_text(
        f"FROM docker.io/library/python:3.12.13-slim-bookworm@{DIGEST} AS python-base\n"
        "FROM alpine:3 AS builder\n"
        "FROM python-base AS runtime\n",
        encoding="utf-8",
    )
    with pytest.raises(BasePolicyError, match="derive directly"):
        validate_base_policy(dockerfile_path=dockerfile, policy_path=policy)


def test_cli_writes_evidence_and_returns_failure_for_invalid_input(tmp_path: Path) -> None:
    dockerfile, policy = _write(tmp_path)
    output = tmp_path / "evidence.json"
    assert (
        main(["--dockerfile", str(dockerfile), "--policy", str(policy), "--output", str(output)])
        == 0
    )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["base"]["digest"] == DIGEST
    assert (
        main(
            [
                "--dockerfile",
                str(tmp_path / "missing"),
                "--policy",
                str(policy),
                "--output",
                str(output),
            ]
        )
        == 1
    )
