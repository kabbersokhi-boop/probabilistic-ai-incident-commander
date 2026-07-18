"""Versioned catalogue and strict argument schemas for gateway tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArgsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SQLQueryArgs(ArgsModel):
    query: str = Field(min_length=1, max_length=20_000)
    limit: int | None = Field(default=None, ge=1, le=5_000)


class SearchArgs(ArgsModel):
    query: str = Field(default="", max_length=1_000)
    limit: int = Field(default=50, ge=1, le=500)


class LineageTraceArgs(ArgsModel):
    node_id: str = Field(min_length=1, max_length=200)
    direction: Literal["both", "upstream", "downstream"] = "both"
    depth: int = Field(default=2, ge=1, le=8)


class ChangesListArgs(SearchArgs):
    service: str | None = Field(default=None, max_length=200)


class RunbookGetArgs(ArgsModel):
    runbook_id: str | None = Field(default=None, max_length=200)
    query: str = Field(default="", max_length=1_000)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def require_selector(self) -> RunbookGetArgs:
        if not self.runbook_id and not self.query:
            raise ValueError("runbook_id or query is required")
        return self


class HistoricalSearchArgs(SearchArgs):
    family: str | None = Field(default=None, max_length=200)
    service: str | None = Field(default=None, max_length=200)


class AnomaliesListArgs(ArgsModel):
    metric: str | None = Field(default=None, max_length=200)
    severity: Literal["low", "medium", "high", "critical"] | None = None
    limit: int = Field(default=50, ge=1, le=500)


class ImpactSummaryArgs(ArgsModel):
    segment: str | None = Field(default=None, max_length=200)


class ArtifactsSummaryArgs(ArgsModel):
    include_tables: bool = True


TOOL_ARGUMENT_MODELS: dict[str, type[ArgsModel]] = {
    "sql.query": SQLQueryArgs,
    "evidence.search": SearchArgs,
    "lineage.trace": LineageTraceArgs,
    "changes.list": ChangesListArgs,
    "runbook.get": RunbookGetArgs,
    "historical_incidents.search": HistoricalSearchArgs,
    "anomalies.list": AnomaliesListArgs,
    "impact.summary": ImpactSummaryArgs,
    "artifacts.summary": ArtifactsSummaryArgs,
}

_TOOL_DESCRIPTIONS = {
    "sql.query": "Run one bounded read-only SQL query over validated registered artifact tables.",
    "evidence.search": "Search canonical operational evidence records.",
    "lineage.trace": "Trace upstream and downstream lineage around a node.",
    "changes.list": "List incident-relevant configuration and feature-flag changes.",
    "runbook.get": "Retrieve a runbook by ID or text query.",
    "historical_incidents.search": "Search comparable historical incidents.",
    "anomalies.list": "List statistical anomaly events.",
    "impact.summary": "Return bounded customer and financial impact summaries.",
    "artifacts.summary": "Summarize validated source artifacts and registered tables.",
}

TOOLS: dict[str, dict[str, Any]] = {
    name: {
        "name": name,
        "version": "1.0",
        "description": _TOOL_DESCRIPTIONS[name],
        "arguments_schema": model.model_json_schema(),
        "read_only": True,
    }
    for name, model in TOOL_ARGUMENT_MODELS.items()
}

CAPABILITIES: dict[str, frozenset[str]] = {
    "observer": frozenset(
        {
            "evidence.search",
            "lineage.trace",
            "changes.list",
            "runbook.get",
            "historical_incidents.search",
            "anomalies.list",
            "impact.summary",
            "artifacts.summary",
        }
    ),
    "investigator": frozenset(TOOLS),
    "approver": frozenset(TOOLS),
}


def catalogue() -> list[dict[str, Any]]:
    return [TOOLS[name] for name in sorted(TOOLS)]


def openai_tools(role: str = "investigator") -> list[dict[str, Any]]:
    allowed = CAPABILITIES.get(role, frozenset())
    result: list[dict[str, Any]] = []
    for name in sorted(allowed):
        item = TOOLS[name]
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": item["description"],
                    "parameters": item["arguments_schema"],
                },
            }
        )
    return result
