from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from paic.investigation.config import DecisionPolicy
from paic.investigation.models import InvestigationProposal, InvestigationReport
from paic.investigation.probability import score_proposal
from paic.remediation import artifact as remediation_artifact
from paic.remediation.approval import (
    ApprovalError,
    ApprovalLedger,
    attest_decision,
    evaluate_approval,
    issue_token,
    load_approval_secret,
    verify_token,
)
from paic.remediation.artifact import (
    RemediationArtifactError,
    export_control_state,
    export_execution,
    export_plan,
    load_control_state,
    manifest_sha256,
    validate_control_state,
    validate_execution,
    validate_plan,
)
from paic.remediation.config import (
    ApprovalPolicy,
    ApproverIdentity,
    InvestigationGate,
    RemediationConfig,
    RemediationConfigError,
    RemediationPolicy,
    load_remediation_config,
)
from paic.remediation.executor import (
    ExecutionError,
    _apply_action,
    build_rollback_proposal,
    execute_plan,
    verify_execution_transition,
)
from paic.remediation.manifest import ArtifactFileManifest, RemediationArtifactManifest
from paic.remediation.models import (
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTokenClaims,
    ConfigurationResource,
    ControlState,
    DeploymentResource,
    ExecutionReceipt,
    ExecutionRequest,
    FeatureFlagResource,
    RemediationPlan,
    RemediationProposal,
    RestoreConfigurationAction,
    RollbackDeploymentAction,
    SetFeatureFlagAction,
)
from paic.remediation.policy import assess_proposal, build_plan, verify_plan
from paic.simulator.io import file_sha256
from paic.tools.ledger import canonical

NOW = datetime(2026, 7, 18, tzinfo=UTC)
APPROVER_ONE_SECRET = b"h" * 64
APPROVER_TWO_SECRET = b"i" * 64


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _approval_identity_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAIC_HARDENING_APPROVER_ONE_KEY", APPROVER_ONE_SECRET.decode("ascii"))
    monkeypatch.setenv("PAIC_HARDENING_APPROVER_TWO_KEY", APPROVER_TWO_SECRET.decode("ascii"))


def _fixture() -> tuple[InvestigationReport, ControlState, RemediationProposal, RemediationConfig]:
    investigation_proposal = InvestigationProposal.model_validate(
        {
            "summary": "Change regression.",
            "hypotheses": [
                {
                    "hypothesis_id": "change",
                    "title": "Change regression",
                    "prior_probability": 0.5,
                    "rationale": "Supported.",
                    "evidence": [
                        {
                            "evidence_record_id": "e1",
                            "direction": "support",
                            "likelihood_ratio": 10,
                            "explanation": "Supports.",
                        },
                        {
                            "evidence_record_id": "e2",
                            "direction": "support",
                            "likelihood_ratio": 10,
                            "explanation": "Supports independently.",
                        },
                    ],
                    "falsifiers": ["No change after rollback."],
                },
                {
                    "hypothesis_id": "other",
                    "title": "Other",
                    "prior_probability": 0.5,
                    "rationale": "Competing.",
                    "evidence": [
                        {
                            "evidence_record_id": "e2",
                            "direction": "contradict",
                            "likelihood_ratio": 0.1,
                            "explanation": "Contradicts.",
                        }
                    ],
                    "falsifiers": ["Other errors lead."],
                },
            ],
            "explicit_unknowns": ["Recovery unknown."],
            "recommended_next_steps": ["Verify recovery."],
        }
    )
    report = score_proposal(
        investigation_proposal,
        investigation_id="hardening-investigation",
        incident_id="hardening-incident",
        question="Why?",
        policy=DecisionPolicy(minimum_distinct_evidence=2),
        observed_evidence={"e1", "e2"},
        source_hashes={"dataset": "a" * 64},
        attempts=[],
        trace=[],
        total_tokens=0,
    )
    state = ControlState.model_validate(
        {
            "state_id": "hardening-state",
            "incident_id": "hardening-incident",
            "generation": 0,
            "resources": [
                {
                    "resource_type": "feature_flag",
                    "resource_id": "flag/checkout",
                    "enabled": True,
                }
            ],
        }
    )
    proposal = RemediationProposal.model_validate(
        {
            "remediation_id": "hardening-remediation",
            "incident_id": "hardening-incident",
            "investigation_report_sha256": report.report_sha256,
            "selected_hypothesis_id": report.selected_hypothesis_id,
            "requested_by": "agent/planner",
            "requested_at": NOW.isoformat(),
            "summary": "Disable a bad flag.",
            "expected_outcome": "Checkout recovers.",
            "rollback_trigger": "Guardrail worsens.",
            "actions": [
                {
                    "action_type": "feature_flag.set",
                    "action_id": "disable-checkout-flag",
                    "blast_radius": "single_service",
                    "evidence_ids": ["e1", "e2"],
                    "justification": "Evidence supports the flag path.",
                    "resource_id": "flag/checkout",
                    "expected_enabled": True,
                    "desired_enabled": False,
                }
            ],
        }
    )
    return (
        report,
        state,
        proposal,
        RemediationConfig(
            policy_id="hardening-policy",
            approval=ApprovalPolicy(
                approver_registry=[
                    ApproverIdentity(
                        approver_id="operator/one",
                        approver_group="group/one",
                        key_id="hardening-one-v1",
                        key_env="PAIC_HARDENING_APPROVER_ONE_KEY",
                    ),
                    ApproverIdentity(
                        approver_id="operator/two",
                        approver_group="group/two",
                        key_id="hardening-two-v1",
                        key_env="PAIC_HARDENING_APPROVER_TWO_KEY",
                    ),
                ]
            ),
        ),
    )


def _plan(tmp_path: Path) -> tuple[RemediationPlan, Path, RemediationConfig]:
    report, state, proposal, config = _fixture()
    state_dir = tmp_path / "state"
    export_control_state(state, state_dir)
    plan = build_plan(
        report,
        state,
        proposal,
        config,
        investigation_manifest_sha256="b" * 64,
        control_state_manifest_sha256=manifest_sha256(state_dir),
    )
    plan_dir = tmp_path / "plan"
    export_plan(config, proposal, plan, plan_dir)
    return plan, plan_dir, config


def _approved_status(
    plan: RemediationPlan,
    config: RemediationConfig,
    directory: Path,
    *,
    reverse: bool = False,
) -> tuple[ApprovalLedger, ApprovalStatus]:
    decisions = [
        ApprovalDecision(
            plan_sha256=plan.plan_sha256,
            approver_id="operator/one",
            approver_group="group/one",
            decision="approve",
            reason="Approved after independent review.",
            decided_at=NOW + timedelta(minutes=1),
        ),
        ApprovalDecision(
            plan_sha256=plan.plan_sha256,
            approver_id="operator/two",
            approver_group="group/two",
            decision="approve",
            reason="Approved after independent review.",
            decided_at=NOW + timedelta(minutes=2),
        ),
    ]
    ledger = ApprovalLedger(directory)
    secrets_by_identity = {
        "operator/one": APPROVER_ONE_SECRET,
        "operator/two": APPROVER_TWO_SECRET,
    }
    for decision in reversed(decisions) if reverse else decisions:
        ledger.append(
            attest_decision(
                decision,
                config,
                secret=secrets_by_identity[decision.approver_id],
                nonce=f"hardening-{decision.approver_id.replace('/', '-')}-attestation-000000",
            )
        )
    return ledger, evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=3))


def _attest(decision: ApprovalDecision, config: RemediationConfig) -> ApprovalDecision:
    secrets_by_identity = {
        "operator/one": APPROVER_ONE_SECRET,
        "operator/two": APPROVER_TWO_SECRET,
    }
    return attest_decision(
        decision,
        config,
        secret=secrets_by_identity[decision.approver_id],
        nonce=f"hardening-{decision.approver_id.replace('/', '-')}-attestation-000000",
    )


def _execution_export(
    tmp_path: Path,
) -> tuple[RemediationPlan, ControlState, ControlState, Path, Path, Path]:
    plan, plan_dir, config = _plan(tmp_path)
    before = load_control_state(tmp_path / "state").state
    _, status = _approved_status(plan, config, tmp_path / "approval-export")
    token = issue_token(plan, status, config, at=NOW + timedelta(minutes=3), secret=b"e" * 64)
    after, receipt = execute_plan(
        plan,
        before,
        status,
        config,
        ExecutionRequest(
            execution_id="execution-export",
            executed_by="operator/executor",
            executed_at=NOW + timedelta(minutes=4),
        ),
        token=token,
        secret=b"e" * 64,
        before_state_manifest_sha256=plan.control_state_manifest_sha256,
    )
    after_dir = tmp_path / "after"
    execution_dir = tmp_path / "execution-export"
    export_control_state(after, after_dir)
    export_execution(receipt, execution_dir)
    return plan, before, after, plan_dir, after_dir, execution_dir


def test_critical_global_action_is_denied() -> None:
    report, state, proposal, config = _fixture()
    raw = proposal.model_dump(mode="json")
    raw["actions"][0]["blast_radius"] = "global"
    decision = assess_proposal(
        report,
        state,
        RemediationProposal.model_validate(raw),
        config,
    )
    assert decision.outcome == "deny"
    assert decision.risk_level == "critical"


def test_policy_fails_closed_when_every_structured_gate_is_violated() -> None:
    report, state, proposal, _ = _fixture()
    raw = proposal.model_dump(mode="json")
    raw["investigation_report_sha256"] = "b" * 64
    raw["incident_id"] = "other-incident"
    raw["selected_hypothesis_id"] = "other"
    raw["actions"][0]["blast_radius"] = "region"
    raw["actions"][0]["evidence_ids"] = ["unobserved"]
    raw["actions"][0]["expected_enabled"] = False
    raw["actions"][0]["desired_enabled"] = True
    constrained = RemediationConfig(
        policy_id="closed-policy",
        investigation_gate=InvestigationGate(
            minimum_confidence=1.0,
            minimum_selected_posterior=1.0,
            minimum_posterior_margin=1.0,
            maximum_normalized_entropy=0.0,
            minimum_supporting_evidence=50,
        ),
        remediation=RemediationPolicy(
            allowed_action_types=["deployment.rollback"],
            maximum_blast_radius="single_instance",
        ),
        approval=ApprovalPolicy(
            approver_registry=[
                ApproverIdentity(
                    approver_id="operator/one",
                    approver_group="group/one",
                    key_id="closed-one-v1",
                    key_env="PAIC_HARDENING_APPROVER_ONE_KEY",
                )
            ]
        ),
    )
    decision = assess_proposal(report, state, RemediationProposal.model_validate(raw), constrained)
    assert decision.outcome == "deny"
    assert len(decision.reasons) >= 8


def test_duplicate_approver_record_is_rejected(tmp_path: Path) -> None:
    plan, _, _ = _plan(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval")
    decision = ApprovalDecision(
        plan_sha256=plan.plan_sha256,
        approver_id="operator/one",
        approver_group="group/one",
        decision="approve",
        reason="Approved.",
        decided_at=NOW + timedelta(minutes=1),
    )
    ledger.append(decision)
    with pytest.raises(ApprovalError, match="only one"):
        ledger.append(decision.model_copy(update={"reason": "Second decision."}))


def test_high_risk_same_group_is_rejected(tmp_path: Path) -> None:
    report, state, proposal, config = _fixture()
    config = config.model_copy(
        update={
            "approval": ApprovalPolicy(
                approver_registry=[
                    ApproverIdentity(
                        approver_id="operator/one",
                        approver_group="group/shared",
                        key_id="hardening-one-v1",
                        key_env="PAIC_HARDENING_APPROVER_ONE_KEY",
                    ),
                    ApproverIdentity(
                        approver_id="operator/two",
                        approver_group="group/shared",
                        key_id="hardening-two-v1",
                        key_env="PAIC_HARDENING_APPROVER_TWO_KEY",
                    ),
                ]
            )
        }
    )
    raw = proposal.model_dump(mode="json")
    raw["actions"] = [
        {
            "action_type": "deployment.rollback",
            "action_id": "rollback-service",
            "blast_radius": "single_service",
            "evidence_ids": ["e1", "e2"],
            "justification": "Evidence supports rollback.",
            "resource_id": "service/checkout",
            "expected_current_revision": "bad",
            "target_revision": "good",
        }
    ]
    state_raw = state.model_dump(mode="json")
    state_raw["resources"] = [
        {
            "resource_type": "deployment",
            "resource_id": "service/checkout",
            "current_revision": "bad",
            "available_revisions": ["good", "bad"],
        }
    ]
    state = ControlState.model_validate(state_raw)
    state_dir = tmp_path / "state-high"
    export_control_state(state, state_dir)
    plan = build_plan(
        report,
        state,
        RemediationProposal.model_validate(raw),
        config,
        investigation_manifest_sha256="b" * 64,
        control_state_manifest_sha256=manifest_sha256(state_dir),
    )
    ledger = ApprovalLedger(tmp_path / "approval-high")
    for approver in ("operator/one", "operator/two"):
        ledger.append(
            _attest(
                ApprovalDecision(
                    plan_sha256=plan.plan_sha256,
                    approver_id=approver,
                    approver_group="group/shared",
                    decision="approve",
                    reason="Approved.",
                    decided_at=NOW + timedelta(minutes=1),
                ),
                config,
            )
        )
    with pytest.raises(ApprovalError, match="distinct groups"):
        evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=2))


def test_expired_plan_cannot_issue_token(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval-expired")
    ledger.append(
        _attest(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id="operator/one",
                approver_group="group/one",
                decision="approve",
                reason="Approved.",
                decided_at=NOW + timedelta(minutes=1),
            ),
            config,
        )
    )
    at = plan.expires_at + timedelta(seconds=1)
    status = evaluate_approval(plan, ledger, config, at=at)
    assert status.status == "expired"
    with pytest.raises(ApprovalError, match="approved plan"):
        issue_token(plan, status, config, at=at, secret=b"x" * 64)


def test_plan_semantic_tampering_is_rejected(tmp_path: Path) -> None:
    _, plan_dir, _ = _plan(tmp_path)
    plan_path = plan_dir / "plan.json"
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    raw["summary"] = "Tampered summary"
    plan_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    manifest_path = plan_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == "plan.json":
            item["byte_size"] = plan_path.stat().st_size
            item["sha256"] = file_sha256(plan_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (plan_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    issues = validate_plan(plan_dir)
    assert any("plan hash" in issue for issue in issues)


def test_control_state_symlink_is_rejected(tmp_path: Path) -> None:
    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-symlink"
    export_control_state(state, state_dir)
    target = state_dir / "state.real.json"
    state_path = state_dir / "state.json"
    state_path.rename(target)
    try:
        state_path.symlink_to(target.name)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    assert any("symbolic link" in issue for issue in validate_control_state(state_dir))


def test_failed_overwrite_keeps_the_prior_complete_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed staged export must not erase or partially replace prior state."""

    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-atomic"
    export_control_state(state, state_dir)
    original = (state_dir / "state.json").read_bytes()

    def reject_staged_export(_: str | Path) -> object:
        raise remediation_artifact.RemediationArtifactError("injected validation failure")

    monkeypatch.setattr(remediation_artifact, "load_control_state", reject_staged_export)
    with pytest.raises(remediation_artifact.RemediationArtifactError, match="injected"):
        export_control_state(state.model_copy(update={"generation": 1}), state_dir, overwrite=True)

    assert (state_dir / "state.json").read_bytes() == original
    assert not list(tmp_path.glob(".state-atomic.tmp-*"))


def test_future_dated_approval_is_not_counted_early(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval-future")
    ledger.append(
        _attest(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id="operator/one",
                approver_group="group/one",
                decision="approve",
                reason="Approved for the scheduled window.",
                decided_at=NOW + timedelta(minutes=5),
            ),
            config,
        )
    )
    with pytest.raises(ApprovalError, match="after the evaluation time"):
        evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=1))


def test_plan_manifest_status_and_bindings_are_semantically_validated(tmp_path: Path) -> None:
    _, plan_dir, _ = _plan(tmp_path)
    manifest_path = plan_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "denied"
    manifest["bindings"]["investigation_report"] = "c" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (plan_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    issues = validate_plan(plan_dir)
    assert any("status differs" in issue or "bindings differ" in issue for issue in issues)


def test_plan_and_token_expiry_are_exclusive_boundaries(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval-boundary")
    ledger.append(
        _attest(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id="operator/one",
                approver_group="group/one",
                decision="approve",
                reason="Approved.",
                decided_at=NOW + timedelta(minutes=1),
            ),
            config,
        )
    )
    status = evaluate_approval(plan, ledger, config, at=plan.expires_at)
    assert status.status == "expired"


def test_approval_snapshot_is_independent_of_submission_order(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    _, ordered = _approved_status(plan, config, tmp_path / "approval-ordered")
    _, reversed_order = _approved_status(plan, config, tmp_path / "approval-reversed", reverse=True)
    assert ordered.status == reversed_order.status == "approved"
    assert ordered.approval_snapshot_sha256 == reversed_order.approval_snapshot_sha256


def test_token_verification_fails_closed_for_bound_claims_and_time(
    tmp_path: Path,
) -> None:
    plan, _, config = _plan(tmp_path)
    _, status = _approved_status(plan, config, tmp_path / "approval-token")
    issued_at = NOW + timedelta(minutes=3)
    token = issue_token(
        plan,
        status,
        config,
        at=issued_at,
        secret=b"t" * 64,
        nonce="nonce-for-claim-test-000000",
        token_id="token-claims",
    )
    assert verify_token(token, plan, status, config, at=issued_at, secret=b"t" * 64).token_id == (
        "token-claims"
    )

    payload, _ = token.split(".")
    raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw["incident_id"] = "other-incident"
    altered_payload = base64.urlsafe_b64encode(canonical(raw).encode()).rstrip(b"=").decode()
    signature = hmac.new(b"t" * 64, canonical(raw).encode(), hashlib.sha256).digest()
    altered = altered_payload + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    with pytest.raises(ApprovalError, match="different plan"):
        verify_token(altered, plan, status, config, at=issued_at, secret=b"t" * 64)
    with pytest.raises(ApprovalError, match="validity window"):
        verify_token(
            token, plan, status, config, at=issued_at - timedelta(seconds=1), secret=b"t" * 64
        )
    with pytest.raises(ApprovalError, match="structure"):
        verify_token("not-a-token", plan, status, config, at=issued_at, secret=b"t" * 64)


def test_secret_loading_never_accepts_missing_or_short_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = RemediationConfig(
        policy_id="secret-policy",
        approval=ApprovalPolicy(
            approver_registry=[
                ApproverIdentity(
                    approver_id="operator/one",
                    approver_group="group/one",
                    key_id="secret-one-v1",
                    key_env="PAIC_HARDENING_APPROVER_ONE_KEY",
                )
            ]
        ),
    )
    monkeypatch.delenv(config.approval.secret_env, raising=False)
    with pytest.raises(ApprovalError, match="is not set"):
        load_approval_secret(config)
    monkeypatch.setenv(config.approval.secret_env, "short")
    with pytest.raises(ApprovalError, match="shorter"):
        load_approval_secret(config)


def test_execution_rechecks_state_binding_and_consumed_nonce(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    state = load_control_state(tmp_path / "state").state
    _, status = _approved_status(plan, config, tmp_path / "approval-execution")
    token = issue_token(
        plan,
        status,
        config,
        at=NOW + timedelta(minutes=3),
        secret=b"x" * 64,
        nonce="nonce-for-replay-test-000000",
        token_id="token-replay",
    )
    request = ExecutionRequest(
        execution_id="execution-replay",
        executed_by="operator/executor",
        executed_at=NOW + timedelta(minutes=4),
    )
    with pytest.raises(ExecutionError, match="control state differs"):
        execute_plan(
            plan,
            state,
            status,
            config,
            request,
            token=token,
            secret=b"x" * 64,
            before_state_manifest_sha256="f" * 64,
        )
    consumed = state.model_copy(
        update={
            "consumed_token_nonce_hashes": [
                hashlib.sha256(b"nonce-for-replay-test-000000").hexdigest()
            ]
        }
    )
    with pytest.raises(ExecutionError, match="already been consumed"):
        execute_plan(
            plan,
            consumed,
            status,
            config,
            request,
            token=token,
            secret=b"x" * 64,
            before_state_manifest_sha256=plan.control_state_manifest_sha256,
        )


def test_execution_transition_rejects_semantically_rehashed_receipt(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    state = load_control_state(tmp_path / "state").state
    _, status = _approved_status(plan, config, tmp_path / "approval-transition")
    token = issue_token(plan, status, config, at=NOW + timedelta(minutes=3), secret=b"y" * 64)
    after, receipt = execute_plan(
        plan,
        state,
        status,
        config,
        ExecutionRequest(
            execution_id="execution-transition",
            executed_by="operator/executor",
            executed_at=NOW + timedelta(minutes=4),
        ),
        token=token,
        secret=b"y" * 64,
        before_state_manifest_sha256=plan.control_state_manifest_sha256,
    )
    invalid_after = after.model_copy(update={"generation": after.generation + 1})
    with pytest.raises(ExecutionError, match="generation"):
        verify_execution_transition(plan, state, invalid_after, receipt)


def test_artifact_loader_rejects_missing_nested_and_unsafe_manifest_paths(tmp_path: Path) -> None:
    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-layout"
    export_control_state(state, state_dir)
    (state_dir / "nested").mkdir()
    assert any(
        "nested" in issue or "undeclared" in issue for issue in validate_control_state(state_dir)
    )

    missing_dir = tmp_path / "state-missing"
    export_control_state(state, missing_dir)
    (missing_dir / "state.json").unlink()
    assert any(
        "missing" in issue or "undeclared" in issue for issue in validate_control_state(missing_dir)
    )

    unsafe_dir = tmp_path / "state-unsafe"
    export_control_state(state, unsafe_dir)
    manifest_path = unsafe_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["relative_path"] = "../state.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (unsafe_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any(
        "cannot load" in issue or "unsafe" in issue for issue in validate_control_state(unsafe_dir)
    )


def test_artifact_output_path_rejects_regular_files_and_symlinks(tmp_path: Path) -> None:
    _, state, _, _ = _fixture()
    file_output = tmp_path / "not-a-directory"
    file_output.write_text("not an artifact", encoding="utf-8")
    with pytest.raises(RemediationArtifactError, match="not a regular directory"):
        export_control_state(state, file_output, overwrite=True)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "state-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    with pytest.raises(RemediationArtifactError, match="not a regular directory"):
        export_control_state(state, link, overwrite=True)


def test_plan_hash_validation_rejects_rehashed_semantic_mutation(tmp_path: Path) -> None:
    plan, _, _ = _plan(tmp_path)
    malformed = plan.model_copy(update={"status": "denied"})
    with pytest.raises(Exception, match="status"):
        RemediationPlan.model_validate(malformed.model_dump(mode="json"))
    with pytest.raises(Exception, match="plan hash"):
        verify_plan(plan.model_copy(update={"summary": "different"}))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("factory", "value", "message"),
    [
        (
            DeploymentResource,
            {
                "resource_id": "service/test",
                "current_revision": "current",
                "available_revisions": ["current", "current"],
            },
            "unique",
        ),
        (
            DeploymentResource,
            {
                "resource_id": "service/test",
                "current_revision": "missing",
                "available_revisions": ["current"],
            },
            "must be available",
        ),
        (
            ConfigurationResource,
            {
                "resource_id": "config/test",
                "current_version": "current",
                "available_versions": ["current", "current"],
            },
            "unique",
        ),
        (
            ConfigurationResource,
            {
                "resource_id": "config/test",
                "current_version": "missing",
                "available_versions": ["current"],
            },
            "must be available",
        ),
    ],
)
def test_versioned_resources_reject_nonreconstructable_state(
    factory: type[DeploymentResource] | type[ConfigurationResource],
    value: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        factory.model_validate(value)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("factory", "value", "message"),
    [
        (
            RollbackDeploymentAction,
            {
                "action_id": "rollback-test",
                "blast_radius": "single_service",
                "evidence_ids": ["e1", "e1"],
                "justification": "Test duplicate evidence.",
                "resource_id": "service/test",
                "expected_current_revision": "same",
                "target_revision": "same",
            },
            "unique",
        ),
        (
            SetFeatureFlagAction,
            {
                "action_id": "flag-test",
                "blast_radius": "single_service",
                "evidence_ids": ["e1"],
                "justification": "Test no-op flag.",
                "resource_id": "flag/test",
                "expected_enabled": True,
                "desired_enabled": True,
            },
            "must change",
        ),
        (
            RestoreConfigurationAction,
            {
                "action_id": "config-test",
                "blast_radius": "single_service",
                "evidence_ids": ["e1"],
                "justification": "Test no-op config.",
                "resource_id": "config/test",
                "expected_current_version": "same",
                "target_version": "same",
            },
            "must differ",
        ),
    ],
)
def test_actions_reject_noop_or_ambiguous_inverse_definitions(
    factory: type[RollbackDeploymentAction]
    | type[SetFeatureFlagAction]
    | type[RestoreConfigurationAction],
    value: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        factory.model_validate(value)


def test_control_state_and_proposal_models_reject_duplicate_targets() -> None:
    with pytest.raises(ValidationError, match="resource IDs"):
        ControlState.model_validate(
            {
                "state_id": "duplicates",
                "incident_id": "incident",
                "generation": 0,
                "resources": [
                    {"resource_type": "feature_flag", "resource_id": "flag/test", "enabled": True},
                    {"resource_type": "feature_flag", "resource_id": "flag/test", "enabled": False},
                ],
            }
        )
    _, _, proposal, _ = _fixture()
    raw = proposal.model_dump(mode="json")
    raw["actions"] = [raw["actions"][0], raw["actions"][0]]
    with pytest.raises(ValidationError, match="action IDs"):
        RemediationProposal.model_validate(raw)


def test_token_claim_model_rejects_invalid_windows_and_duplicate_actions() -> None:
    claims = {
        "token_id": "token-validation",
        "plan_sha256": "a" * 64,
        "incident_id": "incident",
        "action_ids": ["action-one", "action-one"],
        "approval_snapshot_sha256": "b" * 64,
        "issued_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
        "nonce": "nonce-for-token-validation-000000",
    }
    with pytest.raises(ValidationError, match="unique"):
        ApprovalTokenClaims.model_validate(claims)
    claims["action_ids"] = ["action-one"]
    claims["expires_at"] = NOW.isoformat()
    with pytest.raises(ValidationError, match="expire after"):
        ApprovalTokenClaims.model_validate(claims)


def test_policy_denial_reasons_cover_investigation_and_action_state_gates() -> None:
    report, state, proposal, config = _fixture()
    abstained = report.model_copy(update={"status": "abstained"})
    decision = assess_proposal(abstained, state, proposal, config)
    assert decision.outcome == "deny"
    assert decision.reasons == ["investigation must be concluded before remediation"]

    missing_selected = report.model_copy(update={"selected_hypothesis_id": "missing"})
    decision = assess_proposal(missing_selected, state, proposal, config)
    assert decision.outcome == "deny"
    assert "selected hypothesis differs" in " ".join(decision.reasons)
    assert "selected hypothesis is missing" in " ".join(decision.reasons)

    raw = proposal.model_dump(mode="json")
    raw["actions"][0] = {
        "action_type": "deployment.rollback",
        "action_id": "rollback-wrong-type",
        "blast_radius": "single_service",
        "evidence_ids": ["e1", "e2"],
        "justification": "Wrong resource type must fail closed.",
        "resource_id": "flag/checkout",
        "expected_current_revision": "bad",
        "target_revision": "good",
    }
    decision = assess_proposal(report, state, RemediationProposal.model_validate(raw), config)
    assert decision.outcome == "deny"
    assert any("non-deployment" in reason for reason in decision.reasons)


def test_policy_rejects_unknown_action_target_and_unavailable_version() -> None:
    report, state, proposal, config = _fixture()
    raw = proposal.model_dump(mode="json")
    raw["actions"][0]["resource_id"] = "flag/missing"
    unknown = assess_proposal(report, state, RemediationProposal.model_validate(raw), config)
    assert any("unknown resource" in reason for reason in unknown.reasons)

    state_raw = state.model_dump(mode="json")
    state_raw["resources"] = [
        {
            "resource_type": "configuration",
            "resource_id": "config/checkout",
            "current_version": "bad",
            "available_versions": ["bad", "good"],
        }
    ]
    raw = proposal.model_dump(mode="json")
    raw["actions"][0] = {
        "action_type": "configuration.restore",
        "action_id": "restore-unavailable",
        "blast_radius": "single_service",
        "evidence_ids": ["e1", "e2"],
        "justification": "Unavailable target must fail closed.",
        "resource_id": "config/checkout",
        "expected_current_version": "bad",
        "target_version": "missing",
    }
    unavailable = assess_proposal(
        report,
        ControlState.model_validate(state_raw),
        RemediationProposal.model_validate(raw),
        config,
    )
    assert any("unavailable configuration" in reason for reason in unavailable.reasons)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("mutation", "expected"),
    [
        (
            lambda root: (root / "_SUCCESS").unlink(),
            "missing or undeclared",
        ),
        (
            lambda root: (root / "manifest.json").write_text("{}", encoding="utf-8"),
            "cannot load artifact manifest",
        ),
        (
            lambda root: (root / "state.json").write_text("{}", encoding="utf-8"),
            "metadata mismatch",
        ),
    ],
)
def test_control_state_manifest_rejects_missing_marker_invalid_manifest_and_hash_drift(
    tmp_path: Path,
    mutation: Any,
    expected: str,
) -> None:
    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-integrity"
    export_control_state(state, state_dir)
    mutation(state_dir)
    assert any(expected in issue for issue in validate_control_state(state_dir))


def test_control_state_rehashed_payload_still_requires_semantic_binding(tmp_path: Path) -> None:
    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-semantic"
    export_control_state(state, state_dir)
    state_path = state_dir / "state.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["generation"] = 2
    state_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    manifest_path = state_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["byte_size"] = state_path.stat().st_size
    manifest["files"][0]["sha256"] = file_sha256(state_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (state_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("payload hash mismatch" in issue for issue in validate_control_state(state_dir))


def test_approval_ledger_rejects_malformed_and_reordered_records(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    ledger, _ = _approved_status(plan, config, tmp_path / "approval-integrity")
    original = ledger.path.read_text(encoding="utf-8")
    ledger.path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ApprovalError, match="invalid record"):
        ledger.validate()
    ledger.path.write_text("\n".join(reversed(original.splitlines())) + "\n", encoding="utf-8")
    with pytest.raises(ApprovalError, match="sequence or chain"):
        ledger.validate()


def test_approval_ledger_rejects_decision_and_record_hash_tampering(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    ledger, _ = _approved_status(plan, config, tmp_path / "approval-hashes")
    raw = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
    raw["decision_sha256"] = "f" * 64
    ledger.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(ApprovalError, match="decision hash"):
        ledger.validate()


def test_action_application_rejects_wrong_resource_types_and_stale_preconditions() -> None:
    deployment = DeploymentResource(
        resource_id="service/test", current_revision="bad", available_revisions=["good", "bad"]
    )
    rollback = RollbackDeploymentAction(
        action_id="rollback-test",
        blast_radius="single_service",
        evidence_ids=["e1"],
        justification="Test stale deployment checks.",
        resource_id="service/test",
        expected_current_revision="bad",
        target_revision="good",
    )
    with pytest.raises(ExecutionError, match="non-deployment"):
        _apply_action(rollback, FeatureFlagResource(resource_id="flag/test", enabled=True))
    with pytest.raises(ExecutionError, match="stale deployment"):
        _apply_action(rollback, deployment.model_copy(update={"current_revision": "good"}))
    with pytest.raises(ExecutionError, match="unavailable deployment"):
        _apply_action(rollback.model_copy(update={"target_revision": "missing"}), deployment)

    flag_action = SetFeatureFlagAction(
        action_id="disable-flag",
        blast_radius="single_service",
        evidence_ids=["e1"],
        justification="Test stale flag checks.",
        resource_id="flag/test",
        expected_enabled=True,
        desired_enabled=False,
    )
    with pytest.raises(ExecutionError, match="non-feature-flag"):
        _apply_action(flag_action, deployment)
    with pytest.raises(ExecutionError, match="stale feature-flag"):
        _apply_action(flag_action, FeatureFlagResource(resource_id="flag/test", enabled=False))

    config_action = RestoreConfigurationAction(
        action_id="restore-config",
        blast_radius="single_service",
        evidence_ids=["e1"],
        justification="Test stale configuration checks.",
        resource_id="config/test",
        expected_current_version="bad",
        target_version="good",
    )
    config_resource = ConfigurationResource(
        resource_id="config/test", current_version="bad", available_versions=["bad", "good"]
    )
    with pytest.raises(ExecutionError, match="non-configuration"):
        _apply_action(config_action, deployment)
    with pytest.raises(ExecutionError, match="stale configuration"):
        _apply_action(config_action, config_resource.model_copy(update={"current_version": "good"}))
    with pytest.raises(ExecutionError, match="unavailable configuration"):
        _apply_action(
            config_action.model_copy(update={"target_version": "missing"}), config_resource
        )


def test_execution_requires_matching_plan_state_and_incident_bindings(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    state = load_control_state(tmp_path / "state").state
    _, status = _approved_status(plan, config, tmp_path / "approval-bindings")
    token = issue_token(plan, status, config, at=NOW + timedelta(minutes=3), secret=b"z" * 64)
    request = ExecutionRequest(
        execution_id="execution-bindings",
        executed_by="operator/executor",
        executed_at=NOW + timedelta(minutes=4),
    )
    with pytest.raises(ExecutionError, match="control state incident"):
        execute_plan(
            plan,
            state.model_copy(update={"incident_id": "other-incident"}),
            status,
            config,
            request,
            token=token,
            secret=b"z" * 64,
            before_state_manifest_sha256=plan.control_state_manifest_sha256,
        )
    with pytest.raises(ExecutionError, match="integrity"):
        execute_plan(
            plan.model_copy(update={"status": "denied"}),
            state,
            status,
            config,
            request,
            token=token,
            secret=b"z" * 64,
            before_state_manifest_sha256=plan.control_state_manifest_sha256,
        )


def test_rollback_and_transition_verification_reject_mismatched_execution(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    state = load_control_state(tmp_path / "state").state
    _, status = _approved_status(plan, config, tmp_path / "approval-verify")
    token = issue_token(plan, status, config, at=NOW + timedelta(minutes=3), secret=b"v" * 64)
    after, receipt = execute_plan(
        plan,
        state,
        status,
        config,
        ExecutionRequest(
            execution_id="execution-verify",
            executed_by="operator/executor",
            executed_at=NOW + timedelta(minutes=4),
        ),
        token=token,
        secret=b"v" * 64,
        before_state_manifest_sha256=plan.control_state_manifest_sha256,
    )
    with pytest.raises(ExecutionError, match="different remediation plan"):
        build_rollback_proposal(
            plan.model_copy(update={"plan_sha256": "e" * 64}),
            receipt,
            requested_by="operator/rollback",
            requested_at=NOW + timedelta(minutes=5),
        )
    with pytest.raises(ExecutionError, match="different plan"):
        verify_execution_transition(
            plan.model_copy(update={"plan_sha256": "e" * 64}), state, after, receipt
        )
    with pytest.raises(ExecutionError, match="action receipt count"):
        verify_execution_transition(
            plan,
            state,
            after,
            receipt.model_copy(update={"action_receipts": []}),
        )


def test_approval_ledger_rejects_duplicate_record_hashes(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    ledger, _ = _approved_status(plan, config, tmp_path / "approval-duplicate-record")
    first = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[0])
    duplicate = dict(first)
    duplicate["sequence"] = 2
    duplicate["previous_record_sha256"] = first["record_sha256"]
    ledger.path.write_text(
        json.dumps(first) + "\n" + json.dumps(duplicate) + "\n", encoding="utf-8"
    )
    with pytest.raises(ApprovalError, match="duplicated"):
        ledger.validate()


def test_token_verification_rejects_status_snapshot_actions_and_invalid_encoding(
    tmp_path: Path,
) -> None:
    plan, _, config = _plan(tmp_path)
    _, status = _approved_status(plan, config, tmp_path / "approval-token-bindings")
    at = NOW + timedelta(minutes=3)
    token = issue_token(plan, status, config, at=at, secret=b"k" * 64)
    with pytest.raises(ApprovalError, match="no longer approved"):
        verify_token(
            token,
            plan,
            status.model_copy(update={"status": "pending"}),
            config,
            at=at,
            secret=b"k" * 64,
        )
    with pytest.raises(ApprovalError, match="stale approval snapshot"):
        verify_token(
            token,
            plan,
            status.model_copy(update={"approval_snapshot_sha256": "c" * 64}),
            config,
            at=at,
            secret=b"k" * 64,
        )
    with pytest.raises(ApprovalError, match="encoding"):
        verify_token("***.***", plan, status, config, at=at, secret=b"k" * 64)

    payload, _ = token.split(".")
    raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw["action_ids"] = ["other-action"]
    encoded = base64.urlsafe_b64encode(canonical(raw).encode()).rstrip(b"=").decode()
    signature = hmac.new(b"k" * 64, canonical(raw).encode(), hashlib.sha256).digest()
    altered = encoded + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    with pytest.raises(ApprovalError, match="action set"):
        verify_token(altered, plan, status, config, at=at, secret=b"k" * 64)


def test_approval_evaluation_rejects_decisions_bound_to_another_plan_or_window(
    tmp_path: Path,
) -> None:
    plan, _, config = _plan(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval-windows")
    ledger.append(
        _attest(
            ApprovalDecision(
                plan_sha256="d" * 64,
                approver_id="operator/one",
                approver_group="group/one",
                decision="approve",
                reason="Deliberately mismatched plan.",
                decided_at=NOW + timedelta(minutes=1),
            ),
            config,
        )
    )
    with pytest.raises(ApprovalError, match="different plan"):
        evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=2))

    ledger = ApprovalLedger(tmp_path / "approval-before-window")
    ledger.append(
        _attest(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id="operator/one",
                approver_group="group/one",
                decision="approve",
                reason="Deliberately predates the plan.",
                decided_at=NOW - timedelta(seconds=1),
            ),
            config,
        )
    )
    with pytest.raises(ApprovalError, match="validity window"):
        evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=2))


def test_control_state_loader_rejects_rehashed_type_and_identity_substitution(
    tmp_path: Path,
) -> None:
    _, state, _, _ = _fixture()
    type_dir = tmp_path / "state-wrong-type"
    export_control_state(state, type_dir)
    manifest_path = type_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_type"] = "execution_receipt"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (type_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("not a control-state" in issue for issue in validate_control_state(type_dir))

    identity_dir = tmp_path / "state-wrong-identity"
    export_control_state(state, identity_dir)
    state_path = identity_dir / "state.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["state_id"] = "substituted-state"
    state_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    manifest_path = identity_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_id"] = "manifest-state-id"
    manifest["payload_sha256"] = hashlib.sha256(canonical(raw).encode()).hexdigest()
    manifest["files"][0]["byte_size"] = state_path.stat().st_size
    manifest["files"][0]["sha256"] = file_sha256(state_path)
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (identity_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("identity differs" in issue for issue in validate_control_state(identity_dir))


def test_atomic_publish_failure_restores_existing_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-publish"
    export_control_state(state, state_dir)
    original = (state_dir / "state.json").read_bytes()
    replace = os.replace

    def fail_stage_publish(source: str | Path, destination: str | Path) -> None:
        if Path(source).name.startswith(".state-publish.tmp-") and Path(destination) == state_dir:
            raise OSError("injected publish failure")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_stage_publish)
    with pytest.raises(RemediationArtifactError, match="atomically publish"):
        export_control_state(state.model_copy(update={"generation": 1}), state_dir, overwrite=True)
    assert (state_dir / "state.json").read_bytes() == original
    assert validate_control_state(state_dir) == []


def _refresh_artifact_file(root: Path, relative_path: str) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = root / relative_path
    for file_entry in manifest["files"]:
        if file_entry["relative_path"] == relative_path:
            file_entry["byte_size"] = target.stat().st_size
            file_entry["sha256"] = file_sha256(target)
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    return cast(dict[str, Any], manifest)


def test_execution_artifact_rejects_rehashed_receipt_and_binding_tampering(tmp_path: Path) -> None:
    _, _, _, _, _, execution_dir = _execution_export(tmp_path)
    receipt_path = execution_dir / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["receipt_sha256"] = "f" * 64
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    _refresh_artifact_file(execution_dir, "receipt.json")
    assert any("receipt hash" in issue for issue in validate_execution(execution_dir))

    _, _, _, _, _, binding_dir = _execution_export(tmp_path / "binding")
    manifest_path = binding_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bindings"]["plan"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (binding_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("bindings differ" in issue for issue in validate_execution(binding_dir))


def test_execution_validation_rejects_missing_bound_artifacts_and_state_hash_drift(
    tmp_path: Path,
) -> None:
    _, _, after, plan_dir, after_dir, execution_dir = _execution_export(tmp_path)
    missing = validate_execution(
        execution_dir,
        plan_dir=tmp_path / "missing-plan",
        before_state_dir=tmp_path / "missing-before",
        after_state_dir=tmp_path / "missing-after",
    )
    assert len(missing) == 3
    assert all("cannot validate" in issue for issue in missing)

    drift = after.model_copy(update={"generation": after.generation + 1})
    drift_dir = tmp_path / "after-drift"
    export_control_state(drift, drift_dir)
    issues = validate_execution(
        execution_dir,
        plan_dir=plan_dir,
        before_state_dir=tmp_path / "state",
        after_state_dir=drift_dir,
    )
    assert any("after-state" in issue or "generation" in issue for issue in issues)
    assert (
        validate_execution(
            execution_dir,
            plan_dir=plan_dir,
            before_state_dir=tmp_path / "state",
            after_state_dir=after_dir,
        )
        == []
    )


def test_remediation_config_loader_rejects_missing_invalid_and_duplicate_policy_values(
    tmp_path: Path,
) -> None:
    with pytest.raises(RemediationConfigError, match="cannot read"):
        load_remediation_config(tmp_path / "missing.yaml")
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("policy_id: [", encoding="utf-8")
    with pytest.raises(RemediationConfigError, match="invalid YAML"):
        load_remediation_config(invalid_yaml)
    duplicate_actions = tmp_path / "duplicate-actions.yaml"
    duplicate_actions.write_text(
        "policy_id: remediation-test\nremediation:\n  allowed_action_types:\n"
        "    - feature_flag.set\n    - feature_flag.set\napproval:\n  approver_registry:\n"
        "    - approver_id: operator/one\n      approver_group: group/one\n"
        "      key_id: loader-one-v1\n      key_env: PAIC_HARDENING_APPROVER_ONE_KEY\n",
        encoding="utf-8",
    )
    with pytest.raises(RemediationConfigError, match="unique"):
        load_remediation_config(duplicate_actions)


def test_remediation_config_loader_accepts_a_registry_bound_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "valid-registry.yaml"
    config_path.write_text(
        "policy_id: remediation-test\napproval:\n  approver_registry:\n"
        "    - approver_id: operator/one\n      approver_group: group/one\n"
        "      key_id: loader-one-v1\n      key_env: PAIC_HARDENING_APPROVER_ONE_KEY\n",
        encoding="utf-8",
    )
    assert load_remediation_config(config_path).policy_id == "remediation-test"


def test_manifest_models_reject_unsafe_and_duplicate_declared_paths() -> None:
    with pytest.raises(ValidationError, match="flat"):
        ArtifactFileManifest(relative_path="nested/state.json", byte_size=1, sha256="a" * 64)
    with pytest.raises(ValidationError, match="inside"):
        ArtifactFileManifest(relative_path="../state.json", byte_size=1, sha256="a" * 64)
    file_entry = {"relative_path": "state.json", "byte_size": 1, "sha256": "a" * 64}
    with pytest.raises(ValidationError, match="unique"):
        RemediationArtifactManifest.model_validate(
            {
                "artifact_type": "control_state",
                "artifact_id": "state",
                "incident_id": "incident",
                "generator_version": "0.9.0",
                "status": "ready",
                "payload_sha256": "b" * 64,
                "bindings": {},
                "files": [file_entry, file_entry],
            }
        )


def test_plan_and_state_models_reject_inconsistent_semantics(tmp_path: Path) -> None:
    plan, _, _ = _plan(tmp_path)
    raw = plan.model_dump(mode="json")
    raw["expires_at"] = raw["requested_at"]
    with pytest.raises(ValidationError, match="expire after"):
        RemediationPlan.model_validate(raw)
    raw = plan.model_dump(mode="json")
    raw["risk_level"] = "high"
    with pytest.raises(ValidationError, match="risk"):
        RemediationPlan.model_validate(raw)
    raw = plan.model_dump(mode="json")
    raw["required_approvals"] = 2
    with pytest.raises(ValidationError, match="approval count"):
        RemediationPlan.model_validate(raw)

    state = load_control_state(tmp_path / "state").state
    duplicate_nonce = state.model_dump(mode="json")
    duplicate_nonce["consumed_token_nonce_hashes"] = ["a" * 64, "a" * 64]
    with pytest.raises(ValidationError, match="nonce"):
        ControlState.model_validate(duplicate_nonce)
    duplicate_plan = state.model_dump(mode="json")
    duplicate_plan["executed_plan_hashes"] = ["b" * 64, "b" * 64]
    with pytest.raises(ValidationError, match="plan hashes"):
        ControlState.model_validate(duplicate_plan)


def test_plan_artifact_rejects_rehashed_payload_proposal_and_manifest_substitution(
    tmp_path: Path,
) -> None:
    _, payload_dir, _ = _plan(tmp_path / "payload")
    manifest_path = payload_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["payload_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (payload_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("payload hash" in issue for issue in validate_plan(payload_dir))

    _, proposal_dir, _ = _plan(tmp_path / "proposal")
    proposal_path = proposal_dir / "proposal.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["summary"] = "Substituted proposal."
    proposal_path.write_text(json.dumps(proposal) + "\n", encoding="utf-8")
    _refresh_artifact_file(proposal_dir, "proposal.json")
    assert any("proposal hash" in issue for issue in validate_plan(proposal_dir))

    _, manifest_dir, _ = _plan(tmp_path / "manifest")
    manifest_path = manifest_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_id"] = "other-remediation"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (manifest_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("identity differs" in issue for issue in validate_plan(manifest_dir))

    _, binding_dir, _ = _plan(tmp_path / "bindings")
    manifest_path = binding_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bindings"]["investigation_report"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (binding_dir / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert any("bindings differ" in issue for issue in validate_plan(binding_dir))


def test_execution_transition_rejects_identity_lineage_and_receipt_drift(tmp_path: Path) -> None:
    plan, before, after, _, _, execution_dir = _execution_export(tmp_path)
    receipt = json.loads((execution_dir / "receipt.json").read_text(encoding="utf-8"))
    parsed_receipt = ExecutionReceipt.model_validate(receipt)
    with pytest.raises(ExecutionError, match="identity differs"):
        verify_execution_transition(
            plan,
            before,
            after,
            parsed_receipt.model_copy(update={"remediation_id": "other-remediation"}),
        )
    with pytest.raises(ExecutionError, match="state incident"):
        verify_execution_transition(
            plan,
            before.model_copy(update={"incident_id": "other-incident"}),
            after,
            parsed_receipt,
        )
    with pytest.raises(ExecutionError, match="control-state identity"):
        verify_execution_transition(
            plan,
            before,
            after.model_copy(update={"state_id": "other-state"}),
            parsed_receipt,
        )
    with pytest.raises(ExecutionError, match="plan was already consumed"):
        verify_execution_transition(
            plan,
            before.model_copy(update={"executed_plan_hashes": [plan.plan_sha256]}),
            after,
            parsed_receipt,
        )
    with pytest.raises(ExecutionError, match="token nonce was already consumed"):
        verify_execution_transition(
            plan,
            before.model_copy(
                update={"consumed_token_nonce_hashes": [parsed_receipt.token_nonce_sha256]}
            ),
            after,
            parsed_receipt,
        )
    with pytest.raises(ExecutionError, match="payload hash"):
        verify_execution_transition(
            plan,
            before,
            after,
            parsed_receipt.model_copy(update={"after_state_payload_sha256": "f" * 64}),
        )


def test_plan_validation_reports_unavailable_bound_artifacts(tmp_path: Path) -> None:
    _, plan_dir, _ = _plan(tmp_path)
    issues = validate_plan(
        plan_dir,
        investigation_dir=tmp_path / "missing-investigation",
        control_state_dir=tmp_path / "missing-state",
    )
    assert len(issues) == 2
    assert all("cannot validate bound" in issue for issue in issues)


def test_control_state_validation_rejects_non_directory_roots_and_forged_markers(
    tmp_path: Path,
) -> None:
    root_file = tmp_path / "not-an-artifact"
    root_file.write_text("not a directory", encoding="utf-8")
    assert any("not a regular directory" in issue for issue in validate_control_state(root_file))
    _, state, _, _ = _fixture()
    state_dir = tmp_path / "state-marker"
    export_control_state(state, state_dir)
    (state_dir / "_SUCCESS").write_text("0" * 64 + "\n", encoding="utf-8")
    assert any("success marker" in issue for issue in validate_control_state(state_dir))


def test_registry_attestation_rejects_forged_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _ = _plan(tmp_path)
    config = RemediationConfig(
        policy_id="attestation-policy",
        approval=ApprovalPolicy(
            approver_registry=[
                ApproverIdentity(
                    approver_id="operator/trusted",
                    approver_group="group/trusted",
                    key_id="trusted-v1",
                    key_env="PAIC_TRUSTED_APPROVER_KEY",
                )
            ]
        ),
    )
    monkeypatch.setenv("PAIC_TRUSTED_APPROVER_KEY", "t" * 64)
    raw = ApprovalDecision(
        plan_sha256=plan.plan_sha256,
        approver_id="operator/trusted",
        approver_group="attacker/group",
        decision="approve",
        reason="Reviewed.",
        decided_at=NOW + timedelta(minutes=1),
    )
    signed = attest_decision(raw, config, secret=b"t" * 64, nonce="attestation-nonce-00000000")
    assert signed.approver_group == "group/trusted"
    ledger = ApprovalLedger(tmp_path / "attested-ledger")
    ledger.append(signed)
    assert (
        evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=2)).status == "approved"
    )

    forged = signed.model_copy(update={"approver_group": "attacker/group"})
    forged_ledger = ApprovalLedger(tmp_path / "forged-group")
    forged_ledger.append(forged)
    with pytest.raises(ApprovalError, match="group differs"):
        evaluate_approval(plan, forged_ledger, config, at=NOW + timedelta(minutes=2))


def test_approval_registry_rejects_unknown_missing_wrong_and_replayed_attestations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every approval identity property is verified against the trusted registry."""

    plan, _, config = _plan(tmp_path)
    unsigned = ApprovalDecision(
        plan_sha256=plan.plan_sha256,
        approver_id="operator/one",
        approver_group="group/one",
        decision="approve",
        reason="Reviewed.",
        decided_at=NOW + timedelta(minutes=1),
    )
    unknown = unsigned.model_copy(update={"approver_id": "operator/unregistered"})
    unknown_ledger = ApprovalLedger(tmp_path / "approval-unknown")
    unknown_ledger.append(unknown)
    with pytest.raises(ApprovalError, match="trusted registry"):
        evaluate_approval(plan, unknown_ledger, config, at=NOW + timedelta(minutes=2))

    missing_ledger = ApprovalLedger(tmp_path / "approval-missing")
    missing_ledger.append(unsigned)
    with pytest.raises(ApprovalError, match="attestation is missing"):
        evaluate_approval(plan, missing_ledger, config, at=NOW + timedelta(minutes=2))

    signed = attest_decision(unsigned, config, secret=APPROVER_ONE_SECRET, nonce="registry-nonce-a")
    assert signed.attestation is not None
    unknown_key = signed.model_copy(
        update={"attestation": signed.attestation.model_copy(update={"key_id": "unknown-v1"})}
    )
    unknown_key_ledger = ApprovalLedger(tmp_path / "approval-unknown-key")
    unknown_key_ledger.append(unknown_key)
    with pytest.raises(ApprovalError, match="unknown key"):
        evaluate_approval(plan, unknown_key_ledger, config, at=NOW + timedelta(minutes=2))

    wrong_key = attest_decision(
        unsigned, config, secret=APPROVER_TWO_SECRET, nonce="registry-nonce-b"
    )
    wrong_key_ledger = ApprovalLedger(tmp_path / "approval-wrong-key")
    wrong_key_ledger.append(wrong_key)
    with pytest.raises(ApprovalError, match="signature"):
        evaluate_approval(plan, wrong_key_ledger, config, at=NOW + timedelta(minutes=2))

    monkeypatch.delenv("PAIC_HARDENING_APPROVER_ONE_KEY")
    absent_key_ledger = ApprovalLedger(tmp_path / "approval-absent-key")
    absent_key_ledger.append(signed)
    with pytest.raises(ApprovalError, match="key is unavailable"):
        evaluate_approval(plan, absent_key_ledger, config, at=NOW + timedelta(minutes=2))


def test_approval_attestation_nonce_cannot_span_distinct_identities(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    first = ApprovalDecision(
        plan_sha256=plan.plan_sha256,
        approver_id="operator/one",
        approver_group="group/one",
        decision="approve",
        reason="Reviewed.",
        decided_at=NOW + timedelta(minutes=1),
    )
    second = first.model_copy(
        update={
            "approver_id": "operator/two",
            "approver_group": "group/two",
            "decided_at": NOW + timedelta(minutes=2),
        }
    )
    ledger = ApprovalLedger(tmp_path / "approval-duplicate-attestation-nonce")
    nonce = "reused-attestation-nonce-000000"
    ledger.append(attest_decision(first, config, secret=APPROVER_ONE_SECRET, nonce=nonce))
    ledger.append(attest_decision(second, config, secret=APPROVER_TWO_SECRET, nonce=nonce))
    with pytest.raises(ApprovalError, match="nonce is duplicated"):
        evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=3))


def test_approval_policy_requires_unique_trusted_registry_entries() -> None:
    with pytest.raises(ValidationError, match="must contain"):
        ApprovalPolicy()
    identity = ApproverIdentity(
        approver_id="operator/one",
        approver_group="group/one",
        key_id="registry-one-v1",
        key_env="PAIC_HARDENING_APPROVER_ONE_KEY",
    )
    with pytest.raises(ValidationError, match="identities must be unique"):
        ApprovalPolicy(approver_registry=[identity, identity])
    duplicate_key = identity.model_copy(update={"approver_id": "operator/two"})
    with pytest.raises(ValidationError, match="key IDs must be unique"):
        ApprovalPolicy(approver_registry=[identity, duplicate_key])


def test_attestation_and_token_fail_closed_at_secret_status_and_expiry_boundaries(
    tmp_path: Path,
) -> None:
    plan, _, config = _plan(tmp_path)
    unsigned = ApprovalDecision(
        plan_sha256=plan.plan_sha256,
        approver_id="operator/one",
        approver_group="group/one",
        decision="approve",
        reason="Reviewed.",
        decided_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ApprovalError, match="shorter"):
        attest_decision(unsigned, config, secret=b"short")
    ledger = ApprovalLedger(tmp_path / "approval-pending")
    pending = evaluate_approval(plan, ledger, config, at=NOW + timedelta(minutes=2))
    assert pending.status == "pending"
    with pytest.raises(ApprovalError, match="approved plan"):
        issue_token(plan, pending, config, at=NOW + timedelta(minutes=2), secret=b"x" * 64)
    approved_ledger, approved = _approved_status(plan, config, tmp_path / "approval-approved")
    assert approved_ledger.validate()
    with pytest.raises(ApprovalError, match="different plan"):
        issue_token(
            plan,
            approved.model_copy(update={"plan_sha256": "f" * 64}),
            config,
            at=NOW + timedelta(minutes=3),
            secret=b"x" * 64,
        )
    with pytest.raises(ApprovalError, match="expired"):
        issue_token(plan, approved, config, at=plan.expires_at, secret=b"x" * 64)
    with pytest.raises(ApprovalError, match="shorter"):
        verify_token("a.b", plan, approved, config, at=NOW + timedelta(minutes=3), secret=b"x")


def test_approval_evaluation_requires_a_timezone_aware_clock(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    with pytest.raises(ApprovalError, match="timezone"):
        evaluate_approval(
            plan, ApprovalLedger(tmp_path / "approval-naive"), config, at=NOW.replace(tzinfo=None)
        )


def test_token_parser_rejects_signed_invalid_payloads_and_revoked_status(tmp_path: Path) -> None:
    plan, _, config = _plan(tmp_path)
    _, status = _approved_status(plan, config, tmp_path / "approval-token-parser")
    at = NOW + timedelta(minutes=3)
    token = issue_token(plan, status, config, at=at, secret=b"z" * 64)
    with pytest.raises(ApprovalError, match="no longer approved"):
        verify_token(
            token,
            plan,
            status.model_copy(update={"status": "pending"}),
            config,
            at=at,
            secret=b"z" * 64,
        )
    payload = b"not-json"
    malformed = (
        base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(hmac.new(b"z" * 64, payload, hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(ApprovalError, match="payload"):
        verify_token(malformed, plan, status, config, at=at, secret=b"z" * 64)
    duplicate_payload = b'{"schema_version":"1.0","schema_version":"1.0"}'
    duplicate = (
        base64.urlsafe_b64encode(duplicate_payload).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(hmac.new(b"z" * 64, duplicate_payload, hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(ApprovalError, match="payload"):
        verify_token(duplicate, plan, status, config, at=at, secret=b"z" * 64)
