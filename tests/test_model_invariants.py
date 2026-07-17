from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from paic.contracts.loader import ContractBundle
from paic.contracts.models import (
    EvaluationContract,
    EvaluationMetric,
    IncidentSpec,
    ProjectContract,
    RemediationPlan,
    SafetyContract,
)


def test_project_rejects_duplicate_workflow_stage(bundle: ContractBundle) -> None:
    raw = bundle.project.model_dump(mode="json")
    raw["workflow"].append(raw["workflow"][0])
    with pytest.raises(ValidationError, match="workflow stages must be unique"):
        ProjectContract.model_validate(raw)


def test_evaluation_metric_requires_complete_hard_gate(bundle: ContractBundle) -> None:
    raw = bundle.evaluation.metrics[0].model_dump(mode="json")
    raw["hard_gate"] = 1.0
    raw["hard_gate_operator"] = None
    with pytest.raises(ValidationError, match="must be set together"):
        EvaluationMetric.model_validate(raw)


def test_evaluation_contract_rejects_duplicate_metric_and_baseline_ids(
    bundle: ContractBundle,
) -> None:
    raw = bundle.evaluation.model_dump(mode="json")
    raw["metrics"].append(deepcopy(raw["metrics"][0]))
    with pytest.raises(ValidationError, match="metric IDs must be unique"):
        EvaluationContract.model_validate(raw)

    raw = bundle.evaluation.model_dump(mode="json")
    raw["baselines"].append(deepcopy(raw["baselines"][0]))
    with pytest.raises(ValidationError, match="baseline IDs must be unique"):
        EvaluationContract.model_validate(raw)


def test_remediation_enforces_approval_and_rollback() -> None:
    with pytest.raises(ValidationError, match="must require approval"):
        RemediationPlan.model_validate(
            {
                "action_id": "unsafe_action",
                "description": "A gated action without approval.",
                "risk_level": 2,
                "approval_required": False,
                "reversible": False,
                "target": "synthetic-target",
            }
        )

    with pytest.raises(ValidationError, match="must define rollback_action"):
        RemediationPlan.model_validate(
            {
                "action_id": "missing_rollback",
                "description": "A reversible action without rollback instructions.",
                "risk_level": 1,
                "approval_required": False,
                "reversible": True,
                "target": "synthetic-target",
            }
        )


def test_incident_rejects_inconsistent_hypotheses_and_evidence(bundle: ContractBundle) -> None:
    base = bundle.incidents[0].model_dump(mode="json")

    raw = deepcopy(base)
    raw["candidate_hypotheses"].append(deepcopy(raw["candidate_hypotheses"][0]))
    with pytest.raises(ValidationError, match="hypothesis IDs must be unique"):
        IncidentSpec.model_validate(raw)

    raw = deepcopy(base)
    raw["hidden_ground_truth"]["root_cause_hypothesis_id"] = "H99"
    with pytest.raises(ValidationError, match="must exist"):
        IncidentSpec.model_validate(raw)

    raw = deepcopy(base)
    raw["evidence_expectations"].append(deepcopy(raw["evidence_expectations"][0]))
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        IncidentSpec.model_validate(raw)

    raw = deepcopy(base)
    raw["evidence_expectations"][0]["hypothesis_ids"] = ["H99"]
    with pytest.raises(ValidationError, match="unknown hypotheses"):
        IncidentSpec.model_validate(raw)

    raw = deepcopy(base)
    for item in raw["evidence_expectations"]:
        if "H1" in item["hypothesis_ids"] and item["relation"] == "supports":
            item["hypothesis_ids"] = ["H2"]
    with pytest.raises(ValidationError, match="must support the ground truth"):
        IncidentSpec.model_validate(raw)

    raw = deepcopy(base)
    for item in raw["evidence_expectations"]:
        item["relation"] = "supports"
    with pytest.raises(ValidationError, match="contradictory evidence"):
        IncidentSpec.model_validate(raw)


def test_safety_contract_rejects_insecure_configuration(bundle: ContractBundle) -> None:
    raw = bundle.safety.model_dump(mode="json")
    raw["action_classes"][-1]["risk_level"] = 2
    with pytest.raises(ValidationError, match="risk levels 0, 1, 2, and 3"):
        SafetyContract.model_validate(raw)

    raw = bundle.safety.model_dump(mode="json")
    raw["default_deny"] = False
    with pytest.raises(ValidationError, match="default-deny"):
        SafetyContract.model_validate(raw)

    raw = bundle.safety.model_dump(mode="json")
    raw["llm_has_direct_credentials"] = True
    with pytest.raises(ValidationError, match="must not receive direct"):
        SafetyContract.model_validate(raw)
