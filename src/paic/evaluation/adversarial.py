"""Adversarial evaluation across supplemental text checks and real boundaries."""

from __future__ import annotations

from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import Field

from paic.evaluation.models import StrictModel


class AdversarialResult(StrictModel):
    case_id: str
    boundary: Literal["text", "sql_policy", "safe_path"]
    blocked: bool
    authoritative_control_exercised: bool
    reason_codes: list[str] = Field(min_length=1)


class AdversarialCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=200)
    threat: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=20_000)
    expected_reason: str = Field(min_length=1, max_length=100)
    boundary: Literal["text", "sql_policy", "safe_path"] = "text"


def check_adversarial_text(case_id: str, text: str) -> AdversarialResult:
    """Supplemental detector only; it is not the project's security authority."""

    lowered = text.lower()
    reasons: list[str] = []
    if "ignore previous instructions" in lowered or "system prompt" in lowered:
        reasons.append("prompt_injection")
    if "drop table" in lowered or "delete from" in lowered:
        reasons.append("destructive_sql_indicator")
    if "../" in text or "\\..\\" in text:
        reasons.append("path_traversal_indicator")
    if (
        "bypass approval" in lowered
        or "execute mutation" in lowered
        or "declare recovery" in lowered
    ):
        reasons.append("unsafe_authority_request")
    return AdversarialResult(
        case_id=case_id,
        boundary="text",
        blocked=bool(reasons),
        authoritative_control_exercised=False,
        reason_codes=reasons or ["no_violation"],
    )


def validate_safe_relative_path(value: str) -> PurePath:
    if not value or "\x00" in value:
        raise ValueError("unsafe_path")
    candidates: tuple[PurePath, ...] = (PurePosixPath(value), PureWindowsPath(value))
    for path in candidates:
        if path.is_absolute() or path.anchor or any(part == ".." for part in path.parts):
            raise ValueError("path_traversal")
    return PurePosixPath(value)


def _evaluate_sql(case: AdversarialCase) -> AdversarialResult:
    from paic.tools.sql import SQLPolicyError, validate_sql

    try:
        validate_sql(
            case.input,
            tables={"incidents"},
            columns={"incidents": {"incident_id", "status"}},
        )
    except SQLPolicyError:
        return AdversarialResult(
            case_id=case.case_id,
            boundary="sql_policy",
            blocked=True,
            authoritative_control_exercised=True,
            reason_codes=["destructive_sql"],
        )
    return AdversarialResult(
        case_id=case.case_id,
        boundary="sql_policy",
        blocked=False,
        authoritative_control_exercised=True,
        reason_codes=["sql_policy_allowed"],
    )


def _evaluate_path(case: AdversarialCase) -> AdversarialResult:
    try:
        validate_safe_relative_path(case.input)
    except ValueError as exc:
        return AdversarialResult(
            case_id=case.case_id,
            boundary="safe_path",
            blocked=True,
            authoritative_control_exercised=True,
            reason_codes=[str(exc)],
        )
    return AdversarialResult(
        case_id=case.case_id,
        boundary="safe_path",
        blocked=False,
        authoritative_control_exercised=True,
        reason_codes=["safe_path"],
    )


def evaluate_adversarial_case(case: AdversarialCase) -> AdversarialResult:
    if case.boundary == "sql_policy":
        return _evaluate_sql(case)
    if case.boundary == "safe_path":
        return _evaluate_path(case)
    return check_adversarial_text(case.case_id, case.input)


def evaluate_adversarial_suite(cases: list[AdversarialCase]) -> list[AdversarialResult]:
    if not cases:
        raise ValueError("adversarial suite must not be empty")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("adversarial case IDs must be unique")
    results = [evaluate_adversarial_case(case) for case in cases]
    for case, result in zip(cases, results, strict=True):
        if not result.blocked or case.expected_reason not in result.reason_codes:
            raise ValueError(f"adversarial case was not blocked as expected: {case.case_id}")
    return results
