from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from scripts.validate_deployment_manifest import DeploymentPolicyError, validate_policy

ROOT = Path(__file__).parents[1]


def _policies() -> tuple[dict[str, object], dict[str, object]]:
    policy = json.loads((ROOT / "deployment/static-site-policy.json").read_text())
    observability = json.loads((ROOT / "deployment/observability.json").read_text())
    return policy, observability


def test_deployment_policy_is_valid() -> None:
    policy, observability = _policies()
    validate_policy(policy, observability, ROOT)


def test_deployment_policy_rejects_mutable_promotion() -> None:
    policy, observability = _policies()
    policy["promotion"]["immutable"] = False  # type: ignore[index]
    with pytest.raises(DeploymentPolicyError, match="validated immutable"):
        validate_policy(policy, observability, ROOT)


def test_deployment_policy_rejects_open_artifact_set() -> None:
    policy, observability = _policies()
    policy["artifact"]["required_files"] = ["bundle.json"]  # type: ignore[index]
    with pytest.raises(DeploymentPolicyError, match="closed-world"):
        validate_policy(policy, observability, ROOT)


def test_deployment_policy_rejects_unsafe_csp() -> None:
    policy, observability = _policies()
    policy["headers"]["Content-Security-Policy"] += "; script-src *"  # type: ignore[index]
    with pytest.raises(DeploymentPolicyError, match="wildcard"):
        validate_policy(policy, observability, ROOT)


def test_deployment_policy_rejects_mutating_alert_action() -> None:
    policy, observability = _policies()
    changed = copy.deepcopy(observability)
    changed["alerts"][0]["action"] = "execute"  # type: ignore[index]
    with pytest.raises(DeploymentPolicyError, match="notify only"):
        validate_policy(policy, changed, ROOT)


def test_deployment_policy_rejects_missing_alert_runbook() -> None:
    policy, observability = _policies()
    changed = copy.deepcopy(observability)
    changed["alerts"][0]["runbook"] = "docs/missing.md"  # type: ignore[index]
    with pytest.raises(DeploymentPolicyError, match="runbook"):
        validate_policy(policy, changed, ROOT)
