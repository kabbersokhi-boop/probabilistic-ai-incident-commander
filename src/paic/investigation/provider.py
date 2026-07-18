"""Provider protocol, NVIDIA NIM client, and deterministic scripted provider."""

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
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


class ChatProvider(Protocol):
    def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]],
    ) -> ProviderResponse: ...


class NvidiaNIMProvider:
    """Minimal OpenAI-compatible client that never stores the API key."""

    def __init__(self, provider: ProviderConfig, route: ModelRoute):
        self.provider = provider
        self.route = route

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
                retryable=False,
            )
        payload: dict[str, Any] = {
            "model": self.route.model,
            "messages": [
                message.model_dump(mode="json", exclude_none=True) for message in messages
            ],
            "temperature": self.route.temperature,
            "top_p": self.route.top_p,
            "max_tokens": self.route.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        # NVIDIA model cards show these provider-specific fields inside
        # ``extra_body`` when using the OpenAI SDK. This raw HTTP client must
        # merge them into the request root. Avoid sending Nemotron-only fields
        # to other model families so fallback routes remain interoperable.
        if self.route.model.startswith("nvidia/nemotron"):
            payload["chat_template_kwargs"] = {
                "enable_thinking": self.route.enable_thinking,
                "force_nonempty_content": True,
            }
            if self.route.enable_thinking and self.route.reasoning_budget:
                payload["reasoning_budget"] = self.route.reasoning_budget
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
                        retryable=True,
                    )
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {400, 404, 408, 409, 422, 425, 429, 500, 502, 503, 504}
            code = "rate_limited" if exc.code == 429 else f"http_{exc.code}"
            raise ProviderError(
                code, f"NVIDIA NIM request failed with HTTP {exc.code}", retryable=retryable
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(
                "transport_error", "NVIDIA NIM request failed", retryable=True
            ) from exc
        try:
            decoded = json.loads(
                raw,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            choice = decoded["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise TypeError("message content must be a string or null")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                "invalid_response", "NVIDIA NIM response is malformed", retryable=True
            ) from exc
        calls: list[ProviderToolCall] = []
        for item in message.get("tool_calls") or []:
            try:
                function = item["function"]
                arguments = json.loads(
                    function.get("arguments") or "{}",
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
                if not isinstance(arguments, dict):
                    raise TypeError
                calls.append(
                    ProviderToolCall(
                        id=str(item["id"]),
                        name=str(function["name"]),
                        arguments=arguments,
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    "invalid_tool_call", "model returned a malformed tool call", retryable=True
                ) from exc
        usage_raw = decoded.get("usage") or {}
        try:
            usage = ProviderUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
                total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                "invalid_response", "NVIDIA NIM usage metadata is malformed", retryable=True
            ) from exc
        return ProviderResponse(
            model=self.route.model,
            content=content,
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )


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
                "script_exhausted", "scripted provider has no remaining responses", retryable=False
            )
        response = self.responses[self.index]
        self.index += 1
        return response.model_copy(update={"model": self.model})
