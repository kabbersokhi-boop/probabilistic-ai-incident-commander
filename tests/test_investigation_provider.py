from __future__ import annotations

import urllib.error
from email.message import Message
from pathlib import Path
from urllib.request import Request

import pytest

from paic.investigation.config import InvestigationConfig, load_investigation_config
from paic.investigation.models import ChatMessage
from paic.investigation.provider import NvidiaNIMProvider, ProviderError


def test_default_config_uses_ordered_nim_fallbacks(repo_root: Path) -> None:
    config = load_investigation_config(repo_root / "configs" / "investigation" / "smoke.yaml")
    assert [item.model for item in config.provider.models] == [
        "nvidia/nemotron-3-super-120b-a12b",
        "qwen/qwen3.5-122b-a10b",
        "nvidia/nemotron-3-nano-30b-a3b",
    ]


def test_nim_provider_requires_environment_key_without_exposing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = InvestigationConfig.model_validate(
        {
            "schema_version": "1.0",
            "investigation_id": "key-test",
            "provider": {"api_key_env": "NVIDIA_API_KEY_TEST", "models": [{"model": "x/y"}]},
        }
    )
    monkeypatch.delenv("NVIDIA_API_KEY_TEST", raising=False)
    provider = NvidiaNIMProvider(config.provider, config.provider.models[0])
    with pytest.raises(ProviderError) as exc:
        provider.complete([ChatMessage(role="user", content="hello")], [])
    assert exc.value.code == "missing_api_key"
    assert "Bearer" not in str(exc.value)


def test_provider_config_rejects_insecure_remote_endpoint() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        InvestigationConfig.model_validate(
            {
                "schema_version": "1.0",
                "investigation_id": "bad-endpoint",
                "provider": {
                    "base_url": "http://example.com/v1",
                    "models": [{"model": "x/y"}],
                },
            }
        )


class _HTTPResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self) -> _HTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


def _live_config() -> InvestigationConfig:
    return InvestigationConfig.model_validate(
        {
            "schema_version": "1.0",
            "investigation_id": "http-test",
            "provider": {"api_key_env": "NVIDIA_API_KEY_TEST", "models": [{"model": "x/y"}]},
        }
    )


def test_nim_provider_parses_openai_tool_response(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    config = _live_config()
    monkeypatch.setenv("NVIDIA_API_KEY_TEST", "temporary-test-key")
    payload = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "t1",
                            "function": {
                                "name": "evidence__search",
                                "arguments": '{"query":"checkout"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
    }
    captured: dict[str, object] = {}

    def respond(request: object, *args: object, **kwargs: object) -> _HTTPResponse:
        del args, kwargs
        assert isinstance(request, Request)
        assert isinstance(request.data, bytes)
        captured["body"] = json.loads(request.data.decode())
        captured["authorization"] = request.headers.get("Authorization")
        return _HTTPResponse(json.dumps(payload).encode())

    monkeypatch.setattr(
        "paic.investigation.provider.urllib.request.urlopen",
        respond,
    )
    response = NvidiaNIMProvider(config.provider, config.provider.models[0]).complete(
        [ChatMessage(role="user", content="investigate")], []
    )
    assert response.tool_calls[0].name == "evidence__search"
    assert response.tool_calls[0].arguments == {"query": "checkout"}
    assert response.usage.total_tokens == 7
    body = captured["body"]
    assert isinstance(body, dict)
    assert "extra_body" not in body
    assert "chat_template_kwargs" not in body
    assert captured["authorization"] == "Bearer temporary-test-key"
    assert "temporary-test-key" not in json.dumps(body)


def test_nim_provider_rejects_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _live_config().model_copy(
        update={"provider": _live_config().provider.model_copy(update={"max_response_bytes": 1024})}
    )
    monkeypatch.setenv("NVIDIA_API_KEY_TEST", "temporary-test-key")
    monkeypatch.setattr(
        "paic.investigation.provider.urllib.request.urlopen",
        lambda *args, **kwargs: _HTTPResponse(b"x" * 1025),
    )
    with pytest.raises(ProviderError) as exc:
        NvidiaNIMProvider(config.provider, config.provider.models[0]).complete(
            [ChatMessage(role="user", content="investigate")], []
        )
    assert exc.value.code == "response_too_large"


def test_nim_provider_classifies_http_and_malformed_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    config = _live_config()
    monkeypatch.setenv("NVIDIA_API_KEY_TEST", "temporary-test-key")

    def rate_limited(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise urllib.error.HTTPError("url", 429, "busy", Message(), None)

    monkeypatch.setattr("paic.investigation.provider.urllib.request.urlopen", rate_limited)
    with pytest.raises(ProviderError) as exc:
        NvidiaNIMProvider(config.provider, config.provider.models[0]).complete(
            [ChatMessage(role="user", content="investigate")], []
        )
    assert exc.value.retryable
    assert exc.value.code == "rate_limited"

    monkeypatch.setattr(
        "paic.investigation.provider.urllib.request.urlopen",
        lambda *args, **kwargs: _HTTPResponse(b"not-json"),
    )
    with pytest.raises(ProviderError) as malformed:
        NvidiaNIMProvider(config.provider, config.provider.models[0]).complete(
            [ChatMessage(role="user", content="investigate")], []
        )
    assert malformed.value.code == "invalid_response"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("status", "body", "code", "kind"),
    [
        (401, b"", "authentication_failed", "fatal"),
        (403, b"", "authentication_failed", "fatal"),
        (404, b"", "model_unavailable", "route"),
        (429, b"", "rate_limited", "transient"),
        (500, b"", "http_500", "transient"),
        (400, b'{"error":{"code":"unsupported_model"}}', "route_incompatible", "route"),
        (422, b'{"error":{"code":"invalid_arguments"}}', "invalid_request", "fatal"),
    ],
)
def test_nim_provider_failure_kinds_are_explicit(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, code: str, kind: str
) -> None:
    config = _live_config()
    monkeypatch.setenv("NVIDIA_API_KEY_TEST", "temporary-test-key")

    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise urllib.error.HTTPError(
            "url", status, "error", Message(), __import__("io").BytesIO(body)
        )

    monkeypatch.setattr("paic.investigation.provider.urllib.request.urlopen", fail)
    with pytest.raises(ProviderError) as exc:
        NvidiaNIMProvider(config.provider, config.provider.models[0]).complete([], [])
    assert exc.value.code == code
    assert exc.value.kind == kind
