"""Strict configuration for probabilistic agentic investigation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvestigationConfigError(RuntimeError):
    pass


class ModelRoute(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    temperature: Annotated[float, Field(ge=0.0, le=1.0)] = 0.2
    top_p: Annotated[float, Field(gt=0.0, le=1.0)] = 0.95
    max_tokens: Annotated[int, Field(ge=128, le=32_768)] = 4_096
    enable_thinking: bool = False
    reasoning_budget: Annotated[int, Field(ge=0, le=32_768)] = 0

    @model_validator(mode="after")
    def validate_reasoning(self) -> ModelRoute:
        if not self.enable_thinking and self.reasoning_budget:
            raise ValueError("reasoning_budget requires enable_thinking")
        if self.reasoning_budget > self.max_tokens:
            raise ValueError("reasoning_budget cannot exceed max_tokens")
        # NVIDIA's current hosted Qwen and Nemotron Nano references expose the
        # thinking switch but do not document this route's reasoning budget.
        # Reject it instead of silently dropping configured intent.
        if (
            self.model
            in {
                "qwen/qwen3.5-122b-a10b",
                "nvidia/nemotron-3-nano-30b-a3b",
            }
            and self.reasoning_budget
        ):
            raise ValueError("reasoning_budget is unsupported for this hosted model route")
        return self


class ProviderConfig(StrictModel):
    kind: Literal["nvidia_nim"] = "nvidia_nim"
    base_url: str = "https://integrate.api.nvidia.com/v1"
    api_key_env: str = Field(default="NVIDIA_API_KEY", pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: Annotated[float, Field(gt=0.0, le=120.0)] = 45.0
    max_response_bytes: Annotated[int, Field(ge=1_024, le=10_000_000)] = 2_000_000
    models: list[ModelRoute]

    @model_validator(mode="after")
    def validate_models(self) -> ProviderConfig:
        parsed = urlparse(self.base_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("provider base_url must use HTTPS or loopback HTTP")
        if (
            not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("provider base_url contains unsupported URL components")
        names = [item.model for item in self.models]
        if not names:
            raise ValueError("at least one model route is required")
        if len(names) != len(set(names)):
            raise ValueError("model routes must be unique")
        return self


class InvestigationBudget(StrictModel):
    max_rounds: Annotated[int, Field(ge=1, le=30)] = 10
    max_tool_calls: Annotated[int, Field(ge=1, le=100)] = 24
    max_provider_failures: Annotated[int, Field(ge=0, le=20)] = 6
    max_total_tokens: Annotated[int, Field(ge=512, le=500_000)] = 40_000
    max_tool_result_bytes: Annotated[int, Field(ge=1_000, le=1_000_000)] = 100_000


class DecisionPolicy(StrictModel):
    minimum_top_posterior: Annotated[float, Field(ge=0.0, le=1.0)] = 0.55
    minimum_margin: Annotated[float, Field(ge=0.0, le=1.0)] = 0.15
    minimum_distinct_evidence: Annotated[int, Field(ge=1, le=50)] = 2
    maximum_normalized_entropy: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    likelihood_ratio_min: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.05
    likelihood_ratio_max: Annotated[float, Field(gt=1.0, le=100.0)] = 20.0


class InvestigationConfig(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    investigation_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    provider: ProviderConfig
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)
    decision: DecisionPolicy = Field(default_factory=DecisionPolicy)
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "artifacts.summary",
            "anomalies.list",
            "evidence.search",
            "changes.list",
            "lineage.trace",
            "runbook.get",
            "historical_incidents.search",
            "impact.summary",
            "sql.query",
        ]
    )

    @model_validator(mode="after")
    def validate_tools(self) -> InvestigationConfig:
        from paic.tools.catalogue import TOOLS

        if not self.allowed_tools:
            raise ValueError("at least one investigation tool is required")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed_tools must be unique")
        unknown = sorted(set(self.allowed_tools).difference(TOOLS))
        if unknown:
            raise ValueError(f"unknown investigation tools: {unknown}")
        return self


def load_investigation_config(path: str | Path) -> InvestigationConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvestigationConfigError(
            f"cannot read investigation config {config_path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise InvestigationConfigError(
            f"invalid YAML in investigation config {config_path}: {exc}"
        ) from exc
    try:
        return InvestigationConfig.model_validate(raw)
    except Exception as exc:
        raise InvestigationConfigError(
            f"invalid investigation config {config_path}: {exc}"
        ) from exc
