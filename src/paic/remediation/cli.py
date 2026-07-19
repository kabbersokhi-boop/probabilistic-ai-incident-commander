"""CLI integration for governed remediation and approval."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paic.investigation.artifact import replay_investigation
from paic.remediation.approval import (
    ApprovalError,
    ApprovalLedger,
    attest_decision,
    evaluate_approval,
    issue_token,
    load_approval_secret,
)
from paic.remediation.artifact import (
    RemediationArtifactError,
    export_control_state,
    export_execution,
    export_plan,
    load_control_state,
    load_execution,
    load_plan,
    manifest_sha256,
    validate_control_state,
    validate_execution,
    validate_plan,
)
from paic.remediation.config import (
    RemediationConfigError,
    load_remediation_config,
)
from paic.remediation.executor import ExecutionError, build_rollback_proposal
from paic.remediation.models import (
    ApprovalDecision,
    ControlState,
    ExecutionRequest,
    RemediationProposal,
)
from paic.remediation.policy import build_plan
from paic.remediation.state_store import ControlStateStore, StateStoreError


def _parse_time(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    result = datetime.fromisoformat(normalized)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return result


def _write_json(path: Path, value: Any, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    if mode is not None:
        os.chmod(path, mode)


def _state_build(args: argparse.Namespace) -> int:
    try:
        state = ControlState.model_validate_json(args.input.read_text(encoding="utf-8"))
        manifest = export_control_state(state, args.output_dir, overwrite=args.overwrite)
    except (OSError, ValueError, RemediationArtifactError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"success": True, "manifest": manifest.model_dump(mode="json")}, indent=2))
    return 0


def _state_validate(args: argparse.Namespace) -> int:
    issues = validate_control_state(args.state_dir)
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _store_validate(args: argparse.Namespace) -> int:
    issues = ControlStateStore(args.state_store).validate()
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _plan_build(args: argparse.Namespace) -> int:
    try:
        config = load_remediation_config(args.config)
        report = replay_investigation(
            args.investigation_dir,
            dataset_dir=args.dataset_dir,
            analytics_dir=args.analytics_dir,
            detection_dir=args.detection_dir,
            impact_dir=args.impact_dir,
            evidence_dir=args.evidence_dir,
            config_path=args.investigation_config,
        )
        loaded_state = load_control_state(args.state_dir)
        proposal = RemediationProposal.model_validate_json(
            args.proposal.read_text(encoding="utf-8")
        )
        plan = build_plan(
            report,
            loaded_state.state,
            proposal,
            config,
            investigation_manifest_sha256=manifest_sha256(args.investigation_dir),
            control_state_manifest_sha256=manifest_sha256(args.state_dir),
        )
        manifest = export_plan(config, proposal, plan, args.output_dir, overwrite=args.overwrite)
    except (
        OSError,
        ValueError,
        RemediationConfigError,
        RemediationArtifactError,
        RuntimeError,
    ) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "success": True,
                "status": plan.status,
                "risk_level": plan.risk_level,
                "required_approvals": plan.required_approvals,
                "plan_sha256": plan.plan_sha256,
                "manifest": manifest.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if plan.status == "awaiting_approval" else 1


def _plan_validate(args: argparse.Namespace) -> int:
    issues = validate_plan(
        args.plan_dir,
        investigation_dir=args.investigation_dir,
        control_state_dir=args.state_dir,
        dataset_dir=args.dataset_dir,
        analytics_dir=args.analytics_dir,
        detection_dir=args.detection_dir,
        impact_dir=args.impact_dir,
        evidence_dir=args.evidence_dir,
        investigation_config_path=args.investigation_config,
    )
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _approval_record(args: argparse.Namespace) -> int:
    try:
        loaded = load_plan(args.plan_dir)
        decision = ApprovalDecision.model_validate_json(args.decision.read_text(encoding="utf-8"))
        if decision.plan_sha256 != loaded.plan.plan_sha256:
            raise ApprovalError("approval decision is bound to a different plan")
        if loaded.config.approval.approver_registry:
            identity = next(
                (
                    item
                    for item in loaded.config.approval.approver_registry
                    if item.approver_id == decision.approver_id
                ),
                None,
            )
            if identity is None:
                raise ApprovalError("approver identity is not in the trusted registry")
            value = os.environ.get(identity.key_env)
            if value is None:
                raise ApprovalError("approval attestation key is unavailable")
            decision = attest_decision(decision, loaded.config, secret=value.encode("utf-8"))
        record = ApprovalLedger(args.approval_dir).append(decision)
    except (OSError, ValueError, RemediationArtifactError, ApprovalError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(record.model_dump_json(indent=2))
    return 0


def _approval_status(args: argparse.Namespace) -> int:
    try:
        loaded = load_plan(args.plan_dir)
        status = evaluate_approval(
            loaded.plan,
            ApprovalLedger(args.approval_dir),
            loaded.config,
            at=_parse_time(args.at),
        )
    except (ValueError, RemediationArtifactError, ApprovalError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(status.model_dump_json(indent=2))
    return 0 if status.status == "approved" else 1


def _token_issue(args: argparse.Namespace) -> int:
    try:
        loaded = load_plan(args.plan_dir)
        at = _parse_time(args.at)
        status = evaluate_approval(
            loaded.plan,
            ApprovalLedger(args.approval_dir),
            loaded.config,
            at=at,
        )
        secret = load_approval_secret(loaded.config)
        token = issue_token(loaded.plan, status, loaded.config, at=at, secret=secret)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(token + "\n", encoding="utf-8")
        os.chmod(args.output, 0o600)
    except (OSError, ValueError, RemediationArtifactError, ApprovalError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"success": True, "token_file": str(args.output), "mode": "0600"}, indent=2))
    return 0


def _execute(args: argparse.Namespace) -> int:
    try:
        loaded_plan = load_plan(args.plan_dir)
        request = ExecutionRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
        token = args.token_file.read_text(encoding="utf-8").strip()
        secret = load_approval_secret(loaded_plan.config)
        store_root = args.state_store or args.state_dir.parent / f".{args.state_dir.name}.lineage"
        store = ControlStateStore(store_root)
        store.initialize(args.state_dir)
        transaction_dir, receipt = store.execute(
            loaded_plan.plan,
            ApprovalLedger(args.approval_dir),
            loaded_plan.config,
            request,
            token=token,
            secret=secret,
        )
        # Compatibility exports are copies only; authorization and replay
        # protection are owned exclusively by the canonical transaction store.
        after_state = load_control_state(transaction_dir / "state").state
        state_manifest = export_control_state(
            after_state, args.output_state_dir, overwrite=args.overwrite
        )
        execution_manifest = export_execution(receipt, args.output_dir, overwrite=args.overwrite)
    except (
        OSError,
        ValueError,
        RemediationArtifactError,
        ApprovalError,
        ExecutionError,
        StateStoreError,
    ) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "success": True,
                "execution_id": receipt.execution_id,
                "receipt_sha256": receipt.receipt_sha256,
                "state_generation": after_state.generation,
                "state_manifest": state_manifest.model_dump(mode="json"),
                "execution_manifest": execution_manifest.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _execution_validate(args: argparse.Namespace) -> int:
    issues = validate_execution(
        args.execution_dir,
        plan_dir=args.plan_dir,
        before_state_dir=args.before_state_dir,
        after_state_dir=args.after_state_dir,
    )
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _rollback_proposal(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan_dir).plan
        receipt = load_execution(args.execution_dir).receipt
        proposal = build_rollback_proposal(
            plan,
            receipt,
            requested_by=args.requested_by,
            requested_at=_parse_time(args.requested_at),
        )
        _write_json(args.output, proposal.model_dump(mode="json"))
    except (
        OSError,
        ValueError,
        RemediationArtifactError,
        ExecutionError,
    ) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"success": True, "proposal": str(args.output)}, indent=2))
    return 0


def register_remediation_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "remediate", help="Plan, approve, and execute reversible simulated remediation."
    )
    commands = parser.add_subparsers(dest="remediate_command", required=True)

    state = commands.add_parser("state", help="Build or validate simulated control state.")
    state_commands = state.add_subparsers(dest="state_command", required=True)
    state_build = state_commands.add_parser("build")
    state_build.add_argument("--input", type=Path, required=True)
    state_build.add_argument("--output-dir", type=Path, required=True)
    state_build.add_argument("--overwrite", action="store_true")
    state_validate = state_commands.add_parser("validate")
    state_validate.add_argument("--state-dir", type=Path, required=True)
    store_validate = state_commands.add_parser("store-validate")
    store_validate.add_argument("--state-store", type=Path, required=True)

    plan = commands.add_parser("plan", help="Build or validate a governed remediation plan.")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    plan_build = plan_commands.add_parser("build")
    plan_build.add_argument("--investigation-dir", type=Path, required=True)
    plan_build.add_argument("--investigation-config", type=Path, required=True)
    plan_build.add_argument("--dataset-dir", type=Path, required=True)
    plan_build.add_argument("--analytics-dir", type=Path)
    plan_build.add_argument("--detection-dir", type=Path)
    plan_build.add_argument("--impact-dir", type=Path)
    plan_build.add_argument("--evidence-dir", type=Path)
    plan_build.add_argument("--state-dir", type=Path, required=True)
    plan_build.add_argument("--proposal", type=Path, required=True)
    plan_build.add_argument("--config", type=Path, required=True)
    plan_build.add_argument("--output-dir", type=Path, required=True)
    plan_build.add_argument("--overwrite", action="store_true")
    plan_validate = plan_commands.add_parser("validate")
    plan_validate.add_argument("--plan-dir", type=Path, required=True)
    plan_validate.add_argument("--investigation-dir", type=Path)
    plan_validate.add_argument("--investigation-config", type=Path)
    plan_validate.add_argument("--dataset-dir", type=Path)
    plan_validate.add_argument("--analytics-dir", type=Path)
    plan_validate.add_argument("--detection-dir", type=Path)
    plan_validate.add_argument("--impact-dir", type=Path)
    plan_validate.add_argument("--evidence-dir", type=Path)
    plan_validate.add_argument("--state-dir", type=Path)

    approval = commands.add_parser("approval", help="Record and evaluate human approval.")
    approval_commands = approval.add_subparsers(dest="approval_command", required=True)
    approval_record = approval_commands.add_parser("record")
    approval_record.add_argument("--plan-dir", type=Path, required=True)
    approval_record.add_argument("--approval-dir", type=Path, required=True)
    approval_record.add_argument("--decision", type=Path, required=True)
    approval_status = approval_commands.add_parser("status")
    approval_status.add_argument("--plan-dir", type=Path, required=True)
    approval_status.add_argument("--approval-dir", type=Path, required=True)
    approval_status.add_argument("--at")

    token = commands.add_parser("token", help="Issue a short-lived approval token.")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    token_issue = token_commands.add_parser("issue")
    token_issue.add_argument("--plan-dir", type=Path, required=True)
    token_issue.add_argument("--approval-dir", type=Path, required=True)
    token_issue.add_argument("--at")
    token_issue.add_argument("--output", type=Path, required=True)

    execute = commands.add_parser("execute", help="Execute an approved plan in simulated state.")
    execute.add_argument("--plan-dir", type=Path, required=True)
    execute.add_argument("--state-dir", type=Path, required=True)
    execute.add_argument(
        "--state-store",
        type=Path,
        help="Canonical local control-state lineage store (defaults beside --state-dir).",
    )
    execute.add_argument("--approval-dir", type=Path, required=True)
    execute.add_argument("--token-file", type=Path, required=True)
    execute.add_argument("--request", type=Path, required=True)
    execute.add_argument("--output-state-dir", type=Path, required=True)
    execute.add_argument("--output-dir", type=Path, required=True)
    execute.add_argument("--overwrite", action="store_true")

    execution = commands.add_parser("execution", help="Validate an execution receipt.")
    execution_commands = execution.add_subparsers(dest="execution_command", required=True)
    execution_validate = execution_commands.add_parser("validate")
    execution_validate.add_argument("--execution-dir", type=Path, required=True)
    execution_validate.add_argument("--plan-dir", type=Path)
    execution_validate.add_argument("--before-state-dir", type=Path)
    execution_validate.add_argument("--after-state-dir", type=Path)

    rollback = commands.add_parser("rollback-proposal", help="Build a fresh inverse proposal.")
    rollback.add_argument("--plan-dir", type=Path, required=True)
    rollback.add_argument("--execution-dir", type=Path, required=True)
    rollback.add_argument("--requested-by", required=True)
    rollback.add_argument("--requested-at", required=True)
    rollback.add_argument("--output", type=Path, required=True)


def dispatch_remediation(args: argparse.Namespace) -> int:
    if args.remediate_command == "state" and args.state_command == "build":
        return _state_build(args)
    if args.remediate_command == "state" and args.state_command == "validate":
        return _state_validate(args)
    if args.remediate_command == "state" and args.state_command == "store-validate":
        return _store_validate(args)
    if args.remediate_command == "plan" and args.plan_command == "build":
        return _plan_build(args)
    if args.remediate_command == "plan" and args.plan_command == "validate":
        return _plan_validate(args)
    if args.remediate_command == "approval" and args.approval_command == "record":
        return _approval_record(args)
    if args.remediate_command == "approval" and args.approval_command == "status":
        return _approval_status(args)
    if args.remediate_command == "token" and args.token_command == "issue":
        return _token_issue(args)
    if args.remediate_command == "execute":
        return _execute(args)
    if args.remediate_command == "execution" and args.execution_command == "validate":
        return _execution_validate(args)
    if args.remediate_command == "rollback-proposal":
        return _rollback_proposal(args)
    raise AssertionError("unhandled remediation command")
