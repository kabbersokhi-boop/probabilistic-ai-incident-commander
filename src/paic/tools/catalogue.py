"""Versioned catalogue and role capabilities."""

from __future__ import annotations

TOOLS: dict[str, dict[str, object]] = {
    "sql.query": {"version": "1.0", "description": "Read-only query over validated tables"},
    "evidence.search": {"version": "1.0", "description": "Search canonical evidence records"},
    "lineage.trace": {"version": "1.0", "description": "Trace lineage graph relationships"},
    "changes.list": {"version": "1.0", "description": "List configuration and flag changes"},
    "runbook.get": {"version": "1.0", "description": "Retrieve a runbook"},
    "historical_incidents.search": {"version": "1.0", "description": "Search historical incidents"},
    "anomalies.list": {"version": "1.0", "description": "List detector anomalies"},
    "impact.summary": {"version": "1.0", "description": "Summarize customer impact"},
    "artifacts.summary": {"version": "1.0", "description": "Summarize bound artifacts"},
}
CAPABILITIES = {
    "observer": frozenset(
        {
            "artifacts.summary",
            "evidence.search",
            "lineage.trace",
            "runbook.get",
            "historical_incidents.search",
        }
    ),
    "investigator": frozenset(TOOLS),
    "approver": frozenset(TOOLS),
}


def catalogue() -> list[dict[str, object]]:
    return [{"name": name, **details} for name, details in sorted(TOOLS.items())]
