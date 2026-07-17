"""Validated configuration for the synthetic commerce simulator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveWeight = Annotated[float, Field(gt=0.0)]


class SimulatorConfigError(RuntimeError):
    """Raised when a simulator configuration cannot be loaded."""


class StrictModel(BaseModel):
    """Base model that rejects undocumented configuration fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WeightedValue(StrictModel):
    """A categorical value and its relative sampling weight."""

    value: NonEmptyText
    weight: PositiveWeight


class RegionConfig(StrictModel):
    """Regional demand, tax, and fulfilment characteristics."""

    code: Annotated[str, Field(pattern=r"^[A-Z]{2}(?:-[A-Z0-9]+)?$")]
    market: NonEmptyText
    weight: PositiveWeight
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    tax_rate: Annotated[float, Field(ge=0.0, le=0.5)]
    conversion_multiplier: Annotated[float, Field(gt=0.25, lt=2.0)]
    payment_approval_multiplier: Annotated[float, Field(gt=0.25, lt=2.0)]
    delivery_days: Annotated[int, Field(ge=1, le=14)]


class ScaleConfig(StrictModel):
    """Entity counts and baseline traffic volume."""

    customers: Annotated[int, Field(ge=100)]
    sellers: Annotated[int, Field(ge=5)]
    products: Annotated[int, Field(ge=50)]
    warehouses: Annotated[int, Field(ge=2)]
    promotions: Annotated[int, Field(ge=0)]
    base_sessions_per_hour: Annotated[float, Field(gt=0.0)]

    @model_validator(mode="after")
    def entity_counts_are_coherent(self) -> ScaleConfig:
        if self.products < self.sellers:
            raise ValueError("products must be greater than or equal to sellers")
        if self.customers < self.sellers:
            raise ValueError("customers must be greater than or equal to sellers")
        return self


class BehaviorConfig(StrictModel):
    """Normal operating probabilities used by the baseline simulator."""

    address_attempt_rate: Probability
    address_success_rate: Probability
    inventory_success_rate: Probability
    payment_attempt_rate: Probability
    base_payment_approval_rate: Probability
    base_return_rate: Probability
    base_late_delivery_rate: Probability
    refund_completion_rate: Probability
    promotion_session_share: Probability
    returning_customer_share: Probability
    preferred_device_adherence: Probability
    preferred_payment_adherence: Probability
    average_items_per_order: Annotated[float, Field(ge=1.0, le=5.0)]

    @model_validator(mode="after")
    def baseline_rates_are_plausible(self) -> BehaviorConfig:
        if self.address_attempt_rate < 0.8:
            raise ValueError("address_attempt_rate must represent a healthy baseline")
        if self.address_success_rate < 0.8:
            raise ValueError("address_success_rate must represent a healthy baseline")
        if self.inventory_success_rate < 0.8:
            raise ValueError("inventory_success_rate must represent a healthy baseline")
        if self.base_payment_approval_rate < 0.7:
            raise ValueError("base_payment_approval_rate must represent a healthy baseline")
        return self


class OutputConfig(StrictModel):
    """Parquet export settings."""

    compression: Literal["zstd", "snappy", "gzip", "lz4", "uncompressed"] = "zstd"
    compression_level: Annotated[int, Field(ge=1, le=22)] | None = 6
    row_group_size: Annotated[int, Field(ge=1_000)] = 50_000
    include_statistics: bool = True

    @model_validator(mode="after")
    def compression_level_matches_codec(self) -> OutputConfig:
        if self.compression in {"snappy", "lz4", "uncompressed"} and self.compression_level:
            raise ValueError(f"{self.compression} does not accept compression_level")
        return self


class SimulationConfig(StrictModel):
    """Complete deterministic commerce simulation configuration."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    simulation_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    seed: Annotated[int, Field(ge=1, le=2**63 - 1)]
    start_at: datetime
    duration_days: Annotated[int, Field(ge=1, le=365)]
    timezone: Literal["UTC"] = "UTC"
    scale: ScaleConfig
    behavior: BehaviorConfig
    regions: Annotated[list[RegionConfig], Field(min_length=2)]
    devices: Annotated[list[WeightedValue], Field(min_length=2)]
    channels: Annotated[list[WeightedValue], Field(min_length=2)]
    payment_methods: Annotated[list[WeightedValue], Field(min_length=2)]
    issuers: Annotated[list[WeightedValue], Field(min_length=2)]
    categories: Annotated[list[WeightedValue], Field(min_length=3)]
    carriers: Annotated[list[WeightedValue], Field(min_length=2)]
    acquisition_channels: Annotated[list[WeightedValue], Field(min_length=2)]
    services: Annotated[list[NonEmptyText], Field(min_length=3)]
    pipelines: Annotated[list[NonEmptyText], Field(min_length=2)]
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="before")
    @classmethod
    def normalize_start_at(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = data.get("start_at")
        if isinstance(raw, str):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            data = dict(data)
            data["start_at"] = parsed
        return data

    @model_validator(mode="after")
    def validate_catalogs(self) -> SimulationConfig:
        if self.start_at.tzinfo is None:
            raise ValueError("start_at must include an explicit UTC offset")
        if self.start_at.utcoffset() != timedelta(0):
            raise ValueError("start_at must be in UTC")
        if self.start_at.minute or self.start_at.second or self.start_at.microsecond:
            raise ValueError("start_at must be aligned to the hour")

        self._require_unique("regions", [item.code for item in self.regions])
        self._require_unique("devices", [item.value for item in self.devices])
        self._require_unique("channels", [item.value for item in self.channels])
        self._require_unique("payment_methods", [item.value for item in self.payment_methods])
        self._require_unique("issuers", [item.value for item in self.issuers])
        self._require_unique("categories", [item.value for item in self.categories])
        self._require_unique("carriers", [item.value for item in self.carriers])
        self._require_unique(
            "acquisition_channels", [item.value for item in self.acquisition_channels]
        )
        self._require_unique("services", list(self.services))
        self._require_unique("pipelines", list(self.pipelines))

        if self.scale.warehouses < len(self.regions):
            raise ValueError("warehouses must be at least the number of configured regions")
        return self

    @staticmethod
    def _require_unique(label: str, values: list[str]) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"{label} values must be unique")

    @property
    def end_at(self) -> datetime:
        """Exclusive logical end of the simulation window."""

        return self.start_at.astimezone(UTC) + timedelta(days=self.duration_days)


def load_simulation_config(path: str | Path) -> SimulationConfig:
    """Load and validate a simulator configuration from YAML."""

    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise SimulatorConfigError(f"cannot read simulator config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SimulatorConfigError(
            f"invalid YAML in simulator config {config_path}: {exc}"
        ) from exc

    try:
        return SimulationConfig.model_validate(raw)
    except Exception as exc:
        raise SimulatorConfigError(f"invalid simulator config {config_path}: {exc}") from exc
