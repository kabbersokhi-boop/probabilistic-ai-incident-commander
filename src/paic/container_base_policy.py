from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any, cast

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
FROM_RE = re.compile(
    r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>[A-Za-z0-9._-]+))?\s*$",
    re.IGNORECASE,
)


class BasePolicyError(ValueError):
    """Raised when the container base policy or Dockerfile is invalid."""


def _require_regular_file(path: Path, *, context: str) -> None:
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise BasePolicyError(f"cannot inspect {context} {path}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise BasePolicyError(f"{context} must be a regular file: {path}")


def _read_text(path: Path, *, context: str) -> str:
    _require_regular_file(path, context=context)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BasePolicyError(f"cannot read {context} {path}: {exc}") from exc


def _read_policy(path: Path) -> dict[str, Any]:
    text = _read_text(path, context="policy")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise BasePolicyError(f"invalid policy JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BasePolicyError("policy must be a JSON object")
    allowed = {
        "schema_version",
        "registry",
        "repository",
        "python_series",
        "variant",
        "external_stage",
        "internal_stages",
    }
    unexpected = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if unexpected or missing:
        raise BasePolicyError(f"policy keys mismatch: missing={missing}, unexpected={unexpected}")
    if value["schema_version"] != 1:
        raise BasePolicyError("policy schema_version must be 1")
    for key in (
        "registry",
        "repository",
        "python_series",
        "variant",
        "external_stage",
    ):
        if not isinstance(value[key], str) or not value[key]:
            raise BasePolicyError(f"policy {key} must be a non-empty string")
    stages = value["internal_stages"]
    if (
        not isinstance(stages, list)
        or not stages
        or any(not isinstance(item, str) or not item for item in stages)
        or len(set(stages)) != len(stages)
    ):
        raise BasePolicyError("policy internal_stages must be unique non-empty strings")
    if value["external_stage"] in stages:
        raise BasePolicyError("external_stage must not appear in internal_stages")
    return cast(dict[str, Any], value)


def _from_instructions(dockerfile: str) -> list[tuple[str, str | None]]:
    instructions: list[tuple[str, str | None]] = []
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("FROM "):
            match = FROM_RE.fullmatch(line)
            if match is None:
                raise BasePolicyError(f"malformed FROM instruction: {line}")
            instructions.append((match.group("image"), match.group("stage")))
    if not instructions:
        raise BasePolicyError("Dockerfile must contain FROM instructions")
    return instructions


def validate_base_policy(*, dockerfile_path: Path, policy_path: Path) -> dict[str, Any]:
    dockerfile = _read_text(dockerfile_path, context="Dockerfile")
    if re.search(r"^\s*ARG\s+[^\n]*IMAGE", dockerfile, flags=re.MULTILINE | re.IGNORECASE):
        raise BasePolicyError("external base image must not use build-argument indirection")
    policy = _read_policy(policy_path)
    instructions = _from_instructions(dockerfile)

    expected_external_stage = cast(str, policy["external_stage"])
    expected_internal = cast(list[str], policy["internal_stages"])
    stage_names = [stage for _, stage in instructions]
    if any(stage is None for stage in stage_names):
        raise BasePolicyError("every FROM instruction must name a stage")
    named_stages = cast(list[str], stage_names)
    if len(set(named_stages)) != len(named_stages):
        raise BasePolicyError("Dockerfile stage names must be unique")
    if named_stages != [expected_external_stage, *expected_internal]:
        raise BasePolicyError(
            "Dockerfile stage order must match policy: "
            f"{[expected_external_stage, *expected_internal]}"
        )

    external_image, _ = instructions[0]
    if external_image.startswith("$") or "${" in external_image:
        raise BasePolicyError("external base image must be direct, not interpolated")
    if "@" not in external_image:
        raise BasePolicyError("external base image must be digest-pinned")
    image_ref, digest = external_image.rsplit("@", 1)
    if DIGEST_RE.fullmatch(digest) is None:
        raise BasePolicyError(
            "external base digest must be lowercase sha256 with 64 hex characters"
        )

    expected_prefix = f"{policy['registry']}/{policy['repository']}:"
    if not image_ref.startswith(expected_prefix):
        raise BasePolicyError(f"external base image must start with {expected_prefix}")
    tag = image_ref[len(expected_prefix) :]
    expected_tag = f"{policy['python_series']}-{policy['variant']}"
    if tag != expected_tag:
        raise BasePolicyError(f"external base tag must be exactly {expected_tag}")

    for (image, stage), expected_stage in zip(instructions[1:], expected_internal, strict=True):
        if image != expected_external_stage or stage != expected_stage:
            raise BasePolicyError(
                f"internal stage {expected_stage} must derive directly from {expected_external_stage}"
            )

    return {
        "schema_version": 1,
        "base": {
            "image": image_ref,
            "tag": tag,
            "digest": digest,
            "external_stage": expected_external_stage,
        },
        "policy": {
            "python_series": policy["python_series"],
            "variant": policy["variant"],
            "internal_stages": expected_internal,
        },
        "stage_graph": [
            {"stage": expected_external_stage, "source": external_image},
            *[{"stage": stage, "source": expected_external_stage} for stage in expected_internal],
        ],
    }


def _write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise BasePolicyError(f"cannot write evidence {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the pinned container base policy")
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = validate_base_policy(
            dockerfile_path=args.dockerfile,
            policy_path=args.policy,
        )
        _write_evidence(args.output, evidence)
    except BasePolicyError as exc:
        print(f"container base policy validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
