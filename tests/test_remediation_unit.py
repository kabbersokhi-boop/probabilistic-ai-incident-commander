from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from paic.investigation.config import DecisionPolicy
from paic.investigation.models import InvestigationProposal, InvestigationReport
from paic.investigation.probability import score_proposal
from paic.remediation.approval import (
    ApprovalError,
    ApprovalLedger,
    evaluate_approval,
    issue_token,
)
from paic.remediation.artifact import (
    export_control_state,
    export_execution,
    export_plan,
    manifest_sha256,
    validate_control_state,
    validate_execution,
    validate_plan,
)
from paic.remediation.config import RemediationConfig
from paic.remediation.executor import (
    ExecutionError,
    _apply_action,
    build_rollback_proposal,
    execute_plan,
)
from paic.remediation.models import (
    ApprovalDecision,
    ConfigurationResource,
    ControlState,
    ExecutionRequest,
    FeatureFlagResource,
    RemediationPlan,
    RemediationProposal,
    RestoreConfigurationAction,
    SetFeatureFlagAction,
)
from paic.remediation.policy import assess_proposal, build_plan

NOW = datetime(2026, 7, 18, tzinfo=UTC)
SECRET = b"s" * 64


def _config() -> RemediationConfig:
    return RemediationConfig(policy_id="unit-remediation")


def _report(*, confidence_policy: DecisionPolicy | None = None) -> InvestigationReport:
    proposal = InvestigationProposal.model_validate(
        {
            "summary": "The primary service change is most likely.",
            "hypotheses": [
                {
                    "hypothesis_id": "primary-service-change",
                    "title": "Primary service change",
                    "prior_probability": 0.5,
                    "rationale": "Two records support the change.",
                    "evidence": [
                        {
                            "evidence_record_id": "evidence-one",
                            "direction": "support",
                            "likelihood_ratio": 8.0,
                            "explanation": "Temporal match.",
                        },
                        {
                            "evidence_record_id": "evidence-two",
                            "direction": "support",
                            "likelihood_ratio": 6.0,
                            "explanation": "Cohort match.",
                        },
                    ],
                    "falsifiers": ["No recovery after rollback."],
                },
                {
                    "hypothesis_id": "other-cause",
                    "title": "Other cause",
                    "prior_probability": 0.5,
                    "rationale": "Competing cause.",
                    "evidence": [
                        {
                            "evidence_record_id": "evidence-two",
                            "direction": "contradict",
                            "likelihood_ratio": 0.1,
                            "explanation": "Evidence points away.",
                        }
                    ],
                    "falsifiers": ["Other service errors lead the incident."],
                },
            ],
            "explicit_unknowns": ["Recovery is not yet measured."],
            "recommended_next_steps": ["Verify recovery after an approved rollback."],
        }
    )
    return score_proposal(
        proposal,
        investigation_id="investigation-unit",
        incident_id="incident-unit",
        question="What caused the incident?",
        policy=confidence_policy
        or DecisionPolicy(
            minimum_top_posterior=0.55,
            minimum_margin=0.15,
            minimum_distinct_evidence=2,
            maximum_normalized_entropy=0.85,
        ),
        observed_evidence={"evidence-one", "evidence-two"},
        source_hashes={"dataset": "d" * 64},
        attempts=[],
        trace=[],
        total_tokens=0,
    )


def _state() -> ControlState:
    return ControlState.model_validate(
        {
            "state_id": "control-unit",
            "incident_id": "incident-unit",
            "generation": 0,
            "resources": [
                {
                    "resource_type": "deployment",
                    "resource_id": "service/checkout",
                    "current_revision": "bad",
                    "available_revisions": ["good", "bad"],
                },
                {
                    "resource_type": "feature_flag",
                    "resource_id": "flag/strict",
                    "enabled": True,
                },
            ],
        }
    )


def _proposal(report: InvestigationReport | None = None) -> RemediationProposal:
    report = report or _report()
    return RemediationProposal.model_validate(
        {
            "remediation_id": "remediation-unit",
            "incident_id": report.incident_id,
            "investigation_report_sha256": report.report_sha256,
            "selected_hypothesis_id": report.selected_hypothesis_id,
            "requested_by": "agent/planner",
            "requested_at": NOW.isoformat(),
            "summary": "Roll back checkout.",
            "expected_outcome": "Checkout recovers.",
            "rollback_trigger": "Guardrails degrade.",
            "actions": [
                {
                    "action_type": "deployment.rollback",
                    "action_id": "rollback-checkout",
                    "blast_radius": "single_service",
                    "evidence_ids": ["evidence-one", "evidence-two"],
                    "justification": "Evidence supports the implicated deployment.",
                    "resource_id": "service/checkout",
                    "expected_current_revision": "bad",
                    "target_revision": "good",
                }
            ],
        }
    )


def _plan_artifacts(
    tmp_path: Path,
) -> tuple[InvestigationReport, ControlState, Path, RemediationPlan, Path]:
    report = _report()
    state = _state()
    state_dir = tmp_path / "state"
    export_control_state(state, state_dir)
    plan = build_plan(
        report,
        state,
        _proposal(report),
        _config(),
        investigation_manifest_sha256="c" * 64,
        control_state_manifest_sha256=manifest_sha256(state_dir),
    )
    plan_dir = tmp_path / "plan"
    export_plan(_config(), _proposal(report), plan, plan_dir)
    return report, state, state_dir, plan, plan_dir


def _approve(plan: RemediationPlan, approval_dir: Path) -> ApprovalLedger:
    ledger = ApprovalLedger(approval_dir)
    for index, (approver, group) in enumerate(
        (("operator/one", "group/one"), ("operator/two", "group/two")), 1
    ):
        ledger.append(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id=approver,
                approver_group=group,
                decision="approve",
                reason="Approved after independent review.",
                decided_at=NOW + timedelta(minutes=index),
            )
        )
    return ledger


def test_policy_allows_reversible_high_risk_plan() -> None:
    decision = assess_proposal(_report(), _state(), _proposal(), _config())
    assert decision.outcome == "allow"
    assert decision.risk_level == "high"
    assert decision.required_approvals == 2


def test_policy_denies_unobserved_evidence_and_stale_state() -> None:
    raw = _proposal().model_dump(mode="json")
    raw["actions"][0]["evidence_ids"] = ["not-observed"]
    raw["actions"][0]["expected_current_revision"] = "older"
    decision = assess_proposal(
        _report(), _state(), RemediationProposal.model_validate(raw), _config()
    )
    assert decision.outcome == "deny"
    assert any("unobserved" in reason for reason in decision.reasons)
    assert any("stale" in reason for reason in decision.reasons)


def test_feature_flag_and_configuration_actions_have_exact_inverses() -> None:
    flag = FeatureFlagResource(resource_id="flag/test", enabled=True)
    flag_action = SetFeatureFlagAction(
        action_id="disable-flag",
        blast_radius="single_service",
        evidence_ids=["evidence-one"],
        justification="Synthetic test.",
        resource_id="flag/test",
        expected_enabled=True,
        desired_enabled=False,
    )
    after_flag, inverse_flag = _apply_action(flag_action, flag)
    assert isinstance(after_flag, FeatureFlagResource)
    assert after_flag.enabled is False
    restored_flag, _ = _apply_action(inverse_flag, after_flag)
    assert restored_flag == flag

    configuration = ConfigurationResource(
        resource_id="config/checkout", current_version="bad", available_versions=["good", "bad"]
    )
    configuration_action = RestoreConfigurationAction(
        action_id="restore-config",
        blast_radius="single_service",
        evidence_ids=["evidence-one"],
        justification="Synthetic test.",
        resource_id="config/checkout",
        expected_current_version="bad",
        target_version="good",
    )
    after_configuration, inverse_configuration = _apply_action(configuration_action, configuration)
    assert isinstance(after_configuration, ConfigurationResource)
    assert after_configuration.current_version == "good"
    restored_configuration, _ = _apply_action(inverse_configuration, after_configuration)
    assert restored_configuration == configuration


def test_plan_and_state_artifacts_are_closed_world(tmp_path: Path) -> None:
    _, _, state_dir, _, plan_dir = _plan_artifacts(tmp_path)
    assert validate_control_state(state_dir) == []
    assert validate_plan(plan_dir) == []
    (state_dir / "extra.txt").write_text("unexpected", encoding="utf-8")
    assert validate_control_state(state_dir)


def test_requester_cannot_self_approve(tmp_path: Path) -> None:
    _, _, _, plan, _ = _plan_artifacts(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval")
    ledger.append(
        ApprovalDecision(
            plan_sha256=plan.plan_sha256,
            approver_id=plan.requested_by,
            approver_group="group/requester",
            decision="approve",
            reason="Self approval is forbidden.",
            decided_at=NOW + timedelta(minutes=1),
        )
    )
    with pytest.raises(ApprovalError, match="may not approve"):
        evaluate_approval(plan, ledger, _config(), at=NOW + timedelta(minutes=2))


def test_rejection_vetoes_approval(tmp_path: Path) -> None:
    _, _, _, plan, _ = _plan_artifacts(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval")
    ledger.append(
        ApprovalDecision(
            plan_sha256=plan.plan_sha256,
            approver_id="operator/rejector",
            approver_group="group/rejector",
            decision="reject",
            reason="Precondition requires more evidence.",
            decided_at=NOW + timedelta(minutes=1),
        )
    )
    status = evaluate_approval(plan, ledger, _config(), at=NOW + timedelta(minutes=2))
    assert status.status == "rejected"


def test_token_and_execution_are_bound_and_replay_safe(tmp_path: Path) -> None:
    _, state, state_dir, plan, plan_dir = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(
        plan,
        status,
        _config(),
        at=at,
        secret=SECRET,
        nonce="unit-test-nonce-000000000000",
        token_id="approval-unit",
    )
    request = ExecutionRequest(
        execution_id="execution-unit",
        executed_by="operator/executor",
        executed_at=NOW + timedelta(minutes=4),
    )
    after, receipt = execute_plan(
        plan,
        state,
        status,
        _config(),
        request,
        token=token,
        secret=SECRET,
        before_state_manifest_sha256=manifest_sha256(state_dir),
    )
    assert after.generation == 1
    deployment = next(item for item in after.resources if item.resource_id == "service/checkout")
    assert deployment.current_revision == "good"  # type: ignore[union-attr]
    with pytest.raises(ExecutionError, match="already executed"):
        execute_plan(
            plan,
            after,
            status,
            _config(),
            request.model_copy(update={"execution_id": "execution-repeat"}),
            token=token,
            secret=SECRET,
            before_state_manifest_sha256=plan.control_state_manifest_sha256,
        )
    after_state_dir = tmp_path / "state-after"
    export_control_state(after, after_state_dir)
    execution_dir = tmp_path / "execution"
    export_execution(receipt, execution_dir)
    assert (
        validate_execution(
            execution_dir,
            plan_dir=plan_dir,
            before_state_dir=state_dir,
            after_state_dir=after_state_dir,
        )
        == []
    )
    tampered = after.model_copy(update={"generation": after.generation + 1})
    tampered_dir = tmp_path / "state-after-tampered"
    export_control_state(tampered, tampered_dir)
    assert any(
        "generation" in issue or "after-state" in issue
        for issue in validate_execution(
            execution_dir,
            plan_dir=plan_dir,
            before_state_dir=state_dir,
            after_state_dir=tampered_dir,
        )
    )


def test_tampered_token_is_rejected(tmp_path: Path) -> None:
    _, state, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(plan, status, _config(), at=at, secret=SECRET)
    with pytest.raises(ExecutionError, match="signature"):
        execute_plan(
            plan,
            state,
            status,
            _config(),
            ExecutionRequest(
                execution_id="execution-tampered",
                executed_by="operator/executor",
                executed_at=NOW + timedelta(minutes=4),
            ),
            token=token[:-1] + ("A" if token[-1] != "A" else "B"),
            secret=SECRET,
            before_state_manifest_sha256=manifest_sha256(state_dir),
        )


def test_rollback_is_a_fresh_proposal_requiring_approval(tmp_path: Path) -> None:
    _, state, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval")
    status = evaluate_approval(plan, ledger, _config(), at=NOW + timedelta(minutes=3))
    token = issue_token(plan, status, _config(), at=NOW + timedelta(minutes=3), secret=SECRET)
    after, receipt = execute_plan(
        plan,
        state,
        status,
        _config(),
        ExecutionRequest(
            execution_id="execution-for-rollback",
            executed_by="operator/executor",
            executed_at=NOW + timedelta(minutes=4),
        ),
        token=token,
        secret=SECRET,
        before_state_manifest_sha256=manifest_sha256(state_dir),
    )
    rollback = build_rollback_proposal(
        plan,
        receipt,
        requested_by="operator/rollback-requester",
        requested_at=NOW + timedelta(minutes=5),
    )
    assert rollback.actions[0].target_revision == "bad"  # type: ignore[union-attr]
    assert after.executed_plan_hashes == [plan.plan_sha256]
