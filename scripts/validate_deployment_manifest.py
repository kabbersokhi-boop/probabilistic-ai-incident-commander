"""Validate the static deployment and observability policy boundary."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class DeploymentPolicyError(ValueError):
    """Raised when a deployment policy is unsafe or incomplete."""


_POLICY_KEYS = {
    "schema_version",
    "target",
    "artifact",
    "promotion",
    "rollback",
    "headers",
    "observability",
}
_REQUIRED_HEADERS = {
    "Content-Security-Policy",
    "Cross-Origin-Opener-Policy",
    "Permissions-Policy",
    "Referrer-Policy",
    "X-Content-Type-Options",
}
_REQUIRED_ARTIFACT_FILES = {"SHA256SUMS", "bundle.json", "manifest.json"}


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentPolicyError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeploymentPolicyError(f"{path} must contain an object")
    return payload


def _require_exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise DeploymentPolicyError(f"{label} has unexpected or missing fields")
    return value


def validate_policy(policy: dict[str, Any], observability: dict[str, Any], root: Path) -> None:
    if set(policy) != _POLICY_KEYS or policy.get("schema_version") != "1.0":
        raise DeploymentPolicyError("deployment policy has an invalid schema")
    if policy.get("target") != "immutable-static-host":
        raise DeploymentPolicyError("deployment target must be immutable-static-host")

    artifact = _require_exact_keys(
        policy["artifact"],
        {
            "bundle_kind",
            "required_files",
            "checksum_algorithm",
            "require_manifest_validation",
            "require_public_scan",
            "require_source_commit",
            "require_lock_digest",
            "require_provenance",
        },
        "artifact policy",
    )
    if artifact["bundle_kind"] != "paic-public-demo":
        raise DeploymentPolicyError("artifact bundle kind is not the approved public demo")
    if (
        not isinstance(artifact["required_files"], list)
        or not all(isinstance(item, str) for item in artifact["required_files"])
        or set(artifact["required_files"]) != _REQUIRED_ARTIFACT_FILES
    ):
        raise DeploymentPolicyError("artifact file set is not closed-world")
    if artifact["checksum_algorithm"] != "sha256" or not all(
        artifact[key] is True
        for key in (
            "require_manifest_validation",
            "require_public_scan",
            "require_source_commit",
            "require_lock_digest",
            "require_provenance",
        )
    ):
        raise DeploymentPolicyError("artifact integrity requirements are incomplete")

    promotion = _require_exact_keys(
        policy["promotion"],
        {
            "accept_only_validated_artifacts",
            "immutable",
            "mutable_tags_allowed",
            "browser_uploads_allowed",
            "deployment_credentials_in_browser",
        },
        "promotion policy",
    )
    if not promotion["accept_only_validated_artifacts"] or not promotion["immutable"]:
        raise DeploymentPolicyError("promotion must require validated immutable artifacts")
    if any(
        promotion[key]
        for key in (
            "mutable_tags_allowed",
            "browser_uploads_allowed",
            "deployment_credentials_in_browser",
        )
    ):
        raise DeploymentPolicyError("promotion policy grants an unsafe authority")

    rollback = _require_exact_keys(
        policy["rollback"],
        {"strategy", "atomic_switch", "restore_validation_required", "retain_previous"},
        "rollback policy",
    )
    if rollback["strategy"] != "previous-validated-bundle" or not all(
        rollback[key] is True
        for key in ("atomic_switch", "restore_validation_required", "retain_previous")
    ):
        raise DeploymentPolicyError("rollback policy is incomplete")

    headers = _require_exact_keys(policy["headers"], _REQUIRED_HEADERS, "security headers")
    csp = headers["Content-Security-Policy"]
    if not isinstance(csp, str) or "default-src 'self'" not in csp:
        raise DeploymentPolicyError("CSP must have a self-only default source")
    if "unsafe-eval" in csp or "*" in csp:
        raise DeploymentPolicyError("CSP permits an unsafe wildcard or eval")
    if headers["X-Content-Type-Options"] != "nosniff":
        raise DeploymentPolicyError("nosniff is required")
    if headers["Referrer-Policy"] != "no-referrer":
        raise DeploymentPolicyError("referrer policy must be no-referrer")

    observability_policy = _require_exact_keys(
        policy["observability"],
        {"version_metadata_required", "checksum_mismatch_alert", "availability_alert", "runbook"},
        "observability policy",
    )
    if observability_policy["version_metadata_required"] is not True:
        raise DeploymentPolicyError("deployment version metadata is required")
    runbook = root / observability_policy["runbook"]
    if not runbook.is_file() or runbook.is_symlink():
        raise DeploymentPolicyError("deployment observability runbook is missing")

    if (
        set(observability) != {"schema_version", "dashboard", "alerts"}
        or observability["schema_version"] != "1.0"
    ):
        raise DeploymentPolicyError("observability definition has an invalid schema")
    dashboard = _require_exact_keys(
        observability["dashboard"], {"id", "title", "signals", "data_source"}, "dashboard"
    )
    if (
        dashboard["data_source"] != "static-host"
        or not isinstance(dashboard["signals"], list)
        or not dashboard["signals"]
        or not all(isinstance(signal, str) for signal in dashboard["signals"])
    ):
        raise DeploymentPolicyError("dashboard must describe static-host signals")
    if not isinstance(observability["alerts"], list) or not observability["alerts"]:
        raise DeploymentPolicyError("at least one alert rule is required")
    alert_ids: set[str] = set()
    for alert in observability["alerts"]:
        item = _require_exact_keys(
            alert,
            {"id", "severity", "signal", "operator", "threshold", "window", "action", "runbook"},
            "alert rule",
        )
        identifier = item["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9-]+", identifier):
            raise DeploymentPolicyError("alert id must be lowercase and stable")
        if identifier in alert_ids:
            raise DeploymentPolicyError(f"duplicate alert id: {identifier}")
        alert_ids.add(identifier)
        if item["severity"] not in {"warning", "critical"} or item["action"] != "notify-only":
            raise DeploymentPolicyError("alerts may notify only and use approved severities")
        if not isinstance(item["threshold"], (int, float)) or item["threshold"] < 0:
            raise DeploymentPolicyError("alert threshold must be non-negative")
        alert_runbook = root / item["runbook"]
        if not alert_runbook.is_file() or alert_runbook.is_symlink():
            raise DeploymentPolicyError(f"alert runbook is missing: {item['runbook']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--observability", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        validate_policy(_load(args.policy), _load(args.observability), args.root.resolve())
    except DeploymentPolicyError as exc:
        print(f"deployment policy error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
