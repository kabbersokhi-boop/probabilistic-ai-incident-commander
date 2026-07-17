from __future__ import annotations

from dataclasses import replace

from paic.contracts.loader import ContractBundle
from paic.contracts.validator import validate_contract_bundle


def test_phase_zero_contracts_have_no_validation_issues(bundle: ContractBundle) -> None:
    assert validate_contract_bundle(bundle) == []


def test_duplicate_seed_is_detected(bundle: ContractBundle) -> None:
    first, second, *remaining = bundle.incidents
    duplicated_second = second.model_copy(update={"random_seed": first.random_seed})
    invalid_bundle = replace(
        bundle,
        incidents=(first, duplicated_second, *remaining),
    )
    issues = validate_contract_bundle(invalid_bundle)
    assert any(issue.code == "incidents.duplicate_seed" for issue in issues)


def test_required_metrics_are_present(bundle: ContractBundle) -> None:
    metric_ids = {metric.metric_id for metric in bundle.evaluation.metrics}
    assert {
        "detection_precision",
        "detection_recall",
        "top_1_root_cause_accuracy",
        "brier_score",
        "approval_compliance_rate",
        "recovery_verification_accuracy",
    }.issubset(metric_ids)


def test_safety_hard_gates_are_explicit(bundle: ContractBundle) -> None:
    metrics = {metric.metric_id: metric for metric in bundle.evaluation.metrics}
    for metric_id in (
        "unsafe_action_block_rate",
        "sql_safety_block_rate",
        "approval_compliance_rate",
    ):
        metric = metrics[metric_id]
        assert metric.hard_gate == 1.0
        assert metric.hard_gate_operator == "=="


def test_every_incident_contains_competing_and_falsifiable_hypotheses(
    bundle: ContractBundle,
) -> None:
    for incident in bundle.incidents:
        assert len(incident.candidate_hypotheses) >= 4
        assert incident.decoy_changes
        assert any(
            evidence.relation == "contradicts" for evidence in incident.evidence_expectations
        )
        assert all(
            hypothesis.expected_observations and hypothesis.falsifying_observations
            for hypothesis in incident.candidate_hypotheses
        )


def test_cross_contract_validator_reports_all_policy_drift(bundle: ContractBundle) -> None:
    from dataclasses import replace

    project = bundle.project.model_copy(
        update={
            "project": bundle.project.project.model_copy(update={"current_phase": 1}),
            "workflow": list(reversed(bundle.project.workflow)),
        }
    )
    benchmark = bundle.evaluation.benchmark.model_copy(
        update={
            "minimum_total_incidents": 3,
            "minimum_incident_families": 5,
            "minimum_candidate_hypotheses": 5,
            "minimum_contradictory_evidence_items": 2,
        }
    )
    evaluation = bundle.evaluation.model_copy(
        update={
            "benchmark": benchmark,
            "metrics": tuple(
                metric for metric in bundle.evaluation.metrics if metric.metric_id != "brier_score"
            ),
        }
    )

    first = bundle.incidents[0].model_copy(
        update={
            "candidate_hypotheses": bundle.incidents[0].candidate_hypotheses[:1],
            "evidence_expectations": tuple(
                evidence
                for evidence in bundle.incidents[0].evidence_expectations
                if evidence.relation != "contradicts"
            ),
            "decoy_changes": (),
        }
    )
    second = bundle.incidents[1].model_copy(
        update={
            "incident_id": first.incident_id,
            "random_seed": first.random_seed,
            "family": first.family,
        }
    )
    reduced_incidents = (first, second, bundle.incidents[2], bundle.incidents[3])

    sql = bundle.safety.sql.model_copy(
        update={
            "require_audit_log": False,
            "forbidden_statement_types": tuple(
                statement
                for statement in bundle.safety.sql.forbidden_statement_types
                if statement != "DELETE"
            ),
        }
    )
    action_classes = tuple(
        item.model_copy(
            update={
                "policy": "automatic"
                if item.risk_level == 2
                else ("human_approval_required" if item.risk_level == 3 else item.policy)
            }
        )
        for item in bundle.safety.action_classes
    )
    safety = bundle.safety.model_copy(update={"sql": sql, "action_classes": action_classes})

    invalid = replace(
        bundle,
        project=project,
        evaluation=evaluation,
        safety=safety,
        incidents=reduced_incidents,
    )
    codes = {issue.code for issue in validate_contract_bundle(invalid)}
    assert {
        "project.phase",
        "workflow.order",
        "incidents.phase0.minimum",
        "benchmark.minimum_below_seed_count",
        "incidents.duplicate_id",
        "incidents.duplicate_seed",
        "incidents.family_coverage",
        "incident.hypothesis_count",
        "incident.contradiction_count",
        "incident.decoy_required",
        "evaluation.required_metrics",
        "safety.sql.audit",
        "safety.sql.delete",
        "safety.risk2",
        "safety.risk3",
    }.issubset(codes)
