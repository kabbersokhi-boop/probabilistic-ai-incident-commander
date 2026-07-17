"""Strict configuration for deterministic analytical artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from paic.analytics.registry import (
    ANALYTIC_DIMENSIONS,
    METRIC_REGISTRY,
    DimensionName,
    resolve_metric_names,
)

NonEmptyText = Annotated[str, Field(min_length=1)]
TimeGrain = Literal["hour", "day"]


class AnalyticsConfigError(RuntimeError):
    """Raised when an analytics configuration cannot be loaded."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CohortSpec(StrictModel):
    """Named zero-, one-, or two-dimensional cohort."""

    name: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    dimensions: Annotated[list[DimensionName], Field(max_length=2)]

    @model_validator(mode="after")
    def dimensions_are_unique(self) -> CohortSpec:
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("cohort dimensions must be unique")
        return self


class ContributionSpec(StrictModel):
    """Adjacent-period contribution analysis for a ratio metric."""

    name: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    metric: NonEmptyText
    dimension: DimensionName
    time_grain: TimeGrain


class AnalyticsOutputConfig(StrictModel):
    compression: Literal["zstd", "snappy", "gzip", "lz4", "uncompressed"] = "zstd"
    compression_level: Annotated[int, Field(ge=1, le=22)] | None = 6
    row_group_size: Annotated[int, Field(ge=1_000)] = 50_000
    include_statistics: bool = True

    @model_validator(mode="after")
    def compression_level_matches_codec(self) -> AnalyticsOutputConfig:
        if self.compression in {"snappy", "lz4", "uncompressed"} and self.compression_level:
            raise ValueError(f"{self.compression} does not accept compression_level")
        if (
            self.compression == "gzip"
            and self.compression_level is not None
            and self.compression_level > 9
        ):
            raise ValueError("gzip compression_level must be between 1 and 9")
        return self


class AnalyticsConfig(StrictModel):
    """Complete deterministic semantic-layer configuration."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    analytics_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    timezone: Literal["UTC"] = "UTC"
    time_grains: Annotated[list[TimeGrain], Field(min_length=1)]
    minimum_denominator: Annotated[int, Field(ge=1)] = 25
    metrics: Annotated[list[NonEmptyText], Field(min_length=1)]
    cohorts: Annotated[list[CohortSpec], Field(min_length=1)]
    funnel_cohorts: Annotated[list[NonEmptyText], Field(min_length=1)]
    contributions: list[ContributionSpec] = Field(default_factory=list)
    output: AnalyticsOutputConfig = Field(default_factory=AnalyticsOutputConfig)

    @model_validator(mode="after")
    def configuration_is_coherent(self) -> AnalyticsConfig:
        if len(self.time_grains) != len(set(self.time_grains)):
            raise ValueError("time_grains must be unique")
        cohort_names = [item.name for item in self.cohorts]
        if len(cohort_names) != len(set(cohort_names)):
            raise ValueError("cohort names must be unique")
        dimension_sets = [tuple(item.dimensions) for item in self.cohorts]
        if len(dimension_sets) != len(set(dimension_sets)):
            raise ValueError("cohort dimension sets must be unique")
        overall = [item for item in self.cohorts if not item.dimensions]
        if len(overall) != 1 or overall[0].name != "overall":
            raise ValueError("exactly one zero-dimensional cohort named 'overall' is required")
        unknown_funnels = sorted(set(self.funnel_cohorts).difference(cohort_names))
        if unknown_funnels:
            raise ValueError(f"unknown funnel cohorts: {', '.join(unknown_funnels)}")
        if len(self.funnel_cohorts) != len(set(self.funnel_cohorts)):
            raise ValueError("funnel_cohorts must be unique")

        metric_names = resolve_metric_names(self.metrics)
        cohort_map = {item.name: item for item in self.cohorts}
        contribution_names = [item.name for item in self.contributions]
        if len(contribution_names) != len(set(contribution_names)):
            raise ValueError("contribution names must be unique")
        for item in self.contributions:
            if item.metric not in metric_names:
                raise ValueError(f"contribution metric is not enabled: {item.metric}")
            definition = METRIC_REGISTRY.get(item.metric)
            if definition is None:
                raise ValueError(f"unknown contribution metric: {item.metric}")
            if definition.calculation not in {"ratio", "ratio_of_sums"}:
                raise ValueError(f"contribution metric must be a ratio: {item.metric}")
            if item.dimension not in definition.supported_dimensions:
                raise ValueError(
                    f"metric {item.metric} does not support dimension {item.dimension}"
                )
            matching = [
                cohort for cohort in cohort_map.values() if cohort.dimensions == [item.dimension]
            ]
            if not matching:
                raise ValueError(
                    f"contribution dimension requires a one-dimensional cohort: {item.dimension}"
                )
            if item.time_grain not in self.time_grains:
                raise ValueError(f"contribution grain is not enabled: {item.time_grain}")
        return self

    @property
    def metric_names(self) -> tuple[str, ...]:
        return resolve_metric_names(self.metrics)

    @property
    def cohort_map(self) -> dict[str, CohortSpec]:
        return {item.name: item for item in self.cohorts}


SUPPORTED_DIMENSIONS = frozenset(ANALYTIC_DIMENSIONS)


def load_analytics_config(path: str | Path) -> AnalyticsConfig:
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise AnalyticsConfigError(f"cannot read analytics config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise AnalyticsConfigError(
            f"invalid YAML in analytics config {config_path}: {exc}"
        ) from exc
    try:
        return AnalyticsConfig.model_validate(raw)
    except Exception as exc:
        raise AnalyticsConfigError(f"invalid analytics config {config_path}: {exc}") from exc
