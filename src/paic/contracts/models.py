"""Strict Pydantic models for project contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictModel(BaseModel):
    """Base model that rejects undocumented fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkflowStage(StrEnum):
    DETECT = "DETECT"
    SCOPE = "SCOPE"
    INVESTIGATE = "INVESTIGATE"
    FORM_HYPOTHESES = "FORM_HYPOTHESES"
    TEST_HYPOTHESES = "TEST_HYPOTHESES"
    RANK_ROOT_CAUSES = "RANK_ROOT_CAUSES"
    RECOMMEND_ACTION = "RECOMMEND_ACTION"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    REMEDIATE = "REMEDIATE"
    VERIFY_RECOVERY = "VERIFY_RECOVERY"
    REPORT = "REPORT"


class ProjectIdentity(StrictModel):
    name: NonEmptyText
    slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    one_sentence_definition: NonEmptyText
    portfolio_goal: NonEmptyText
    target_roles: Annotated[list[NonEmptyText], Field(min_length=1)]
    primary_users: Annotated[list[NonEmptyText], Field(min_length=1)]


class ProjectContract(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    project: ProjectIdentity
    workflow: Annotated[list[WorkflowStage], Field(min_length=3)]
    principles: Annotated[list[NonEmptyText], Field(min_length=3)]
    non_goals: Annotated[list[NonEmptyText], Field(min_length=1)]
    signature_demo: Annotated[list[NonEmptyText], Field(min_length=5)]

    @model_validator(mode="after")
    def workflow_has_no_duplicates(self) -> ProjectContract:
        if len(self.workflow) != len(set(self.workflow)):
            raise ValueError("workflow stages must be unique")
        return self


class EvaluationMetric(StrictModel):
    metric_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    category: Literal[
        "detection",
        "diagnosis",
        "probability",
        "operations",
        "efficiency",
        "customer_impact",
        "security",
    ]
    definition: NonEmptyText
    direction: Literal["higher_is_better", "lower_is_better", "target"]
    unit: NonEmptyText
    formula: NonEmptyText
    introduced_in_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    hard_gate: float | None = None
    hard_gate_operator: Literal[">=", "<=", "=="] | None = None
    notes: NonEmptyText | None = None

    @model_validator(mode="after")
    def gate_is_complete(self) -> EvaluationMetric:
        if (self.hard_gate is None) != (self.hard_gate_operator is None):
            raise ValueError("hard_gate and hard_gate_operator must be set together")
        return self


class BaselineSpec(StrictModel):
    baseline_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    name: NonEmptyText
    purpose: NonEmptyText
    introduced_in_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]


class BenchmarkContract(StrictModel):
    minimum_total_incidents: Annotated[int, Field(ge=5)]
    minimum_demo_incidents: Annotated[int, Field(ge=1)]
    minimum_incident_families: Annotated[int, Field(ge=3)]
    hidden_ground_truth_required: bool
    fixed_seed_required: bool
    decoy_change_required: bool
    minimum_candidate_hypotheses: Annotated[int, Field(ge=2)]
    minimum_contradictory_evidence_items: Annotated[int, Field(ge=1)]


class EvaluationContract(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    benchmark: BenchmarkContract
    metrics: Annotated[list[EvaluationMetric], Field(min_length=5)]
    baselines: Annotated[list[BaselineSpec], Field(min_length=3)]
    reporting_requirements: Annotated[list[NonEmptyText], Field(min_length=3)]

    @model_validator(mode="after")
    def unique_identifiers(self) -> EvaluationContract:
        metric_ids = [item.metric_id for item in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("evaluation metric IDs must be unique")
        baseline_ids = [item.baseline_id for item in self.baselines]
        if len(baseline_ids) != len(set(baseline_ids)):
            raise ValueError("baseline IDs must be unique")
        return self


class CandidateHypothesis(StrictModel):
    hypothesis_id: Annotated[str, Field(pattern=r"^H\d+$")]
    statement: NonEmptyText
    expected_observations: Annotated[list[NonEmptyText], Field(min_length=1)]
    falsifying_observations: Annotated[list[NonEmptyText], Field(min_length=1)]


class EvidenceExpectation(StrictModel):
    evidence_id: Annotated[str, Field(pattern=r"^E\d+$")]
    source_type: Literal[
        "metric",
        "sql",
        "deployment",
        "configuration",
        "log",
        "trace",
        "lineage",
        "pipeline",
        "historical_incident",
        "runbook",
        "customer_model",
    ]
    observation: NonEmptyText
    relation: Literal["supports", "contradicts"]
    hypothesis_ids: Annotated[list[Annotated[str, Field(pattern=r"^H\d+$")]], Field(min_length=1)]
    reliability: Literal["high", "medium", "low"]


class DecoyChange(StrictModel):
    change_id: Annotated[str, Field(pattern=r"^D\d+$")]
    description: NonEmptyText
    timing_relative_to_incident_minutes: int
    why_plausible: NonEmptyText
    why_not_causal: NonEmptyText


class GroundTruth(StrictModel):
    root_cause_hypothesis_id: Annotated[str, Field(pattern=r"^H\d+$")]
    root_cause_summary: NonEmptyText
    causal_chain: Annotated[list[NonEmptyText], Field(min_length=2)]
    affected_components: Annotated[list[NonEmptyText], Field(min_length=1)]


class RemediationPlan(StrictModel):
    action_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    description: NonEmptyText
    risk_level: Annotated[int, Field(ge=0, le=3)]
    approval_required: bool
    reversible: bool
    target: NonEmptyText
    rollback_action: NonEmptyText | None = None

    @model_validator(mode="after")
    def risk_policy_is_coherent(self) -> RemediationPlan:
        if self.risk_level >= 2 and not self.approval_required:
            raise ValueError("risk level 2 or 3 remediation must require approval")
        if self.reversible and not self.rollback_action:
            raise ValueError("reversible remediation must define rollback_action")
        return self


class RecoveryExpectation(StrictModel):
    primary_metric: NonEmptyText
    expected_direction: Literal["increase", "decrease", "return_to_baseline"]
    verification_windows: Annotated[int, Field(ge=2)]
    minimum_sample_size: Annotated[int, Field(ge=1)]
    success_condition: NonEmptyText
    guardrail_metrics: Annotated[list[NonEmptyText], Field(min_length=1)]
    reopen_condition: NonEmptyText


class IncidentSpec(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    incident_id: Annotated[str, Field(pattern=r"^INC-\d{3}$")]
    title: NonEmptyText
    family: NonEmptyText
    business_process: NonEmptyText
    difficulty: Literal["medium", "hard", "expert"]
    random_seed: Annotated[int, Field(ge=1)]
    narrative: NonEmptyText
    trigger: NonEmptyText
    primary_metric: NonEmptyText
    symptoms: Annotated[list[NonEmptyText], Field(min_length=2)]
    affected_cohorts: dict[NonEmptyText, Annotated[list[NonEmptyText], Field(min_length=1)]]
    unaffected_cohorts: dict[NonEmptyText, Annotated[list[NonEmptyText], Field(min_length=1)]]
    required_tools: Annotated[list[NonEmptyText], Field(min_length=2)]
    candidate_hypotheses: Annotated[list[CandidateHypothesis], Field(min_length=2)]
    evidence_expectations: Annotated[list[EvidenceExpectation], Field(min_length=2)]
    decoy_changes: Annotated[list[DecoyChange], Field(min_length=1)]
    hidden_ground_truth: GroundTruth
    correct_remediation: RemediationPlan
    recovery_expectation: RecoveryExpectation

    @model_validator(mode="after")
    def incident_is_self_consistent(self) -> IncidentSpec:
        hypothesis_ids = [item.hypothesis_id for item in self.candidate_hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("candidate hypothesis IDs must be unique")
        if self.hidden_ground_truth.root_cause_hypothesis_id not in hypothesis_ids:
            raise ValueError("ground-truth hypothesis must exist in candidate hypotheses")

        evidence_ids = [item.evidence_id for item in self.evidence_expectations]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        referenced_hypotheses = {
            hypothesis_id
            for evidence in self.evidence_expectations
            for hypothesis_id in evidence.hypothesis_ids
        }
        unknown = referenced_hypotheses.difference(hypothesis_ids)
        if unknown:
            raise ValueError(f"evidence references unknown hypotheses: {sorted(unknown)}")

        root_id = self.hidden_ground_truth.root_cause_hypothesis_id
        supports_root = any(
            item.relation == "supports" and root_id in item.hypothesis_ids
            for item in self.evidence_expectations
        )
        if not supports_root:
            raise ValueError("at least one evidence item must support the ground truth")

        has_contradiction = any(
            item.relation == "contradicts" for item in self.evidence_expectations
        )
        if not has_contradiction:
            raise ValueError("at least one contradictory evidence item is required")
        return self


class SqlSafetyPolicy(StrictModel):
    default_role: Literal["read_only"]
    approved_schemas: Annotated[list[NonEmptyText], Field(min_length=1)]
    forbidden_statement_types: Annotated[list[NonEmptyText], Field(min_length=1)]
    row_limit: Annotated[int, Field(ge=1)]
    timeout_seconds: Annotated[int, Field(ge=1)]
    result_size_bytes: Annotated[int, Field(ge=1024)]
    require_parameterized_values: bool
    require_audit_log: bool
    require_query_plan_check: bool


class ActionClass(StrictModel):
    risk_level: Annotated[int, Field(ge=0, le=3)]
    name: NonEmptyText
    examples: Annotated[list[NonEmptyText], Field(min_length=1)]
    policy: Literal[
        "automatic",
        "automatic_and_logged",
        "human_approval_required",
        "blocked",
    ]


class SafetyContract(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    default_deny: bool
    llm_has_direct_credentials: bool
    sql: SqlSafetyPolicy
    action_classes: Annotated[list[ActionClass], Field(min_length=4, max_length=4)]
    untrusted_content_sources: Annotated[list[NonEmptyText], Field(min_length=2)]
    required_audit_events: Annotated[list[NonEmptyText], Field(min_length=3)]
    prohibited_capabilities: Annotated[list[NonEmptyText], Field(min_length=1)]

    @model_validator(mode="after")
    def action_levels_are_complete(self) -> SafetyContract:
        levels = sorted(item.risk_level for item in self.action_classes)
        if levels != [0, 1, 2, 3]:
            raise ValueError("action classes must define risk levels 0, 1, 2, and 3")
        if not self.default_deny:
            raise ValueError("safety contract must use default-deny policy")
        if self.llm_has_direct_credentials:
            raise ValueError("LLM must not receive direct operational credentials")
        return self
