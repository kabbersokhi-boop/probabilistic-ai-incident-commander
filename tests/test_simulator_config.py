from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from paic.simulator.config import (
    OutputConfig,
    SimulationConfig,
    SimulatorConfigError,
    load_simulation_config,
)
from paic.simulator.randomness import RandomFactory
from paic.simulator.utils import probability_vector, random_datetimes, stable_ids


def test_smoke_config_is_explicit_and_utc(smoke_config: SimulationConfig) -> None:
    assert smoke_config.simulation_id == "commerce-smoke-baseline"
    assert smoke_config.start_at.isoformat() == "2026-05-01T00:00:00+00:00"
    assert smoke_config.end_at.isoformat() == "2026-05-02T00:00:00+00:00"
    assert len(smoke_config.regions) == 4


def test_standard_config_is_large_enough_for_analytics_but_practical(repo_root: Path) -> None:
    config = load_simulation_config(repo_root / "configs" / "simulation" / "standard.yaml")
    assert config.duration_days == 14
    assert config.scale.customers == 5_000
    assert config.scale.products == 1_000
    assert config.scale.base_sessions_per_hour == 30
    assert config.end_at.isoformat() == "2026-04-15T00:00:00+00:00"


def test_configuration_rejects_duplicates_and_non_utc_start(
    smoke_config: SimulationConfig,
) -> None:
    raw = smoke_config.model_dump(mode="json")
    raw["devices"].append(deepcopy(raw["devices"][0]))
    with pytest.raises(ValidationError, match="devices values must be unique"):
        SimulationConfig.model_validate(raw)

    raw = smoke_config.model_dump(mode="json")
    raw["start_at"] = "2026-05-01T00:00:00+05:30"
    with pytest.raises(ValidationError, match="must be in UTC"):
        SimulationConfig.model_validate(raw)

    raw = smoke_config.model_dump(mode="json")
    raw["start_at"] = "2026-05-01T00:30:00Z"
    with pytest.raises(ValidationError, match="aligned to the hour"):
        SimulationConfig.model_validate(raw)


def test_configuration_rejects_invalid_entity_relationships(
    smoke_config: SimulationConfig,
) -> None:
    raw = smoke_config.model_dump(mode="json")
    raw["scale"]["products"] = 50
    raw["scale"]["sellers"] = 60
    with pytest.raises(ValidationError, match="products must be greater"):
        SimulationConfig.model_validate(raw)

    raw = smoke_config.model_dump(mode="json")
    raw["scale"]["warehouses"] = 2
    with pytest.raises(ValidationError, match="warehouses must be at least"):
        SimulationConfig.model_validate(raw)


def test_healthy_baseline_and_output_settings_are_enforced(
    smoke_config: SimulationConfig,
) -> None:
    raw = smoke_config.model_dump(mode="json")
    raw["behavior"]["base_payment_approval_rate"] = 0.5
    with pytest.raises(ValidationError, match="healthy baseline"):
        SimulationConfig.model_validate(raw)

    with pytest.raises(ValidationError, match="does not accept compression_level"):
        OutputConfig(compression="snappy", compression_level=6)


def test_config_loader_reports_file_yaml_and_model_errors(
    tmp_path: Path, smoke_config: SimulationConfig
) -> None:
    with pytest.raises(SimulatorConfigError, match="cannot read"):
        load_simulation_config(tmp_path / "missing.yaml")

    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("simulation: [", encoding="utf-8")
    with pytest.raises(SimulatorConfigError, match="invalid YAML"):
        load_simulation_config(invalid_yaml)

    invalid_model = tmp_path / "model.yaml"
    invalid_model.write_text("schema_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(SimulatorConfigError, match="invalid simulator config"):
        load_simulation_config(invalid_model)


def test_random_namespaces_are_stable_and_independent() -> None:
    first = RandomFactory(1234)
    second = RandomFactory(1234)
    assert first.seed_for("customers") == second.seed_for("customers")
    assert first.seed_for("customers") != first.seed_for("orders")
    assert first.numpy("customers").integers(0, 1_000) == second.numpy("customers").integers(
        0, 1_000
    )
    assert first.faker("sellers").company() == second.faker("sellers").company()


def test_utility_validation_and_identifiers(smoke_config: SimulationConfig) -> None:
    assert stable_ids("X", 3, width=2) == ["X-01", "X-02", "X-03"]
    probabilities = probability_vector([1.0, 3.0])
    assert probabilities.tolist() == [0.25, 0.75]
    with pytest.raises(ValueError, match="non-empty"):
        probability_vector([])
    with pytest.raises(ValueError, match="strictly positive"):
        probability_vector([1.0, 0.0])

    rng = RandomFactory(44).numpy("dates")
    dates = random_datetimes(rng, smoke_config.start_at, smoke_config.end_at, 5)
    assert all(smoke_config.start_at <= value < smoke_config.end_at for value in dates)
    with pytest.raises(ValueError, match="end must be after"):
        random_datetimes(rng, smoke_config.end_at, smoke_config.start_at, 1)
