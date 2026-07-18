"""Small deterministic adversarial boundary for untrusted evaluation text."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdversarialResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    blocked: bool
    reason_codes: list[str] = Field(min_length=1)


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
