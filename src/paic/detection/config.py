"""Strict configuration models for deterministic anomaly detection."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from paic.analytics.config import TimeGrain
from paic.analytics.registry import ANALYTIC_DIMENSIONS, METRIC_REGISTRY, DimensionName

NonEmptyText = Annotated[str, Field(min_length=1)]
PerturbationKind = Literal["level_shift", "drift", "spike"]


class DetectionConfigError(RuntimeError):
    """Raised when a detection configuration cannot be loaded."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaselineWindow(StrictModel):
    lookback_periods: Annotated[int, Field(ge=4)]
    minimum_history: Annotated[int, Field(ge=3)]
    seasonal_periods: Annotated[int, Field(ge=1)]
    minimum_seasonal_history: Annotated[int, Field(ge=2)]
    minimum_scale: Annotated[float, Field(gt=0.0)] = 1e-6
    relative_scale_floor: Annotated[float, Field(ge=0.0)] = 0.01

    @model_validator(mode="after")
    def validate_history(self) -> BaselineWindow:
        if self.minimum_history > self.lookback_periods:
            raise ValueError("minimum_history must not exceed lookback_periods")
        if self.minimum_seasonal_history > self.lookback_periods:
            raise ValueError("minimum_seasonal_history must not exceed lookback_periods")
        return self


class BaselineConfig(StrictModel):
    hour: BaselineWindow
    day: BaselineWindow

    def for_grain(self, grain: TimeGrain) -> BaselineWindow:
        return self.hour if grain == "hour" else self.day


class CusumConfig(StrictModel):
    enabled: bool = True
    drift: Annotated[float, Field(gt=0.0)] = 0.5
    threshold: Annotated[float, Field(gt=0.0)] = 5.0


class SequentialConfig(StrictModel):
    enabled: bool = True
    alpha: Annotated[float, Field(gt=0.0, lt=0.5)] = 0.01
    alternative_standardized_shift: Annotated[float, Field(gt=0.0)] = 1.5


class AlertPolicy(StrictModel):
    fdr_alpha: Annotated[float, Field(gt=0.0, lt=0.5)] = 0.05
    robust_z_threshold: Annotated[float, Field(gt=0.0)] = 3.0
    minimum_detector_support: Annotated[int, Field(ge=1, le=4)] = 2
    minimum_relative_effect: Annotated[float, Field(ge=0.0)] = 0.08
    minimum_absolute_effect: Annotated[float, Field(ge=0.0)] = 0.0
    minimum_sample_size: Annotated[int, Field(ge=1)] = 10


class MetricPolicyOverride(StrictModel):
    metric: NonEmptyText
    robust_z_threshold: Annotated[float, Field(gt=0.0)] | None = None
    minimum_detector_support: Annotated[int, Field(ge=1, le=4)] | None = None
    minimum_relative_effect: Annotated[float, Field(ge=0.0)] | None = None
    minimum_absolute_effect: Annotated[float, Field(ge=0.0)] | None = None
    minimum_sample_size: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def metric_exists(self) -> MetricPolicyOverride:
        if self.metric not in METRIC_REGISTRY:
            raise ValueError(f"unknown metric override: {self.metric}")
        return self


class SeriesSelector(StrictModel):
    metric: NonEmptyText
    time_grains: Annotated[list[TimeGrain], Field(min_length=1)]
    cohorts: Annotated[list[NonEmptyText], Field(min_length=1)]

    @model_validator(mode="after")
    def selector_is_valid(self) -> SeriesSelector:
        if self.metric not in METRIC_REGISTRY:
            raise ValueError(f"unknown metric: {self.metric}")
        if len(self.time_grains) != len(set(self.time_grains)):
            raise ValueError("selector time_grains must be unique")
        if len(self.cohorts) != len(set(self.cohorts)):
            raise ValueError("selector cohorts must be unique")
        return self


class BenchmarkScenario(StrictModel):
    """A deterministic metric-level perturbation used only for detector evaluation."""

    scenario_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    metric: NonEmptyText
    time_grain: TimeGrain
    cohort: NonEmptyText
    dimension_values: dict[DimensionName, NonEmptyText] = Field(default_factory=dict)
    start_at: datetime
    duration_periods: Annotated[int, Field(ge=1)]
    kind: PerturbationKind
    magnitude: float
    expected_direction: Literal["increase", "decrease"]

    @model_validator(mode="after")
    def validate_scenario(self) -> BenchmarkScenario:
        if self.metric not in METRIC_REGISTRY:
            raise ValueError(f"unknown benchmark metric: {self.metric}")
        if self.magnitude == 0.0:
            raise ValueError("benchmark magnitude must be non-zero")
        if self.start_at.tzinfo is None:
            raise ValueError("benchmark start_at must be timezone-aware")
        unknown = set(self.dimension_values).difference(ANALYTIC_DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown benchmark dimensions: {sorted(unknown)}")
        if self.expected_direction == "increase" and self.magnitude < 0:
            raise ValueError("increase scenarios require a positive magnitude")
        if self.expected_direction == "decrease" and self.magnitude > 0:
            raise ValueError("decrease scenarios require a negative magnitude")
        if self.kind == "spike" and self.duration_periods > 3:
            raise ValueError("spike scenarios must last at most three periods")
        return self


class DetectionOutputConfig(StrictModel):
    compression: Literal["zstd", "snappy", "gzip", "lz4", "uncompressed"] = "zstd"
    compression_level: Annotated[int, Field(ge=1, le=22)] | None = 6
    row_group_size: Annotated[int, Field(ge=1_000)] = 50_000
    include_statistics: bool = True

    @model_validator(mode="after")
    def validate_compression(self) -> DetectionOutputConfig:
        if self.compression in {"snappy", "lz4", "uncompressed"} and self.compression_level:
            raise ValueError(f"{self.compression} does not accept compression_level")
        if self.compression == "gzip" and (self.compression_level or 0) > 9:
            raise ValueError("gzip compression_level must be between 1 and 9")
        return self


class DetectionConfig(StrictModel):
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    detection_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    timezone: Literal["UTC"] = "UTC"
    selectors: Annotated[list[SeriesSelector], Field(min_length=1)]
    baseline: BaselineConfig
    cusum: CusumConfig = Field(default_factory=CusumConfig)
    sequential: SequentialConfig = Field(default_factory=SequentialConfig)
    alert_policy: AlertPolicy = Field(default_factory=AlertPolicy)
    metric_overrides: list[MetricPolicyOverride] = Field(default_factory=list)
    benchmark_scenarios: list[BenchmarkScenario] = Field(default_factory=list)
    output: DetectionOutputConfig = Field(default_factory=DetectionOutputConfig)

    @model_validator(mode="after")
    def validate_configuration(self) -> DetectionConfig:
        selector_keys = [
            (item.metric, tuple(item.time_grains), tuple(item.cohorts)) for item in self.selectors
        ]
        if len(selector_keys) != len(set(selector_keys)):
            raise ValueError("detection selectors must be unique")
        override_names = [item.metric for item in self.metric_overrides]
        if len(override_names) != len(set(override_names)):
            raise ValueError("metric overrides must be unique")
        scenario_ids = [item.scenario_id for item in self.benchmark_scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("benchmark scenario IDs must be unique")
        selectable = {
            (selector.metric, grain, cohort)
            for selector in self.selectors
            for grain in selector.time_grains
            for cohort in selector.cohorts
        }
        for scenario in self.benchmark_scenarios:
            if (scenario.metric, scenario.time_grain, scenario.cohort) not in selectable:
                raise ValueError(
                    "benchmark scenario target is not selected: "
                    f"{scenario.metric}/{scenario.time_grain}/{scenario.cohort}"
                )
        return self

    @property
    def override_map(self) -> dict[str, MetricPolicyOverride]:
        return {item.metric: item for item in self.metric_overrides}


def load_detection_config(path: str | Path) -> DetectionConfig:
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise DetectionConfigError(f"cannot read detection config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise DetectionConfigError(
            f"invalid YAML in detection config {config_path}: {exc}"
        ) from exc
    try:
        return DetectionConfig.model_validate(raw)
    except Exception as exc:
        raise DetectionConfigError(f"invalid detection config {config_path}: {exc}") from exc
