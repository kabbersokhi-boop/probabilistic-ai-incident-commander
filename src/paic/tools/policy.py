"""Deny-by-default authorization policy."""

from __future__ import annotations

from dataclasses import dataclass

from paic.tools.catalogue import CAPABILITIES, TOOLS


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str
    reason: str


def authorize(role: str, tool: str, version: str, arguments: dict[str, object]) -> PolicyDecision:
    if role not in CAPABILITIES:
        return PolicyDecision(False, "unknown_role", "role is not registered")
    if tool not in TOOLS:
        return PolicyDecision(False, "unknown_tool", "tool is not registered")
    if tool not in CAPABILITIES[role]:
        return PolicyDecision(False, "forbidden", "role is not authorized for this tool")
    expected = str(TOOLS[tool]["version"])
    if version != expected:
        return PolicyDecision(False, "unsupported_version", "tool version is not supported")
    if any(not isinstance(key, str) or not key for key in arguments):
        return PolicyDecision(
            False, "invalid_arguments", "argument names must be non-empty strings"
        )
    return PolicyDecision(True, "allowed", "policy permits read-only access")
