"""Strict wire models for gateway requests, responses, and errors."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolRequest(StrictModel):
    tool: str = Field(min_length=1, max_length=80)
    tool_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    incident_id: str = Field(min_length=1, max_length=200)
    role: Literal["observer", "investigator", "approver"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    dataset_dir: str = Field(min_length=1)
    analytics_dir: str | None = None
    detection_dir: str | None = None
    impact_dir: str | None = None
    evidence_dir: str | None = None
    audit_dir: str | None = None
    call_id: UUID | None = None


class ToolError(StrictModel):
    code: str
    message: str


class ToolResponse(StrictModel):
    call_id: str
    incident_id: str
    tool: str
    tool_version: str
    policy_decision: Literal["allow", "deny"]
    execution_status: Literal["success", "error"]
    source_manifest_hashes: dict[str, str]
    row_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    truncated: bool
    evidence_record_ids: list[str]
    result: Any = None
    result_sha256: str
    error: ToolError | None = None
