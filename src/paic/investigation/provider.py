"""Provider-neutral OpenAI-compatible clients and deterministic scripted provider."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Protocol

from paic.investigation.config import ModelRoute, ProviderConfig
from paic.investigation.models import (
    ChatMessage,
    ProviderResponse,
    ProviderToolCall,
    ProviderUsage,
)


class ProviderError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, kind: str | None = None, retryable: bool | None = None
    ):
        super().__init__(message)
        self.code = code
        # ``retryable`` remains accepted for scripted test doubles and callers;
        # production classifications use a precise failure kind.
        self.kind = kind or ("transient" if retryable else "fatal")
        self.retryable = self.kind in {"transient", "route"}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _http_failure(exc: urllib.error.HTTPError, provider_kind: str) -> tuple[str, str]:
    """Classify HTTP failure without retaining an unbounded provider body."""

    del provider_kind
    status = exc.code
    if status in {401, 403}:
        return ("authentication_failed", "fatal")
    if status == 429:
        return ("rate_limited", "transient")
    if status in {408, 425, 500, 502, 503, 504}:
        return (f"http_{status}", "transient")
    if status == 404:
        return ("model_unavailable", "route")
    if status in {400, 422}:
        # A bounded error code is sufficient to identify documented route/model
        # incompatibility. We do not retain or surface provider response prose.
        category = ""
        try:
            raw = exc.read(4096)
            decoded = json.loads(
                raw,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            error = decoded.get("error", {}) if isinstance(decoded, dict) else {}
            if isinstance(error, dict):
                category = str(error.get("code") or error.get("type") or "").lower()
        except (OSError, ValueError, json.JSONDecodeError):
            category = ""
        if "failed_generation" in category or ("tool" in category and "generation" in category):
            return ("tool_generation_failed", "tool_generation")
        if any(token in category for token in ("model", "route", "unsupported")):
            return ("route_incompatible", "route")
        return ("invalid_request", "fatal")
    return (f"http_{status}", "fatal")


class ChatProvider(Protocol):
    def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]],
    ) -> ProviderResponse: ...


class _OpenAICompatibleProvider:
    """Strict bounded OpenAI-compatible transport shared by hosted providers."""

    provider_label = "provider"

    def __init__(self, provider: ProviderConfig, route: ModelRoute):
        self.provider = provider
        self.route = route

    def _payload(
        self, messages: Sequence[ChatMessage], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.route.model,
            "messages": [
                message.model_dump(mode="json", exclude_none=True) for message in messages
            ],
            "temperature": self.route.temperature,
            "top_p": self.route.top_p,
            "stream": False,
        }
        if self.provider.kind == "groq":
            payload["max_completion_tokens"] = self.route.max_tokens
            payload["reasoning_effort"] = self.route.reasoning_effort or "low"
            payload["reasoning_format"] = self.route.reasoning_format or "hidden"
        else:
            payload["max_tokens"] = self.route.max_tokens
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto", "parallel_tool_calls": False})
        return payload

    def _parse(
        self, raw: bytes, known_tools: set[str], *, strict_type: bool = True
    ) -> ProviderResponse:
        try:
            decoded = json.loads(
                raw, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant
            )
            choices = decoded.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("choices must be a non-empty array")
            choice = choices[0]
            if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                raise ValueError("message must be an object")
            message = choice["message"]
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise TypeError("message content must be a string or null")
            calls: list[ProviderToolCall] = []
            raw_calls = message.get("tool_calls", [])
            if raw_calls is not None and not isinstance(raw_calls, list):
                raise TypeError("tool_calls must be an array")
            for item in raw_calls or []:
                if not isinstance(item, dict) or (strict_type and item.get("type") != "function"):
                    raise ValueError("tool call must be a function")
                function = item.get("function")
                if not isinstance(function, dict) or not isinstance(item.get("id"), str):
                    raise ValueError("tool call id and function are required")
                name = function.get("name")
                if not isinstance(name, str) or (
                    known_tools and name not in known_tools and name != "submit_investigation"
                ):
                    raise ValueError("unknown tool function")
                arguments = json.loads(
                    function.get("arguments") or "{}",
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
                if not isinstance(arguments, dict):
                    raise TypeError("tool arguments must be an object")
                calls.append(ProviderToolCall(id=item["id"], name=name, arguments=arguments))
            usage_raw = decoded.get("usage") or {}
            if not isinstance(usage_raw, dict):
                raise TypeError("usage must be an object")
            usage = ProviderUsage(
                **{
                    key: usage_raw.get(key, 0) or 0
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                "invalid_response", f"{self.provider_label} response is malformed", kind="transient"
            ) from exc
        return ProviderResponse(
            model=self.route.model,
            content=content,
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )

    def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]],
    ) -> ProviderResponse:
        api_key = os.environ.get(self.provider.api_key_env)
        if not api_key:
            raise ProviderError(
                "missing_api_key",
                f"environment variable {self.provider.api_key_env} is not set",
                kind="fatal",
            )
        payload = self._payload(messages, tools)
        request = urllib.request.Request(
            self.provider.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.provider.timeout_seconds) as response:
                raw = response.read(self.provider.max_response_bytes + 1)
                if len(raw) > self.provider.max_response_bytes:
                    raise ProviderError(
                        "response_too_large",
                        "NVIDIA NIM response exceeded the configured byte limit",
                        kind="transient",
                    )
        except urllib.error.HTTPError as exc:
            code, kind = _http_failure(exc, self.provider.kind)
            raise ProviderError(
                code, f"NVIDIA NIM request failed with HTTP {exc.code}", kind=kind
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(
                "transport_error", "NVIDIA NIM request failed", kind="transient"
            ) from exc
        try:
            return self._parse(
                raw,
                {str(item.get("function", {}).get("name")) for item in tools},
                strict_type=self.provider.kind == "groq",
            )
        except ProviderError:
            raise


class NvidiaNIMProvider(_OpenAICompatibleProvider):
    """NVIDIA NIM OpenAI-compatible client with explicit route fields."""

    provider_label = "NVIDIA NIM"

    def _payload(
        self, messages: Sequence[ChatMessage], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload = super()._payload(messages, tools)
        payload.pop("reasoning_effort", None)
        payload.pop("reasoning_format", None)
        if self.route.model == "nvidia/nemotron-3-super-120b-a12b":
            payload["chat_template_kwargs"] = {
                "enable_thinking": self.route.enable_thinking,
                "force_nonempty_content": True,
            }
            if self.route.enable_thinking and self.route.reasoning_budget:
                payload["reasoning_budget"] = self.route.reasoning_budget
        elif self.route.model in {"qwen/qwen3.5-122b-a10b", "nvidia/nemotron-3-nano-30b-a3b"}:
            payload["chat_template_kwargs"] = {"enable_thinking": self.route.enable_thinking}
        return payload


class GroqProvider(_OpenAICompatibleProvider):
    """Groq OpenAI-compatible client for GPT-OSS routes."""

    provider_label = "Groq"


class ScriptedProvider:
    """Offline provider used by CI, replay tests, and deterministic benchmarks."""

    def __init__(self, model: str, responses: Sequence[ProviderResponse | dict[str, Any]]):
        self.model = model
        self.responses = [
            item if isinstance(item, ProviderResponse) else ProviderResponse.model_validate(item)
            for item in responses
        ]
        self.index = 0

    def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]],
    ) -> ProviderResponse:
        del messages, tools
        if self.index >= len(self.responses):
            raise ProviderError(
                "script_exhausted", "scripted provider has no remaining responses", kind="fatal"
            )
        response = self.responses[self.index]
        self.index += 1
        return response.model_copy(update={"model": self.model})
