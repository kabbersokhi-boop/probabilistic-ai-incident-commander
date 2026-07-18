"""Deny-by-default role and argument policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from paic.tools.catalogue import CAPABILITIES, TOOL_ARGUMENT_MODELS, TOOLS


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str
    reason: str
    normalized_arguments: dict[str, Any]


def authorize(role: str, tool: str, version: str, arguments: dict[str, Any]) -> PolicyDecision:
    if role not in CAPABILITIES:
        return PolicyDecision(False, "unknown_role", "role is not registered", {})
    if tool not in TOOLS:
        return PolicyDecision(False, "unknown_tool", "tool is not registered", {})
    if tool not in CAPABILITIES[role]:
        return PolicyDecision(False, "forbidden", "role is not authorized for this tool", {})
    if version != str(TOOLS[tool]["version"]):
        return PolicyDecision(False, "unsupported_version", "tool version is not supported", {})
    try:
        parsed = TOOL_ARGUMENT_MODELS[tool].model_validate(arguments)
    except ValidationError as exc:
        return PolicyDecision(False, "invalid_arguments", str(exc), {})
    return PolicyDecision(
        True,
        "allowed",
        "policy permits read-only access",
        parsed.model_dump(mode="json", exclude_none=True),
    )
