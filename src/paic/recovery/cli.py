"""CLI integration for recovery verification and deterministic reopening."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from paic.recovery.artifact import (
    RecoveryArtifactError,
    export_recovery,
    load_recovery,
    validate_recovery,
)
from paic.recovery.config import RecoveryConfigError, load_recovery_config
from paic.recovery.engine import RecoveryEvaluationError, evaluate_recovery
from paic.recovery.lifecycle import RecoveryStateStore, RecoveryStateStoreError
from paic.recovery.models import RecoveryObservationSet
from paic.remediation.artifact import load_execution, manifest_sha256


def _evaluate(args: argparse.Namespace) -> int:
    try:
        config = load_recovery_config(args.config)
        observations = RecoveryObservationSet.model_validate_json(
            args.observations.read_text(encoding="utf-8")
        )
        execution = load_execution(args.execution_dir)
        if observations.execution_receipt_sha256 != execution.receipt.receipt_sha256:
            raise RecoveryEvaluationError("observations are bound to another execution receipt")
        report = evaluate_recovery(
            config,
            observations,
            execution_manifest_sha256=manifest_sha256(args.execution_dir),
        )
        manifest = export_recovery(
            config,
            observations,
            report,
            args.output_dir,
            overwrite=args.overwrite,
        )
    except (
        OSError,
        ValueError,
        RecoveryConfigError,
        RecoveryEvaluationError,
        RecoveryArtifactError,
    ) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "success": True,
                "decision": report.decision,
                "report_sha256": report.report_sha256,
                "primary": f"{report.primary_recovered}/{report.primary_total}",
                "guardrails": f"{report.guardrails_healthy}/{report.guardrail_total}",
                "manifest": manifest.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.decision == "recovered" else 1


def _validate(args: argparse.Namespace) -> int:
    expected = None
    if args.execution_dir is not None:
        try:
            expected = load_execution(args.execution_dir).receipt.receipt_sha256
        except Exception as exc:
            print(json.dumps({"valid": False, "issues": [str(exc)]}, indent=2))
            return 2
    issues = validate_recovery(
        args.recovery_dir,
        expected_execution_receipt_sha256=expected,
    )
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _summary(args: argparse.Namespace) -> int:
    try:
        report = load_recovery(args.recovery_dir).report
    except RecoveryArtifactError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(report.model_dump_json(indent=2))
    return 0


def _state_apply(args: argparse.Namespace) -> int:
    try:
        loaded = load_recovery(args.recovery_dir)
        store = RecoveryStateStore(args.state_store)
        store.initialize(loaded.report.incident_id, loaded.report.execution_receipt_sha256)
        state, event = store.apply(loaded.report, loaded.config)
    except (RecoveryArtifactError, RecoveryStateStoreError, ValueError, OSError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "success": True,
                "generation": state.generation,
                "incident_status": state.status,
                "trigger": event.trigger,
                "event_sha256": event.event_sha256,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if state.status == "reopened" else 0


def _state_validate(args: argparse.Namespace) -> int:
    issues = RecoveryStateStore(args.state_store).validate()
    print(json.dumps({"valid": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 1 if issues else 0


def _state_show(args: argparse.Namespace) -> int:
    try:
        state = RecoveryStateStore(args.state_store).current()
    except RecoveryStateStoreError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        return 2
    print(state.model_dump_json(indent=2))
    return 0


def register_recovery_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "recovery",
        help="Verify sustained statistical recovery and reopen regressed incidents.",
    )
    commands = parser.add_subparsers(dest="recovery_command", required=True)
    evaluate = commands.add_parser(
        "evaluate", help="Evaluate primary and guardrail recovery windows."
    )
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--observations", type=Path, required=True)
    evaluate.add_argument("--execution-dir", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--overwrite", action="store_true")
    validate = commands.add_parser(
        "validate", help="Validate and deterministically replay a recovery artifact."
    )
    validate.add_argument("--recovery-dir", type=Path, required=True)
    validate.add_argument("--execution-dir", type=Path)
    summary = commands.add_parser("summary", help="Print a recovery report.")
    summary.add_argument("--recovery-dir", type=Path, required=True)
    state = commands.add_parser(
        "state", help="Apply recovery results to the incident lifecycle store."
    )
    state_commands = state.add_subparsers(dest="recovery_state_command", required=True)
    apply = state_commands.add_parser("apply")
    apply.add_argument("--recovery-dir", type=Path, required=True)
    apply.add_argument("--state-store", type=Path, required=True)
    validate_state = state_commands.add_parser("validate")
    validate_state.add_argument("--state-store", type=Path, required=True)
    show = state_commands.add_parser("show")
    show.add_argument("--state-store", type=Path, required=True)


def dispatch_recovery(args: argparse.Namespace) -> int:
    if args.recovery_command == "evaluate":
        return _evaluate(args)
    if args.recovery_command == "validate":
        return _validate(args)
    if args.recovery_command == "summary":
        return _summary(args)
    if args.recovery_command == "state" and args.recovery_state_command == "apply":
        return _state_apply(args)
    if args.recovery_command == "state" and args.recovery_state_command == "validate":
        return _state_validate(args)
    if args.recovery_command == "state" and args.recovery_state_command == "show":
        return _state_show(args)
    raise AssertionError("unhandled recovery command")  # pragma: no cover
