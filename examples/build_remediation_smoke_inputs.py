"""Build deterministic smoke inputs for governed remediation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paic.investigation.artifact import replay_investigation
from paic.remediation.artifact import load_plan


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_initial(investigation_dir: Path, state_input: Path, proposal: Path) -> None:
    report = replay_investigation(investigation_dir, artifact_only=True)
    if report.status != "concluded" or report.selected_hypothesis_id is None:
        raise RuntimeError("remediation smoke requires a concluded investigation")
    selected = next(
        item for item in report.hypotheses if item.hypothesis_id == report.selected_hypothesis_id
    )
    if len(selected.supporting_evidence_ids) < 2:
        raise RuntimeError("remediation smoke requires two supporting evidence records")
    evidence_ids = selected.supporting_evidence_ids[:2]
    _write(
        state_input,
        {
            "schema_version": "1.0",
            "state_id": "checkout-control-smoke",
            "incident_id": report.incident_id,
            "generation": 0,
            "resources": [
                {
                    "resource_type": "deployment",
                    "resource_id": "service/checkout-address-validator",
                    "current_revision": "2026.07.18-bad",
                    "available_revisions": ["2026.07.17-good", "2026.07.18-bad"],
                },
                {
                    "resource_type": "feature_flag",
                    "resource_id": "flag/strict-address-validation",
                    "enabled": True,
                },
                {
                    "resource_type": "configuration",
                    "resource_id": "config/address-validation-rules",
                    "current_version": "v2",
                    "available_versions": ["v1", "v2"],
                },
            ],
            "consumed_token_nonce_hashes": [],
            "executed_plan_hashes": [],
        },
    )
    _write(
        proposal,
        {
            "schema_version": "1.0",
            "remediation_id": "checkout-rollback-smoke",
            "incident_id": report.incident_id,
            "investigation_report_sha256": report.report_sha256,
            "selected_hypothesis_id": report.selected_hypothesis_id,
            "requested_by": "agent/remediation-planner",
            "requested_at": "2026-07-18T00:00:00+00:00",
            "summary": "Roll back the address-validation service to the last known good revision.",
            "expected_outcome": "Checkout address validation returns to the prior success rate.",
            "rollback_trigger": "A post-action guardrail degrades or the checkout error rate increases.",
            "actions": [
                {
                    "action_type": "deployment.rollback",
                    "action_id": "rollback-address-validator",
                    "blast_radius": "single_service",
                    "evidence_ids": evidence_ids,
                    "justification": "The selected hypothesis is supported by two source-bound records.",
                    "resource_id": "service/checkout-address-validator",
                    "expected_current_revision": "2026.07.18-bad",
                    "target_revision": "2026.07.17-good",
                }
            ],
        },
    )


def build_after_plan(
    plan_dir: Path,
    decision_one: Path,
    decision_two: Path,
    execution_request: Path,
) -> None:
    plan = load_plan(plan_dir).plan
    common = {
        "schema_version": "1.0",
        "plan_sha256": plan.plan_sha256,
        "approver_role": "approver",
        "decision": "approve",
    }
    _write(
        decision_one,
        {
            **common,
            "approver_id": "operator/oncall-primary",
            "approver_group": "operations/primary",
            "reason": "The rollback is reversible and matches the validated investigation.",
            "decided_at": "2026-07-18T00:05:00+00:00",
        },
    )
    _write(
        decision_two,
        {
            **common,
            "approver_id": "operator/change-manager",
            "approver_group": "operations/change-management",
            "reason": "The blast radius and rollback preconditions satisfy policy.",
            "decided_at": "2026-07-18T00:06:00+00:00",
        },
    )
    _write(
        execution_request,
        {
            "schema_version": "1.0",
            "execution_id": "checkout-remediation-execution-smoke",
            "executed_by": "operator/incident-commander",
            "executed_at": "2026-07-18T00:07:00+00:00",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--investigation-dir", type=Path)
    parser.add_argument("--state-input", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--plan-dir", type=Path)
    parser.add_argument("--decision-one", type=Path)
    parser.add_argument("--decision-two", type=Path)
    parser.add_argument("--execution-request", type=Path)
    args = parser.parse_args()
    if args.investigation_dir:
        if not args.state_input or not args.proposal:
            parser.error("initial mode requires --state-input and --proposal")
        build_initial(args.investigation_dir, args.state_input, args.proposal)
    elif args.plan_dir:
        if not args.decision_one or not args.decision_two or not args.execution_request:
            parser.error(
                "post-plan mode requires --decision-one, --decision-two, and --execution-request"
            )
        build_after_plan(
            args.plan_dir,
            args.decision_one,
            args.decision_two,
            args.execution_request,
        )
    else:
        parser.error("choose --investigation-dir or --plan-dir mode")


if __name__ == "__main__":
    main()
