"""Small deterministic adversarial boundary for untrusted evaluation text."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdversarialResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    blocked: bool
    reason_codes: list[str] = Field(min_length=1)


class AdversarialCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=200)
    threat: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=20_000)
    expected_reason: str = Field(min_length=1, max_length=100)


def check_adversarial_text(case_id: str, text: str) -> AdversarialResult:
    lowered = text.lower()
    reasons: list[str] = []
    if "ignore previous instructions" in lowered or "system prompt" in lowered:
        reasons.append("prompt_injection")
    if "drop table" in lowered or "delete from" in lowered:
        reasons.append("destructive_sql")
    if "../" in text or "\\..\\" in text:
        reasons.append("path_traversal")
    if "bypass approval" in lowered or "execute mutation" in lowered:
        reasons.append("unsafe_authority_request")
    return AdversarialResult(
        case_id=case_id, blocked=bool(reasons), reason_codes=reasons or ["no_violation"]
    )


def evaluate_adversarial_suite(cases: list[AdversarialCase]) -> list[AdversarialResult]:
    results = [check_adversarial_text(case.case_id, case.input) for case in cases]
    for case, result in zip(cases, results, strict=True):
        if not result.blocked or case.expected_reason not in result.reason_codes:
            raise ValueError(f"adversarial case was not blocked as expected: {case.case_id}")
    return results
