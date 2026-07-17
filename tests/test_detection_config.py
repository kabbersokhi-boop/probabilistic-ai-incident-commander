from __future__ import annotations

from pathlib import Path

import pytest

from paic.detection.config import (
    DetectionConfig,
    DetectionConfigError,
    load_detection_config,
)


def test_public_detection_configs_load(repo_root: Path) -> None:
    smoke = load_detection_config(repo_root / "configs/detection/smoke.yaml")
    standard = load_detection_config(repo_root / "configs/detection/standard.yaml")

    assert smoke.detection_id == "commerce-detection-smoke"
    assert smoke.benchmark_scenarios == []
    assert standard.detection_id == "commerce-detection-standard"
    assert len(standard.benchmark_scenarios) == 10
    assert len(standard.selectors) == 9
    assert standard.override_map["checkout_conversion_rate"].minimum_relative_effect == 0.35


def test_missing_and_invalid_yaml_raise_clear_errors(tmp_path: Path) -> None:
    with pytest.raises(DetectionConfigError, match="cannot read detection config"):
        load_detection_config(tmp_path / "missing.yaml")

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("selectors: [", encoding="utf-8")
    with pytest.raises(DetectionConfigError, match="invalid YAML"):
        load_detection_config(invalid)


def test_configuration_rejects_unknown_metric_and_duplicate_selectors(
    detection_smoke_config: DetectionConfig,
) -> None:
    raw = detection_smoke_config.model_dump(mode="json")
    raw["selectors"][0]["metric"] = "not-a-metric"
    with pytest.raises(ValueError, match="unknown metric"):
        DetectionConfig.model_validate(raw)

    raw = detection_smoke_config.model_dump(mode="json")
    raw["selectors"].append(dict(raw["selectors"][0]))
    with pytest.raises(ValueError, match="selectors must be unique"):
        DetectionConfig.model_validate(raw)


def test_configuration_rejects_duplicate_overrides_and_benchmarks(
    detection_standard_config: DetectionConfig,
) -> None:
    raw = detection_standard_config.model_dump(mode="json")
    raw["metric_overrides"].append(dict(raw["metric_overrides"][0]))
    with pytest.raises(ValueError, match="metric overrides must be unique"):
        DetectionConfig.model_validate(raw)

    raw = detection_standard_config.model_dump(mode="json")
    raw["benchmark_scenarios"].append(dict(raw["benchmark_scenarios"][0]))
    with pytest.raises(ValueError, match="scenario IDs must be unique"):
        DetectionConfig.model_validate(raw)


def test_benchmark_target_must_be_selected(detection_standard_config: DetectionConfig) -> None:
    raw = detection_standard_config.model_dump(mode="json")
    raw["benchmark_scenarios"][0]["cohort"] = "device"
    with pytest.raises(ValueError, match="target is not selected"):
        DetectionConfig.model_validate(raw)


def test_benchmark_direction_and_window_invariants(
    detection_standard_config: DetectionConfig,
) -> None:
    raw = detection_standard_config.model_dump(mode="json")
    raw["benchmark_scenarios"][0]["magnitude"] = 0.2
    with pytest.raises(ValueError, match="decrease scenarios require a negative"):
        DetectionConfig.model_validate(raw)

    raw = detection_standard_config.model_dump(mode="json")
    spike = next(item for item in raw["benchmark_scenarios"] if item["kind"] == "spike")
    spike["duration_periods"] = 4
    with pytest.raises(ValueError, match="spike scenarios must last"):
        DetectionConfig.model_validate(raw)


def test_baseline_and_compression_invariants(detection_smoke_config: DetectionConfig) -> None:
    raw = detection_smoke_config.model_dump(mode="json")
    raw["baseline"]["day"]["minimum_history"] = 20
    with pytest.raises(ValueError, match="must not exceed lookback"):
        DetectionConfig.model_validate(raw)

    raw = detection_smoke_config.model_dump(mode="json")
    raw["output"]["compression"] = "snappy"
    with pytest.raises(ValueError, match="does not accept compression_level"):
        DetectionConfig.model_validate(raw)
