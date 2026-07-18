"""Provider-neutral deterministic gateway orchestration."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import polars as pl
from pydantic import ValidationError

from paic.analytics.io import load_analytics
from paic.detection.io import load_detection
from paic.evidence.io import load_evidence
from paic.impact.io import load_impact
from paic.tools.binding import BindingError, BoundSources, bind_sources
from paic.tools.catalogue import catalogue
from paic.tools.ledger import AuditLedger, canonical
from paic.tools.models import ToolError, ToolRequest, ToolResponse
from paic.tools.policy import authorize
from paic.tools.sql import SQLPolicyError, execute


class GatewayError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Gateway:
    def __init__(self, *, row_limit: int = 500, byte_limit: int = 100_000):
        self.row_limit = row_limit
        self.byte_limit = byte_limit

    @staticmethod
    def list_tools() -> list[dict[str, object]]:
        return catalogue()

    def _frames(self, bound: BoundSources) -> dict[str, pl.DataFrame]:
        frames: dict[str, pl.DataFrame] = {}
        _dataset_manifest, dataset_tables = __import__(
            "paic.simulator.io", fromlist=["load_dataset"]
        ).load_dataset(bound.dataset)
        for name, frame in dataset_tables.items():
            frames[f"dataset__{name}"] = frame
        if bound.analytics:
            for name, frame in load_analytics(bound.analytics).tables.items():
                frames[f"analytics__{name}"] = frame
        if bound.detection:
            for name, frame in load_detection(bound.detection).tables.items():
                frames[f"detection__{name}"] = frame
        if bound.impact:
            for name, frame in load_impact(bound.impact).tables.items():
                frames[f"impact__{name}"] = frame
        if bound.evidence:
            for name, frame in load_evidence(bound.evidence).tables.items():
                frames[f"evidence__{name}"] = frame
        return frames

    @staticmethod
    def _select(
        frames: dict[str, pl.DataFrame], prefix: str, args: dict[str, Any]
    ) -> list[dict[str, Any]]:
        candidates = [(name, frame) for name, frame in frames.items() if name.startswith(prefix)]
        if not candidates:
            return []
        _name, frame = candidates[0]
        query = args.get("query", args.get("text", ""))
        if query:
            mask = pl.lit(False)
            for col in frame.columns:
                if frame.schema[col] == pl.String:
                    mask = mask | pl.col(col).str.to_lowercase().str.contains(
                        str(query).lower(), literal=True
                    )
            frame = frame.filter(mask)
        if args.get("limit") is not None:
            frame = frame.head(int(args["limit"]))
        return cast(list[dict[str, Any]], json.loads(frame.write_json()))

    def _run(self, request: ToolRequest, bound: BoundSources) -> tuple[Any, list[str], bool]:
        frames = self._frames(bound)
        args = request.arguments
        tool = request.tool
        if tool == "sql.query":
            rows, trunc = execute(
                str(args.get("query", "")), frames, self.row_limit, self.byte_limit
            )
            return (
                rows,
                [str(row["evidence_record_id"]) for row in rows if "evidence_record_id" in row],
                trunc,
            )
        if tool == "evidence.search":
            return self._select(frames, "evidence__evidence_records", args), [], False
        if tool == "lineage.trace":
            nodes = frames.get("evidence__lineage_nodes", pl.DataFrame()).filter(pl.lit(True))
            edges = frames.get("evidence__lineage_edges", pl.DataFrame()).filter(pl.lit(True))
            node = args.get("node_id")
            if node:
                nodes = nodes.filter(pl.col("node_id") == node)
                edges = edges.filter(
                    (pl.col("upstream_node_id") == node) | (pl.col("downstream_node_id") == node)
                )
            return (
                {"nodes": json.loads(nodes.write_json()), "edges": json.loads(edges.write_json())},
                [],
                False,
            )
        mapping = {
            "changes.list": "evidence__config_changes",
            "runbook.get": "evidence__runbooks",
            "historical_incidents.search": "evidence__historical_incidents",
            "anomalies.list": "detection__anomaly_events",
            "impact.summary": "impact__customer_impact_summary",
        }
        if tool in mapping:
            return self._select(frames, mapping[tool], args), [], False
        if tool == "artifacts.summary":
            return {"tables": sorted(frames), "source_manifest_hashes": bound.hashes}, [], False
        raise GatewayError("unknown tool")

    def invoke(self, request: ToolRequest | dict[str, Any]) -> ToolResponse:
        try:
            req = (
                request if isinstance(request, ToolRequest) else ToolRequest.model_validate(request)
            )
        except ValidationError as exc:
            raise GatewayError(f"invalid request: {exc}") from exc
        decision = authorize(req.role, req.tool, req.tool_version, req.arguments)
        call_id = str(req.call_id or uuid5(NAMESPACE_URL, canonical(req.model_dump(mode="json"))))
        base: dict[str, Any] = {
            "call_id": call_id,
            "incident_id": req.incident_id,
            "tool": req.tool,
            "tool_version": req.tool_version,
            "policy_decision": "allow" if decision.allowed else "deny",
            "execution_status": "error",
            "source_manifest_hashes": {},
            "row_count": 0,
            "byte_count": 0,
            "truncated": False,
            "evidence_record_ids": [],
            "result": None,
            "result_sha256": _hash(None),
        }
        ledger = AuditLedger(req.audit_dir) if req.audit_dir else None
        if not decision.allowed:
            base["error"] = ToolError(code=decision.code, message=decision.reason).model_dump()
            response = ToolResponse.model_validate(base)
            if ledger:
                ledger.append(
                    req.model_dump(mode="json"),
                    response.model_dump(mode="json"),
                    policy="deny",
                    sources={},
                )
            return response
        try:
            bound = bind_sources(
                req.dataset_dir,
                req.analytics_dir,
                req.detection_dir,
                req.impact_dir,
                req.evidence_dir,
            )
            result, refs, truncated = self._run(req, bound)
            encoded = canonical(result).encode()
            base.update(
                {
                    "execution_status": "success",
                    "source_manifest_hashes": bound.hashes,
                    "row_count": len(result) if isinstance(result, list) else 1,
                    "byte_count": len(encoded),
                    "truncated": truncated,
                    "evidence_record_ids": sorted(set(refs)),
                    "result": result,
                    "result_sha256": _hash(result),
                }
            )
        except (BindingError, GatewayError, SQLPolicyError, OSError, ValueError) as exc:
            base["error"] = ToolError(code="request_rejected", message=str(exc)).model_dump()
        response = ToolResponse.model_validate(base)
        if ledger:
            ledger.append(
                req.model_dump(mode="json"),
                response.model_dump(mode="json"),
                policy="allow",
                sources=response.source_manifest_hashes,
            )
        return response
