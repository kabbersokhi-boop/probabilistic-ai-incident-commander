from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from paic.investigation.config import DecisionPolicy
from paic.investigation.models import InvestigationProposal, InvestigationReport
from paic.investigation.probability import score_proposal
from paic.remediation import state_store
from paic.remediation.approval import (
    ApprovalError,
    ApprovalLedger,
    attest_decision,
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
from paic.remediation.config import ApprovalPolicy, ApproverIdentity, RemediationConfig
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
from paic.remediation.state_store import ControlStateStore, StateStoreError
from paic.tools.ledger import canonical

NOW = datetime(2026, 7, 18, tzinfo=UTC)
SECRET = b"s" * 64
APPROVER_ONE_SECRET = b"1" * 64
APPROVER_TWO_SECRET = b"2" * 64
APPROVER_REJECTOR_SECRET = b"3" * 64
REQUESTER_SECRET = b"4" * 64


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _approval_identity_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAIC_UNIT_APPROVER_ONE_KEY", APPROVER_ONE_SECRET.decode("ascii"))
    monkeypatch.setenv("PAIC_UNIT_APPROVER_TWO_KEY", APPROVER_TWO_SECRET.decode("ascii"))
    monkeypatch.setenv("PAIC_UNIT_APPROVER_REJECTOR_KEY", APPROVER_REJECTOR_SECRET.decode("ascii"))
    monkeypatch.setenv("PAIC_UNIT_REQUESTER_KEY", REQUESTER_SECRET.decode("ascii"))


def _config() -> RemediationConfig:
    return RemediationConfig(
        policy_id="unit-remediation",
        approval=ApprovalPolicy(
            approver_registry=[
                ApproverIdentity(
                    approver_id="operator/one",
                    approver_group="group/one",
                    key_id="unit-one-v1",
                    key_env="PAIC_UNIT_APPROVER_ONE_KEY",
                ),
                ApproverIdentity(
                    approver_id="operator/rejector",
                    approver_group="group/rejector",
                    key_id="unit-rejector-v1",
                    key_env="PAIC_UNIT_APPROVER_REJECTOR_KEY",
                ),
                ApproverIdentity(
                    approver_id="agent/planner",
                    approver_group="group/requester",
                    key_id="unit-requester-v1",
                    key_env="PAIC_UNIT_REQUESTER_KEY",
                ),
                ApproverIdentity(
                    approver_id="operator/two",
                    approver_group="group/two",
                    key_id="unit-two-v1",
                    key_env="PAIC_UNIT_APPROVER_TWO_KEY",
                ),
            ]
        ),
    )


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
    config = _config()
    for index, (approver, group, secret) in enumerate(
        (
            ("operator/one", "group/one", APPROVER_ONE_SECRET),
            ("operator/two", "group/two", APPROVER_TWO_SECRET),
        ),
        1,
    ):
        ledger.append(
            attest_decision(
                ApprovalDecision(
                    plan_sha256=plan.plan_sha256,
                    approver_id=approver,
                    approver_group=group,
                    decision="approve",
                    reason="Approved after independent review.",
                    decided_at=NOW + timedelta(minutes=index),
                ),
                config,
                secret=secret,
                nonce=f"unit-attestation-{index:024d}",
            )
        )
    return ledger


def _attest(decision: ApprovalDecision) -> ApprovalDecision:
    secrets_by_identity = {
        "operator/one": APPROVER_ONE_SECRET,
        "operator/two": APPROVER_TWO_SECRET,
        "operator/rejector": APPROVER_REJECTOR_SECRET,
        "agent/planner": REQUESTER_SECRET,
    }
    return attest_decision(
        decision,
        _config(),
        secret=secrets_by_identity[decision.approver_id],
        nonce=f"unit-{decision.approver_id.replace('/', '-')}-attestation-000000",
    )


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
        _attest(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id=plan.requested_by,
                approver_group="group/requester",
                decision="approve",
                reason="Self approval is forbidden.",
                decided_at=NOW + timedelta(minutes=1),
            )
        )
    )
    with pytest.raises(ApprovalError, match="may not approve"):
        evaluate_approval(plan, ledger, _config(), at=NOW + timedelta(minutes=2))


def test_rejection_vetoes_approval(tmp_path: Path) -> None:
    _, _, _, plan, _ = _plan_artifacts(tmp_path)
    ledger = ApprovalLedger(tmp_path / "approval")
    ledger.append(
        _attest(
            ApprovalDecision(
                plan_sha256=plan.plan_sha256,
                approver_id="operator/rejector",
                approver_group="group/rejector",
                decision="reject",
                reason="Precondition requires more evidence.",
                decided_at=NOW + timedelta(minutes=1),
            )
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
    with pytest.raises(ExecutionError, match=r"already executed|differs from the plan binding"):
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


def test_control_state_store_consumes_plan_and_nonce_once_across_output_paths(
    tmp_path: Path,
) -> None:
    """The canonical store, not a caller-provided export, owns replay state."""

    _, _, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-store")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(
        plan,
        status,
        _config(),
        at=at,
        secret=SECRET,
        nonce="store-nonce-000000000000000",
    )
    store = ControlStateStore(tmp_path / "store")
    store.initialize(state_dir)
    request = ExecutionRequest(
        execution_id="execution-store-one",
        executed_by="operator/executor",
        executed_at=NOW + timedelta(minutes=4),
    )
    transaction, _receipt = store.execute(
        plan, ledger, _config(), request, token=token, secret=SECRET
    )
    assert (transaction / "state" / "_SUCCESS").is_file()
    assert (transaction / "execution" / "_SUCCESS").is_file()
    assert store.validate() == []

    # Supplying the original state export cannot reset the store lineage.
    store.initialize(state_dir)
    with pytest.raises(ExecutionError, match=r"already executed|differs from the plan binding"):
        store.execute(
            plan,
            ledger,
            _config(),
            request.model_copy(update={"execution_id": "execution-store-repeat"}),
            token=token,
            secret=SECRET,
        )
    assert store.validate() == []


def test_control_state_store_serializes_concurrent_same_plan_execution(tmp_path: Path) -> None:
    _, _, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-concurrent")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(
        plan,
        status,
        _config(),
        at=at,
        secret=SECRET,
        nonce="concurrent-nonce-0000000000",
    )
    store = ControlStateStore(tmp_path / "concurrent-store")
    store.initialize(state_dir)

    def invoke(index: int) -> str:
        try:
            store.execute(
                plan,
                ledger,
                _config(),
                ExecutionRequest(
                    execution_id=f"execution-concurrent-{index}",
                    executed_by="operator/executor",
                    executed_at=NOW + timedelta(minutes=4),
                ),
                token=token,
                secret=SECRET,
            )
        except ExecutionError as exc:
            return str(exc)
        return "success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(invoke, range(2)))
    assert outcomes.count("success") == 1
    assert any("already executed" in outcome for outcome in outcomes)
    assert store.validate() == []


def test_control_state_store_rejects_a_different_initial_artifact(tmp_path: Path) -> None:
    _, _, state_dir, _, _ = _plan_artifacts(tmp_path)
    store = ControlStateStore(tmp_path / "bound-store")
    store.initialize(state_dir)
    other = _state().model_copy(update={"state_id": "other-control"})
    other_dir = tmp_path / "other-state"
    export_control_state(other, other_dir)
    with pytest.raises(StateStoreError, match="bound to another"):
        store.initialize(other_dir)


def test_control_state_store_rejects_unsafe_and_corrupt_metadata(tmp_path: Path) -> None:
    root_file = tmp_path / "root-file"
    root_file.write_text("not a store", encoding="utf-8")
    with pytest.raises(StateStoreError, match="regular directory"):
        ControlStateStore(root_file).current_state_dir()

    store = ControlStateStore(tmp_path / "corrupt-store")
    with pytest.raises(StateStoreError, match="not initialized"):
        store.current_state_dir()
    store.generations.mkdir(exist_ok=True)
    store.meta_path.write_text("[]", encoding="utf-8")
    store.current_path.write_text("{bad", encoding="utf-8")
    assert store.validate() == ["control-state store metadata is invalid"]


def test_control_state_store_orphan_and_pointer_tampering_fail_closed(tmp_path: Path) -> None:
    _, _, state_dir, _, _ = _plan_artifacts(tmp_path)
    orphan = ControlStateStore(tmp_path / "orphan-store")
    orphan._ensure_regular_root()
    (orphan.generations / "00000000000000000000").mkdir()
    with pytest.raises(StateStoreError, match="orphan"):
        orphan.initialize(state_dir)

    store = ControlStateStore(tmp_path / "pointer-store")
    store.initialize(state_dir)
    store.current_path.write_text(
        '{"generation": 0, "directory": "../escape", "receipt_sha256": null}',
        encoding="utf-8",
    )
    assert store.validate() == ["control-state store pointer is unsafe"]


def test_state_store_commit_and_recovery_paths_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-failure")
    status = evaluate_approval(plan, ledger, _config(), at=NOW + timedelta(minutes=3))
    token = issue_token(plan, status, _config(), at=NOW + timedelta(minutes=3), secret=SECRET)
    store = ControlStateStore(tmp_path / "failure-store")
    store.initialize(state_dir)
    original_export = state_store.__dict__["export_execution"]

    def fail_receipt(*args: object, **kwargs: object) -> object:
        raise OSError("injected receipt staging failure")

    monkeypatch.setattr(state_store, "export_execution", fail_receipt)
    with pytest.raises(OSError, match="injected"):
        store.execute(
            plan,
            ledger,
            _config(),
            ExecutionRequest(
                execution_id="execution-stage-failure",
                executed_by="operator/executor",
                executed_at=NOW + timedelta(minutes=4),
            ),
            token=token,
            secret=SECRET,
        )
    assert store.current_state_dir().name == "state"
    assert store.validate() == []
    monkeypatch.setattr(state_store, "export_execution", original_export)


def test_pointer_fsync_failure_after_replace_is_not_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "pointer.json"
    original = state_store._fsync_dir

    def fail_fsync(_path: Path) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(state_store, "_fsync_dir", fail_fsync)
    state_store._write_json_atomic(target, {"generation": 1})
    assert target.exists()
    monkeypatch.setattr(state_store, "_fsync_dir", original)


def test_control_state_store_rejects_malformed_pointer_and_generation_layout(
    tmp_path: Path,
) -> None:
    """The pointer is the only commit point and must reference a contiguous lineage."""

    _, _, state_dir, _, _ = _plan_artifacts(tmp_path)
    store = ControlStateStore(tmp_path / "layout-store")
    store.initialize(state_dir)

    store.current_path.write_text(
        '{"generation": 0, "directory": "00000000000000000000", "receipt_sha256": "x"}',
        encoding="utf-8",
    )
    assert store.validate() == ["control-state store pointer is unsafe"]

    store.current_path.write_text(
        '{"generation": 0, "directory": "00000000000000000000", "receipt_sha256": null}',
        encoding="utf-8",
    )
    (store.generations / "00000000000000000002-deadbeefdeadbeef").mkdir()
    assert store.validate() == []
    store.current_path.write_text(
        '{"generation": 2, "directory": "00000000000000000002-deadbeefdeadbeef", '
        '"receipt_sha256": "' + "0" * 64 + '"}',
        encoding="utf-8",
    )
    assert store.validate() == ["control-state store generations are not contiguous"]


def test_control_state_store_rejects_pointer_receipt_and_state_binding_drift(
    tmp_path: Path,
) -> None:
    _, _, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-binding")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(plan, status, _config(), at=at, secret=SECRET)
    store = ControlStateStore(tmp_path / "binding-store")
    store.initialize(state_dir)
    store.execute(
        plan,
        ledger,
        _config(),
        ExecutionRequest(
            execution_id="execution-binding",
            executed_by="operator/executor",
            executed_at=NOW + timedelta(minutes=4),
        ),
        token=token,
        secret=SECRET,
    )
    pointer = json.loads(store.current_path.read_text(encoding="utf-8"))
    pointer["receipt_sha256"] = "0" * 64
    store.current_path.write_text(canonical(pointer) + "\n", encoding="utf-8")
    assert store.validate() == ["current receipt does not match pointer"]


def test_execution_rejects_a_rehashed_plan_model_mutation(tmp_path: Path) -> None:
    """A caller cannot retain the old plan hash after changing a structured action."""

    _, state, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-plan-integrity")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(plan, status, _config(), at=at, secret=SECRET)
    altered_action = plan.actions[0].model_copy(update={"target_revision": "bad"})
    altered_plan = plan.model_copy(update={"actions": [altered_action]})
    with pytest.raises(ExecutionError, match="integrity"):
        execute_plan(
            altered_plan,
            state,
            status,
            _config(),
            ExecutionRequest(
                execution_id="execution-mutated-plan",
                executed_by="operator/executor",
                executed_at=NOW + timedelta(minutes=4),
            ),
            token=token,
            secret=SECRET,
            before_state_manifest_sha256=manifest_sha256(state_dir),
        )


def test_store_recovers_an_orphan_generation_after_pre_pointer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renamed transaction is inert until the pointer commits and retry cleans it."""

    _, _, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-orphan")
    at = NOW + timedelta(minutes=3)
    status = evaluate_approval(plan, ledger, _config(), at=at)
    token = issue_token(plan, status, _config(), at=at, secret=SECRET)
    store = ControlStateStore(tmp_path / "orphan-recovery-store")
    store.initialize(state_dir)
    request = ExecutionRequest(
        execution_id="execution-orphan-recovery",
        executed_by="operator/executor",
        executed_at=NOW + timedelta(minutes=4),
    )
    original_fsync = state_store._fsync_dir

    def fail_generation_fsync(path: Path) -> None:
        if path == store.generations:
            raise OSError("injected generation fsync failure")
        original_fsync(path)

    monkeypatch.setattr(state_store, "_fsync_dir", fail_generation_fsync)
    with pytest.raises(OSError, match="generation fsync"):
        store.execute(plan, ledger, _config(), request, token=token, secret=SECRET)
    assert store.validate() == []
    assert len(list(store.generations.iterdir())) == 2

    monkeypatch.setattr(state_store, "_fsync_dir", original_fsync)
    transaction, _ = store.execute(plan, ledger, _config(), request, token=token, secret=SECRET)
    assert transaction.name.startswith("00000000000000000001-")
    assert store.validate() == []
    assert len(list(store.generations.iterdir())) == 2


def test_store_rejects_invalid_state_identity_and_generation_before_authorization(
    tmp_path: Path,
) -> None:
    _, state, state_dir, plan, _ = _plan_artifacts(tmp_path)
    store = ControlStateStore(tmp_path / "store-state-drift")
    store.initialize(state_dir)
    altered = state.model_copy(update={"incident_id": "other-incident"})
    export_control_state(altered, store.current_state_dir(), overwrite=True)
    ledger = _approve(plan, tmp_path / "approval-state-drift")
    token = issue_token(
        plan,
        evaluate_approval(plan, ledger, _config(), at=NOW + timedelta(minutes=3)),
        _config(),
        at=NOW + timedelta(minutes=3),
        secret=SECRET,
    )
    with pytest.raises(StateStoreError, match="store identity"):
        store.execute(
            plan,
            ledger,
            _config(),
            ExecutionRequest(
                execution_id="execution-state-drift",
                executed_by="operator/executor",
                executed_at=NOW + timedelta(minutes=4),
            ),
            token=token,
            secret=SECRET,
        )


def test_store_validation_rejects_nonobject_pointer_missing_generation_and_state_generation_drift(
    tmp_path: Path,
) -> None:
    _, state, state_dir, _, _ = _plan_artifacts(tmp_path)
    store = ControlStateStore(tmp_path / "store-pointer-validation")
    store.initialize(state_dir)
    store.current_path.write_text("[]", encoding="utf-8")
    assert store.validate() == ["control-state store metadata is invalid"]

    store.current_path.write_text(
        '{"generation": 0, "directory": "00000000000000000000", "receipt_sha256": null}',
        encoding="utf-8",
    )
    (store.generations / "00000000000000000000").rename(store.generations / "removed-generation")
    assert store.validate() == ["current control-state generation is missing"]

    repaired = ControlStateStore(tmp_path / "store-generation-validation")
    repaired.initialize(state_dir)
    export_control_state(
        state.model_copy(update={"generation": 1}),
        repaired.current_state_dir(),
        overwrite=True,
    )
    assert repaired.validate() == ["current state generation does not match pointer"]


def test_store_precommit_validation_failure_keeps_the_original_generation_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, state_dir, plan, _ = _plan_artifacts(tmp_path)
    ledger = _approve(plan, tmp_path / "approval-precommit-validation")
    at = NOW + timedelta(minutes=3)
    token = issue_token(
        plan,
        evaluate_approval(plan, ledger, _config(), at=at),
        _config(),
        at=at,
        secret=SECRET,
    )
    store = ControlStateStore(tmp_path / "store-precommit-validation")
    store.initialize(state_dir)
    monkeypatch.setattr(state_store, "validate_execution", lambda *args, **kwargs: ["injected"])
    with pytest.raises(StateStoreError, match="prepared transaction"):
        store.execute(
            plan,
            ledger,
            _config(),
            ExecutionRequest(
                execution_id="execution-precommit-validation",
                executed_by="operator/executor",
                executed_at=NOW + timedelta(minutes=4),
            ),
            token=token,
            secret=SECRET,
        )
    assert store.validate() == []
    assert not list(store.root.glob(".prepare-*"))


def test_pointer_publish_reports_unrecoverable_post_commit_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "unreadable-pointer.json"

    def fail_fsync(_path: Path) -> None:
        raise OSError("injected fsync failure")

    def fail_json_load(_value: str) -> object:
        raise ValueError("injected unreadable pointer")

    monkeypatch.setattr(state_store, "_fsync_dir", fail_fsync)
    monkeypatch.setattr("paic.remediation.state_store.json.loads", fail_json_load)
    with pytest.raises(StateStoreError, match="cannot durably publish"):
        state_store._write_json_atomic(target, {"generation": 1})
    assert target.exists()


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
