from __future__ import annotations

import json
import os
from pathlib import Path

from paic.cli import main
from paic.evidence.io import load_evidence
from paic.investigation.artifact import replay_investigation


def test_remediation_state_cli_round_trip(tmp_path: Path, capsys: object) -> None:
    state_input = tmp_path / "state-input.json"
    state_dir = tmp_path / "state"
    state_input.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "state_id": "cli-state",
                "incident_id": "cli-incident",
                "generation": 0,
                "resources": [
                    {
                        "resource_type": "feature_flag",
                        "resource_id": "flag/cli",
                        "enabled": True,
                    }
                ],
                "consumed_token_nonce_hashes": [],
                "executed_plan_hashes": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "remediate",
                "state",
                "build",
                "--input",
                str(state_input),
                "--output-dir",
                str(state_dir),
            ]
        )
        == 0
    )
    assert main(["remediate", "state", "validate", "--state-dir", str(state_dir)]) == 0
    assert main(["remediate", "state", "validate", "--state-dir", str(state_dir)]) == 0


def test_remediation_state_cli_rejects_invalid_input(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert (
        main(
            [
                "remediate",
                "state",
                "build",
                "--input",
                str(invalid),
                "--output-dir",
                str(tmp_path / "state"),
            ]
        )
        == 2
    )


def test_token_files_are_never_tracked_by_example_name() -> None:
    # The smoke workflow uses an ignored .artifacts path and an ephemeral token file.
    assert os.path.basename(".artifacts/remediation-approval.token") == "remediation-approval.token"


def test_remediation_cli_end_to_end_with_scripted_investigation(
    repo_root: Path,
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Exercise the public plan, approval, token, execution, and validation commands."""

    ids = (
        load_evidence(evidence_smoke_dir)
        .tables["evidence_records"]
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    request = tmp_path / "investigation-request.json"
    request.write_text(
        json.dumps(
            {
                "incident_id": "checkout-address-validation-smoke",
                "question": "What caused the incident?",
                "dataset_dir": str(impact_smoke_dataset_dir),
                "evidence_dir": str(evidence_smoke_dir),
                "audit_dir": str(tmp_path / "audit"),
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "provider-script.json"
    script.write_text(
        json.dumps(
            {
                "nvidia/nemotron-3-super-120b-a12b": [
                    {
                        "model": "nvidia/nemotron-3-super-120b-a12b",
                        "tool_calls": [
                            {
                                "id": "search",
                                "name": "evidence__search",
                                "arguments": {"query": "", "limit": 2},
                            }
                        ],
                        "usage": {"total_tokens": 5},
                    },
                    {
                        "model": "nvidia/nemotron-3-super-120b-a12b",
                        "tool_calls": [
                            {
                                "id": "submit",
                                "name": "submit_investigation",
                                "arguments": {
                                    "summary": "A change is favored.",
                                    "hypotheses": [
                                        {
                                            "hypothesis_id": "change",
                                            "title": "Change regression",
                                            "prior_probability": 0.5,
                                            "rationale": "Two records support it.",
                                            "evidence": [
                                                {
                                                    "evidence_record_id": ids[0],
                                                    "direction": "support",
                                                    "likelihood_ratio": 8,
                                                    "explanation": "Temporal match.",
                                                },
                                                {
                                                    "evidence_record_id": ids[1],
                                                    "direction": "support",
                                                    "likelihood_ratio": 6,
                                                    "explanation": "Cohort match.",
                                                },
                                            ],
                                            "falsifiers": ["No recovery after rollback."],
                                        },
                                        {
                                            "hypothesis_id": "other",
                                            "title": "Other cause",
                                            "prior_probability": 0.5,
                                            "rationale": "Competing cause.",
                                            "evidence": [
                                                {
                                                    "evidence_record_id": ids[1],
                                                    "direction": "contradict",
                                                    "likelihood_ratio": 0.1,
                                                    "explanation": "Points away.",
                                                }
                                            ],
                                            "falsifiers": ["Other errors lead."],
                                        },
                                    ],
                                },
                            }
                        ],
                        "usage": {"total_tokens": 10},
                    },
                ],
                "qwen/qwen3.5-122b-a10b": [],
                "nvidia/nemotron-3-nano-30b-a3b": [],
            }
        ),
        encoding="utf-8",
    )
    investigation_dir = tmp_path / "investigation"
    assert (
        main(
            [
                "investigate",
                "run",
                "--request",
                str(request),
                "--config",
                str(repo_root / "configs/investigation/smoke.yaml"),
                "--output-dir",
                str(investigation_dir),
                "--provider-script",
                str(script),
            ]
        )
        == 0
    )
    report = replay_investigation(investigation_dir)

    state_input = tmp_path / "control-state.json"
    state_input.write_text(
        json.dumps(
            {
                "state_id": "cli-remediation",
                "incident_id": report.incident_id,
                "generation": 0,
                "resources": [
                    {
                        "resource_type": "deployment",
                        "resource_id": "service/checkout",
                        "current_revision": "bad",
                        "available_revisions": ["good", "bad"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    assert (
        main(
            [
                "remediate",
                "state",
                "build",
                "--input",
                str(state_input),
                "--output-dir",
                str(state_dir),
            ]
        )
        == 0
    )
    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "remediation_id": "cli-remediation",
                "incident_id": report.incident_id,
                "investigation_report_sha256": report.report_sha256,
                "selected_hypothesis_id": report.selected_hypothesis_id,
                "requested_by": "agent/planner",
                "requested_at": "2026-07-18T00:00:00+00:00",
                "summary": "Rollback.",
                "expected_outcome": "Recovery.",
                "rollback_trigger": "Guardrail regression.",
                "actions": [
                    {
                        "action_type": "deployment.rollback",
                        "action_id": "rollback-checkout",
                        "blast_radius": "single_service",
                        "evidence_ids": ids,
                        "justification": "Bound evidence.",
                        "resource_id": "service/checkout",
                        "expected_current_revision": "bad",
                        "target_revision": "good",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan_dir = tmp_path / "plan"
    assert (
        main(
            [
                "remediate",
                "plan",
                "build",
                "--investigation-dir",
                str(investigation_dir),
                "--state-dir",
                str(state_dir),
                "--proposal",
                str(proposal),
                "--config",
                str(repo_root / "configs/remediation/smoke.yaml"),
                "--output-dir",
                str(plan_dir),
            ]
        )
        == 0
    )
    plan = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    assert (
        main(
            [
                "remediate",
                "plan",
                "validate",
                "--plan-dir",
                str(plan_dir),
                "--investigation-dir",
                str(investigation_dir),
                "--state-dir",
                str(state_dir),
            ]
        )
        == 0
    )
    approval_dir = tmp_path / "approval"
    monkeypatch.setenv("PAIC_APPROVER_ONCALL_PRIMARY_KEY", "p" * 64)  # type: ignore[attr-defined]
    monkeypatch.setenv("PAIC_APPROVER_CHANGE_MANAGER_KEY", "m" * 64)  # type: ignore[attr-defined]
    for identity, group in (
        ("operator/oncall-primary", "operations/primary"),
        ("operator/change-manager", "operations/change-management"),
    ):
        decision = tmp_path / f"{identity.split('/')[-1]}.json"
        decision.write_text(
            json.dumps(
                {
                    "plan_sha256": plan["plan_sha256"],
                    "approver_id": identity,
                    "approver_group": group,
                    "decision": "approve",
                    "reason": "Approved.",
                    "decided_at": "2026-07-18T00:01:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        assert (
            main(
                [
                    "remediate",
                    "approval",
                    "record",
                    "--plan-dir",
                    str(plan_dir),
                    "--approval-dir",
                    str(approval_dir),
                    "--decision",
                    str(decision),
                ]
            )
            == 0
        )
    assert (
        main(
            [
                "remediate",
                "approval",
                "status",
                "--plan-dir",
                str(plan_dir),
                "--approval-dir",
                str(approval_dir),
                "--at",
                "2026-07-18T00:02:00+00:00",
            ]
        )
        == 0
    )
    monkeypatch.setenv("PAIC_APPROVAL_SECRET", "s" * 64)  # type: ignore[attr-defined]
    token = tmp_path / "approval.token"
    assert (
        main(
            [
                "remediate",
                "token",
                "issue",
                "--plan-dir",
                str(plan_dir),
                "--approval-dir",
                str(approval_dir),
                "--at",
                "2026-07-18T00:02:00+00:00",
                "--output",
                str(token),
            ]
        )
        == 0
    )
    execution_request = tmp_path / "execution-request.json"
    execution_request.write_text(
        json.dumps(
            {
                "execution_id": "cli-execution",
                "executed_by": "operator/executor",
                "executed_at": "2026-07-18T00:03:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    after_state, execution = tmp_path / "after-state", tmp_path / "execution"
    assert (
        main(
            [
                "remediate",
                "execute",
                "--plan-dir",
                str(plan_dir),
                "--state-dir",
                str(state_dir),
                "--approval-dir",
                str(approval_dir),
                "--token-file",
                str(token),
                "--request",
                str(execution_request),
                "--output-state-dir",
                str(after_state),
                "--output-dir",
                str(execution),
            ]
        )
        == 0
    )
    rollback = tmp_path / "rollback-proposal.json"
    assert (
        main(
            [
                "remediate",
                "rollback-proposal",
                "--plan-dir",
                str(plan_dir),
                "--execution-dir",
                str(execution),
                "--requested-by",
                "operator/rollback-requester",
                "--requested-at",
                "2026-07-18T00:04:00+00:00",
                "--output",
                str(rollback),
            ]
        )
        == 0
    )
    assert rollback.is_file()
    assert (
        main(
            [
                "remediate",
                "execution",
                "validate",
                "--execution-dir",
                str(execution),
                "--plan-dir",
                str(plan_dir),
                "--before-state-dir",
                str(state_dir),
                "--after-state-dir",
                str(after_state),
            ]
        )
        == 0
    )
