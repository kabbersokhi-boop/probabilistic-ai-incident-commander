"""Strict configuration for customer-impact, survival, and causal analysis."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]


class ImpactConfigError(RuntimeError):
    """Raised when an impact configuration cannot be loaded."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IncidentDefinition(StrictModel):
    incident_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    family: Literal[
        "checkout_failure",
        "payment_decline",
        "late_delivery",
        "refund_delay",
    ]
    started_at: datetime
    ended_at: datetime
    region: NonEmptyText | None = None
    device: NonEmptyText | None = None
    minimum_interactions: Annotated[int, Field(ge=1)] = 1

    @model_validator(mode="after")
    def validate_window(self) -> IncidentDefinition:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("incident timestamps must be timezone-aware")
        if self.ended_at <= self.started_at:
            raise ValueError("incident ended_at must be after started_at")
        return self


class OutcomeDefinition(StrictModel):
    churn_horizon_days: Annotated[int, Field(ge=7, le=365)] = 60
    pre_period_days: Annotated[int, Field(ge=7, le=365)] = 45
    ltv_horizon_days: Annotated[int, Field(ge=30, le=730)] = 180
    minimum_pre_orders: Annotated[int, Field(ge=0)] = 0


class BenchmarkEffect(StrictModel):
    enabled: bool = True
    delay_days: Annotated[int, Field(ge=0, le=180)] = 18
    censor_probability: Annotated[float, Field(ge=0.0, le=1.0)] = 0.18
    seed: int = 2026071704


class CausalConfig(StrictModel):
    propensity_caliper: Annotated[float, Field(gt=0.0, le=1.0)] = 0.20
    propensity_clip: Annotated[float, Field(gt=0.0, lt=0.5)] = 0.02
    bootstrap_samples: Annotated[int, Field(ge=20, le=5000)] = 200
    confidence_level: Annotated[float, Field(gt=0.5, lt=1.0)] = 0.95
    placebo_shift_days: Annotated[int, Field(ge=7, le=180)] = 21
    random_seed: int = 2026071705


class FinancialConfig(StrictModel):
    contribution_margin_rate: Annotated[float, Field(ge=0.0, le=1.0)] = 0.32
    support_cost_per_exposed_customer: Annotated[float, Field(ge=0.0)] = 35.0
    recovery_cost_per_exposed_customer: Annotated[float, Field(ge=0.0)] = 12.0


class OutputConfig(StrictModel):
    compression: Literal["zstd", "snappy", "gzip", "lz4", "uncompressed"] = "zstd"
    compression_level: Annotated[int, Field(ge=1, le=22)] | None = 6
    row_group_size: Annotated[int, Field(ge=100)] = 25_000
    include_statistics: bool = True

    @model_validator(mode="after")
    def validate_compression(self) -> OutputConfig:
        if self.compression in {"snappy", "lz4", "uncompressed"} and self.compression_level:
            raise ValueError(f"{self.compression} does not accept compression_level")
        if self.compression == "gzip" and (self.compression_level or 0) > 9:
            raise ValueError("gzip compression_level must be between 1 and 9")
        return self


class ImpactConfig(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    impact_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    timezone: Literal["UTC"] = "UTC"
    incident: IncidentDefinition
    outcome: OutcomeDefinition = Field(default_factory=OutcomeDefinition)
    benchmark_effect: BenchmarkEffect = Field(default_factory=BenchmarkEffect)
    causal: CausalConfig = Field(default_factory=CausalConfig)
    financial: FinancialConfig = Field(default_factory=FinancialConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def validate_observation_window(self) -> ImpactConfig:
        if self.incident.ended_at - self.incident.started_at > timedelta(days=14):
            raise ValueError("incident windows longer than 14 days are not supported")
        return self


def load_impact_config(path: str | Path) -> ImpactConfig:
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise ImpactConfigError(f"cannot read impact config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ImpactConfigError(f"invalid YAML in impact config {config_path}: {exc}") from exc
    try:
        return ImpactConfig.model_validate(raw)
    except Exception as exc:
        raise ImpactConfigError(f"invalid impact config {config_path}: {exc}") from exc
