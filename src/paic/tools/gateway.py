"""Provider-neutral deterministic Governed Tool Gateway orchestration."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import polars as pl
from pydantic import ValidationError

from paic.analytics.io import load_analytics
from paic.detection.io import load_detection
from paic.evidence.io import load_evidence
from paic.impact.io import load_impact
from paic.simulator.io import load_dataset
from paic.tools.binding import BindingError, BoundSources, bind_sources
from paic.tools.catalogue import catalogue
from paic.tools.ledger import AuditLedger, canonical
from paic.tools.models import ToolError, ToolRequest, ToolResponse
from paic.tools.policy import authorize
from paic.tools.sql import SQLPolicy, SQLPolicyError, execute


class GatewayError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _json_rows(frame: pl.DataFrame) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads(frame.write_json()))


def _evidence_refs(value: Any) -> list[str]:
    refs: set[str] = set()
    queue: deque[Any] = deque([value])
    while queue:
        item = queue.popleft()
        if isinstance(item, dict):
            direct = item.get("evidence_record_id")
            if isinstance(direct, str):
                refs.add(direct)
            many = item.get("evidence_record_ids")
            if isinstance(many, list):
                refs.update(str(value) for value in many)
            raw = item.get("evidence_record_ids_json")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = []
                if isinstance(parsed, list):
                    refs.update(str(value) for value in parsed)
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)
    return sorted(refs)


class Gateway:
    def __init__(
        self,
        *,
        row_limit: int = 500,
        byte_limit: int = 100_000,
        query_timeout_seconds: float = 3.0,
    ):
        if row_limit < 1 or byte_limit < 1 or query_timeout_seconds <= 0:
            raise ValueError("gateway limits must be positive")
        self.row_limit = row_limit
        self.byte_limit = byte_limit
        self.sql_policy = SQLPolicy(
            row_limit=row_limit,
            byte_limit=byte_limit,
            timeout_seconds=query_timeout_seconds,
        )

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return catalogue()

    def _frames(self, bound: BoundSources) -> dict[str, pl.DataFrame]:
        frames: dict[str, pl.DataFrame] = {}
        _dataset_manifest, dataset_tables = load_dataset(bound.dataset)
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

    def _bounded(self, value: Any) -> tuple[Any, bool]:
        if isinstance(value, list):
            result = value[: self.row_limit]
            truncated = len(value) > len(result)
            while result and len(canonical(result).encode()) > self.byte_limit:
                result.pop()
                truncated = True
            return result, truncated
        encoded = canonical(value).encode()
        if len(encoded) > self.byte_limit:
            raise GatewayError("tool result exceeds byte limit")
        return value, False

    @staticmethod
    def _search(frame: pl.DataFrame, query: str) -> pl.DataFrame:
        if not query or frame.is_empty():
            return frame
        mask = pl.lit(False)
        for column in frame.columns:
            if frame.schema[column] == pl.String:
                mask = mask | pl.col(column).str.to_lowercase().str.contains(
                    query.lower(), literal=True
                )
        return frame.filter(mask)

    def _evidence_search(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        frame = frames.get("evidence__evidence_records", pl.DataFrame())
        return _json_rows(self._search(frame, str(args.get("query", ""))).head(int(args["limit"])))

    def _lineage_trace(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        nodes = frames.get("evidence__lineage_nodes", pl.DataFrame())
        edges = frames.get("evidence__lineage_edges", pl.DataFrame())
        if nodes.is_empty() or edges.is_empty():
            return {"nodes": [], "edges": []}
        node_id = str(args["node_id"])
        if nodes.filter(pl.col("node_id") == node_id).is_empty():
            raise GatewayError("unknown lineage node")
        direction = str(args["direction"])
        depth = int(args["depth"])
        selected_nodes = {node_id}
        selected_edges: set[str] = set()
        frontier = {node_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for row in edges.iter_rows(named=True):
                upstream = str(row["upstream_node_id"])
                downstream = str(row["downstream_node_id"])
                include = False
                if direction in {"both", "downstream"} and upstream in frontier:
                    next_frontier.add(downstream)
                    include = True
                if direction in {"both", "upstream"} and downstream in frontier:
                    next_frontier.add(upstream)
                    include = True
                if include:
                    selected_edges.add(str(row["edge_id"]))
            next_frontier -= selected_nodes
            if not next_frontier:
                break
            selected_nodes.update(next_frontier)
            frontier = next_frontier
        node_rows = _json_rows(nodes.filter(pl.col("node_id").is_in(sorted(selected_nodes))))
        edge_rows = _json_rows(edges.filter(pl.col("edge_id").is_in(sorted(selected_edges))))
        return {"nodes": node_rows, "edges": edge_rows}

    def _changes(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        parts: list[pl.DataFrame] = []
        changes = frames.get("evidence__config_changes", pl.DataFrame())
        flags = frames.get("evidence__feature_flag_states", pl.DataFrame())
        if not changes.is_empty():
            parts.append(changes.with_columns(pl.lit("configuration").alias("record_kind")))
        if not flags.is_empty():
            parts.append(flags.with_columns(pl.lit("feature_flag").alias("record_kind")))
        if not parts:
            return []
        frame = pl.concat(parts, how="diagonal_relaxed")
        if args.get("service") and "service" in frame.columns:
            frame = frame.filter(pl.col("service") == str(args["service"]))
        frame = self._search(frame, str(args.get("query", "")))
        sort_columns = [
            name for name in ("changed_at", "valid_from", "record_kind") if name in frame
        ]
        if sort_columns:
            frame = frame.sort(sort_columns, nulls_last=True)
        return _json_rows(frame.head(int(args["limit"])))

    def _runbooks(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        frame = frames.get("evidence__runbooks", pl.DataFrame())
        if frame.is_empty():
            return []
        if args.get("runbook_id"):
            frame = frame.filter(pl.col("runbook_id") == str(args["runbook_id"]))
        else:
            frame = self._search(frame, str(args.get("query", "")))
        return _json_rows(frame.head(int(args["limit"])))

    def _historical(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        frame = frames.get("evidence__historical_incidents", pl.DataFrame())
        if frame.is_empty():
            return []
        if args.get("family") and "family" in frame.columns:
            frame = frame.filter(pl.col("family") == str(args["family"]))
        if args.get("service") and "service" in frame.columns:
            frame = frame.filter(pl.col("service") == str(args["service"]))
        frame = self._search(frame, str(args.get("query", "")))
        if "started_at" in frame.columns:
            frame = frame.sort("started_at", descending=True)
        return _json_rows(frame.head(int(args["limit"])))

    def _anomalies(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        frame = frames.get("detection__anomaly_events", pl.DataFrame())
        if frame.is_empty():
            return []
        if args.get("metric") and "metric_name" in frame.columns:
            frame = frame.filter(pl.col("metric_name") == str(args["metric"]))
        if args.get("severity") and "severity" in frame.columns:
            frame = frame.filter(pl.col("severity") == str(args["severity"]))
        sort_columns = [name for name in ("started_at", "event_id") if name in frame.columns]
        if sort_columns:
            frame = frame.sort(sort_columns, descending=True)
        return _json_rows(frame.head(int(args["limit"])))

    def _impact(self, frames: dict[str, pl.DataFrame], args: dict[str, Any]) -> Any:
        result: dict[str, Any] = {}
        for suffix in ("financial_impact", "causal_estimates", "segment_impact", "model_metrics"):
            frame = frames.get(f"impact__{suffix}", pl.DataFrame())
            if frame.is_empty():
                continue
            if args.get("segment") and "segment" in frame.columns:
                frame = frame.filter(pl.col("segment") == str(args["segment"]))
            result[suffix] = _json_rows(frame.head(self.row_limit))
        return result

    def _run(
        self, request: ToolRequest, bound: BoundSources, args: dict[str, Any]
    ) -> tuple[Any, bool]:
        frames = self._frames(bound)
        tool = request.tool
        if tool == "sql.query":
            return execute(
                str(args["query"]),
                frames,
                policy=self.sql_policy,
                requested_limit=cast(int | None, args.get("limit")),
            )
        if tool == "evidence.search":
            return self._bounded(self._evidence_search(frames, args))
        if tool == "lineage.trace":
            return self._bounded(self._lineage_trace(frames, args))
        if tool == "changes.list":
            return self._bounded(self._changes(frames, args))
        if tool == "runbook.get":
            return self._bounded(self._runbooks(frames, args))
        if tool == "historical_incidents.search":
            return self._bounded(self._historical(frames, args))
        if tool == "anomalies.list":
            return self._bounded(self._anomalies(frames, args))
        if tool == "impact.summary":
            return self._bounded(self._impact(frames, args))
        if tool == "artifacts.summary":
            result = {
                "source_manifest_hashes": bound.hashes,
                "tables": sorted(frames) if args["include_tables"] else [],
                "table_count": len(frames),
            }
            return self._bounded(result)
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
            "normalized_arguments": decision.normalized_arguments,
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
            result, truncated = self._run(req, bound, decision.normalized_arguments)
            encoded = canonical(result).encode()
            refs = _evidence_refs(result)
            base.update(
                {
                    "execution_status": "success",
                    "source_manifest_hashes": bound.hashes,
                    "row_count": len(result) if isinstance(result, list) else 1,
                    "byte_count": len(encoded),
                    "truncated": truncated,
                    "evidence_record_ids": refs,
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
