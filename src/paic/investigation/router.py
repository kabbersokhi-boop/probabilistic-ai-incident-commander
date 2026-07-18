"""Ordered model routing with explicit fallback semantics."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from paic.investigation.config import InvestigationConfig, ModelRoute
from paic.investigation.models import ChatMessage, ModelAttempt, ProviderResponse
from paic.investigation.provider import ChatProvider, NvidiaNIMProvider, ProviderError

ProviderFactory = Callable[[ModelRoute], ChatProvider]


class ModelRouter:
    def __init__(
        self,
        config: InvestigationConfig,
        factory: ProviderFactory | None = None,
    ):
        self.config = config
        self.factory = factory or (lambda route: NvidiaNIMProvider(config.provider, route))
        self.attempts: list[ModelAttempt] = []
        self.failures = 0
        self.preferred_route_index = 0

    def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: list[dict[str, Any]],
    ) -> ProviderResponse:
        last_error: ProviderError | None = None
        routes = self.config.provider.models
        ordered = list(range(self.preferred_route_index, len(routes)))
        ordered.extend(range(0, self.preferred_route_index))
        for route_index in ordered:
            route = routes[route_index]
            provider = self.factory(route)
            try:
                response = provider.complete(messages, tools)
            except ProviderError as exc:
                self.failures += 1
                if exc.retryable:
                    self.attempts.append(
                        ModelAttempt(
                            model=route.model, status="retryable_error", error_code=exc.code
                        )
                    )
                else:
                    self.attempts.append(
                        ModelAttempt(model=route.model, status="fatal_error", error_code=exc.code)
                    )
                last_error = exc
                if self.failures > self.config.budget.max_provider_failures:
                    break
                if exc.retryable:
                    continue
                raise
            self.attempts.append(
                ModelAttempt(
                    model=route.model,
                    status="success",
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                )
            )
            # Stay on the first healthy route for later tool rounds. This avoids
            # repeatedly hitting a rate-limited primary while retaining ordered
            # fallback if the selected route later becomes unavailable.
            self.preferred_route_index = route_index
            return response
        if last_error is not None:
            raise last_error
        raise ProviderError("no_models", "no model route is configured", retryable=False)
