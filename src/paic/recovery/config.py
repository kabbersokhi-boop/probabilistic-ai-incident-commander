"""Strict configuration for statistical recovery verification."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from paic.recovery.models import HealthyDirection, Identifier, MetricRole


class RecoveryConfigError(RuntimeError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecoveryMetricPolicy(StrictModel):
    metric_id: Identifier
    cohort: Identifier = "overall"
    role: MetricRole
    healthy_direction: HealthyDirection
    baseline_lookback_periods: int = Field(default=24, ge=4, le=10_000)
    minimum_baseline_periods: int = Field(default=8, ge=3)
    minimum_post_periods: int = Field(default=4, ge=2)
    sustain_periods: int = Field(default=3, ge=2)
    minimum_sample_size: int = Field(default=20, ge=1)
    equivalence_margin_relative: float = Field(default=0.05, gt=0.0, le=1.0)
    equivalence_margin_absolute: float = Field(default=0.0, ge=0.0)
    minimum_within_band_fraction: float = Field(default=0.8, ge=0.5, le=1.0)
    minimum_improvement_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    severe_robust_z: float = Field(default=4.0, gt=0.0)

    @model_validator(mode="after")
    def validate_windows(self) -> RecoveryMetricPolicy:
        if self.minimum_baseline_periods > self.baseline_lookback_periods:
            raise ValueError("minimum baseline periods cannot exceed the lookback")
        if self.sustain_periods > self.minimum_post_periods:
            raise ValueError("sustain periods cannot exceed minimum post periods")
        return self


class RecoveryConfig(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    recovery_id: Identifier
    incident_id: Identifier
    alpha: float = Field(default=0.05, gt=0.0, lt=0.5)
    scale_floor: float = Field(default=1e-9, gt=0.0)
    reopen_after_consecutive_failures: int = Field(default=2, ge=1, le=20)
    immediate_reopen_on_severe_guardrail: bool = True
    metrics: Annotated[list[RecoveryMetricPolicy], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_metrics(self) -> RecoveryConfig:
        keys = [(item.metric_id, item.cohort) for item in self.metrics]
        if len(keys) != len(set(keys)):
            raise ValueError("recovery metric policies must be unique")
        if not any(item.role == "primary" for item in self.metrics):
            raise ValueError("recovery configuration requires at least one primary metric")
        return self


def load_recovery_config(path: str | Path) -> RecoveryConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecoveryConfigError(f"cannot read recovery config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RecoveryConfigError(f"invalid YAML in recovery config {config_path}: {exc}") from exc
    try:
        return RecoveryConfig.model_validate(raw)
    except ValueError as exc:
        raise RecoveryConfigError(f"invalid recovery config {config_path}: {exc}") from exc
