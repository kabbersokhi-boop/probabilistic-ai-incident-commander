"""Bounded tool-using probabilistic investigation orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from paic.investigation.config import InvestigationConfig, ModelRoute
from paic.investigation.models import (
    ChatMessage,
    InvestigationProposal,
    InvestigationReport,
    InvestigationRequest,
    ToolTraceEntry,
    TranscriptEvent,
)
from paic.investigation.probability import ProposalValidationError, score_proposal
from paic.investigation.prompts import (
    SUBMIT_TOOL,
    gateway_name,
    system_prompt,
    tool_definitions,
    user_prompt,
)
from paic.investigation.provider import ChatProvider, ProviderError
from paic.investigation.router import ModelRouter, ProviderFactory
from paic.tools.binding import bind_sources
from paic.tools.gateway import Gateway
from paic.tools.ledger import canonical
from paic.tools.models import ToolRequest, ToolResponse


class InvestigationError(RuntimeError):
    pass


class ToolGateway(Protocol):
    def invoke(self, request: ToolRequest) -> ToolResponse: ...


def _require_source_lineage(response: ToolResponse, expected: Mapping[str, str]) -> None:
    if response.execution_status == "success" and response.source_manifest_hashes != dict(expected):
        raise InvestigationError("governed tool source lineage changed during investigation")


def _event(
    events: list[TranscriptEvent], event_type: str, payload: dict[str, Any]
) -> TranscriptEvent:
    previous = events[-1].event_sha256 if events else "0" * 64
    base = {
        "sequence": len(events) + 1,
        "event_type": event_type,
        "payload": payload,
        "previous_event_sha256": previous,
    }
    event_hash = hashlib.sha256(canonical(base).encode()).hexdigest()
    event = TranscriptEvent.model_validate({**base, "event_sha256": event_hash})
    events.append(event)
    return event


def _provider_event_payload(response: Any) -> dict[str, Any]:
    """Persist bounded operational metadata, never model free-form output."""

    content = response.content or ""
    return {
        "model": response.model,
        "finish_reason": response.finish_reason,
        "usage": response.usage.model_dump(mode="json"),
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in response.tool_calls
        ],
        "content_present": response.content is not None,
        "content_byte_count": len(content.encode("utf-8")),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


class Investigator:
    def __init__(
        self,
        config: InvestigationConfig,
        *,
        gateway: ToolGateway | None = None,
        provider_factory: ProviderFactory | None = None,
    ):
        self.config = config
        self.gateway = gateway or Gateway(byte_limit=config.budget.max_tool_result_bytes)
        self.router = ModelRouter(config, provider_factory)

    def run(
        self, request: InvestigationRequest | dict[str, Any]
    ) -> tuple[InvestigationReport, list[TranscriptEvent]]:
        try:
            req = (
                request
                if isinstance(request, InvestigationRequest)
                else InvestigationRequest.model_validate(request)
            )
        except ValidationError as exc:
            raise InvestigationError(f"invalid investigation request: {exc}") from exc
        bound = bind_sources(
            req.dataset_dir,
            req.analytics_dir,
            req.detection_dir,
            req.impact_dir,
            req.evidence_dir,
        )
        messages = [
            ChatMessage(role="system", content=system_prompt()),
            ChatMessage(
                role="user",
                content=user_prompt(req.incident_id, req.question, bound.hashes),
            ),
        ]
        definitions = tool_definitions(self.config.allowed_tools)
        events: list[TranscriptEvent] = []
        trace: list[ToolTraceEntry] = []
        observed_evidence: set[str] = set()
        tool_calls = 0
        total_tokens = 0
        for round_index in range(1, self.config.budget.max_rounds + 1):
            try:
                response = self.router.complete(messages, definitions)
            except ProviderError as exc:
                raise InvestigationError(f"all model routes failed: {exc.code}") from exc
            total_tokens += response.usage.total_tokens
            if total_tokens > self.config.budget.max_total_tokens:
                raise InvestigationError("provider token budget exceeded")
            _event(
                events,
                "provider_response",
                _provider_event_payload(response),
            )
            assistant_calls = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": canonical(call.arguments),
                    },
                }
                for call in response.tool_calls
            ]
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=assistant_calls or None,
                )
            )
            if not response.tool_calls:
                messages.append(
                    ChatMessage(
                        role="user",
                        content="Use the available tools or call submit_investigation with the required schema.",
                    )
                )
                continue
            if len(response.tool_calls) != 1:
                for parallel_call in response.tool_calls:
                    messages.append(
                        ChatMessage(
                            role="tool",
                            name=parallel_call.name,
                            tool_call_id=parallel_call.id,
                            content=canonical(
                                {
                                    "success": False,
                                    "error": "parallel tool calls are not allowed",
                                    "instruction": "Make exactly one tool call next.",
                                }
                            ),
                        )
                    )
                continue
            call = response.tool_calls[0]
            if call.name == SUBMIT_TOOL:
                try:
                    proposal = InvestigationProposal.model_validate(call.arguments)
                    report = score_proposal(
                        proposal,
                        investigation_id=self.config.investigation_id,
                        incident_id=req.incident_id,
                        question=req.question,
                        policy=self.config.decision,
                        observed_evidence=observed_evidence,
                        source_hashes=bound.hashes,
                        attempts=self.router.attempts,
                        trace=trace,
                        total_tokens=total_tokens,
                    )
                except (ValidationError, ProposalValidationError) as exc:
                    _event(
                        events,
                        "proposal_rejected",
                        {"tool_call_id": call.id, "error": str(exc)},
                    )
                    messages.append(
                        ChatMessage(
                            role="tool",
                            name=SUBMIT_TOOL,
                            tool_call_id=call.id,
                            content=canonical(
                                {
                                    "accepted": False,
                                    "error": str(exc),
                                    "instruction": "Repair the proposal using only observed evidence IDs.",
                                }
                            ),
                        )
                    )
                    continue
                _event(
                    events,
                    "proposal_accepted",
                    {
                        "tool_call_id": call.id,
                        "report_sha256": report.report_sha256,
                        "status": report.status,
                    },
                )
                return report, events
            tool = gateway_name(call.name)
            if tool not in self.config.allowed_tools:
                content = canonical({"success": False, "error": "tool is not allowed"})
                messages.append(
                    ChatMessage(role="tool", name=call.name, tool_call_id=call.id, content=content)
                )
                continue
            tool_calls += 1
            if tool_calls > self.config.budget.max_tool_calls:
                raise InvestigationError("tool-call budget exceeded")
            call_uuid = uuid5(
                NAMESPACE_URL,
                f"{self.config.investigation_id}:{round_index}:{call.id}:{tool}:{canonical(call.arguments)}",
            )
            tool_request = ToolRequest(
                tool=tool,
                incident_id=req.incident_id,
                role=req.role,
                arguments=call.arguments,
                dataset_dir=req.dataset_dir,
                analytics_dir=req.analytics_dir,
                detection_dir=req.detection_dir,
                impact_dir=req.impact_dir,
                evidence_dir=req.evidence_dir,
                audit_dir=req.audit_dir,
                call_id=call_uuid,
            )
            tool_response = self.gateway.invoke(tool_request)
            _require_source_lineage(tool_response, bound.hashes)
            # A model may cite only evidence returned by a successful governed
            # tool invocation. Error responses are not observations.
            if tool_response.execution_status == "success":
                observed_evidence.update(tool_response.evidence_record_ids)
            trace_entry = ToolTraceEntry(
                sequence=len(trace) + 1,
                call_id=tool_response.call_id,
                tool=tool,
                arguments=tool_response.normalized_arguments,
                execution_status=tool_response.execution_status,
                result_sha256=tool_response.result_sha256,
                evidence_record_ids=tool_response.evidence_record_ids,
                truncated=tool_response.truncated,
                error_code=tool_response.error.code if tool_response.error else None,
            )
            trace.append(trace_entry)
            safe_result = tool_response.model_dump(mode="json")
            _event(
                events,
                "tool_result",
                {
                    "tool_call_id": call.id,
                    "trace": trace_entry.model_dump(mode="json"),
                    "response": safe_result,
                },
            )
            messages.append(
                ChatMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.id,
                    content=canonical(safe_result),
                )
            )
        raise InvestigationError("investigation round budget exhausted without a valid proposal")


def scripted_factory(providers: Mapping[str, ChatProvider]) -> Callable[[ModelRoute], ChatProvider]:
    def factory(route: ModelRoute) -> ChatProvider:
        try:
            return providers[route.model]
        except KeyError as exc:
            raise InvestigationError(f"missing scripted provider for {route.model}") from exc

    return factory
