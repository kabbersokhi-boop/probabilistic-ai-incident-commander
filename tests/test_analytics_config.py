from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paic.analytics.config import (
    AnalyticsConfig,
    AnalyticsConfigError,
    AnalyticsOutputConfig,
    load_analytics_config,
)
from paic.analytics.registry import METRIC_REGISTRY, resolve_metric_names


def test_smoke_and_standard_configs_are_strict_and_complete(repo_root: Path) -> None:
    smoke = load_analytics_config(repo_root / "configs" / "analytics" / "smoke.yaml")
    standard = load_analytics_config(repo_root / "configs" / "analytics" / "standard.yaml")

    assert smoke.metric_names == tuple(METRIC_REGISTRY)
    assert len(smoke.metric_names) == 43
    assert smoke.time_grains == ["day"]
    assert standard.time_grains == ["hour", "day"]
    assert len(standard.cohorts) == 18
    assert standard.cohort_map["region-device"].dimensions == ["region", "device"]
    assert len(standard.contributions) == 4


def test_metric_resolution_rejects_unknown_and_duplicate_names() -> None:
    assert resolve_metric_names(["checkout_sessions"]) == ("checkout_sessions",)
    with pytest.raises(ValueError, match="unknown metrics"):
        resolve_metric_names(["does-not-exist"])
    with pytest.raises(ValueError, match="must be unique"):
        resolve_metric_names(["checkout_sessions", "checkout_sessions"])
    with pytest.raises(ValueError, match="unknown metrics"):
        resolve_metric_names(["*", "checkout_sessions"])


def test_config_rejects_unknown_fields_and_incoherent_cohorts(
    analytics_smoke_config: AnalyticsConfig,
) -> None:
    payload = analytics_smoke_config.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AnalyticsConfig.model_validate(payload)

    payload = analytics_smoke_config.model_dump(mode="json")
    payload["cohorts"] = [
        {"name": "overall", "dimensions": []},
        {"name": "region", "dimensions": ["region"]},
        {"name": "duplicate-region", "dimensions": ["region"]},
    ]
    payload["funnel_cohorts"] = ["overall", "region"]
    payload["contributions"] = []
    with pytest.raises(ValidationError, match="dimension sets must be unique"):
        AnalyticsConfig.model_validate(payload)

    payload = analytics_smoke_config.model_dump(mode="json")
    payload["funnel_cohorts"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown funnel cohorts"):
        AnalyticsConfig.model_validate(payload)


def test_contribution_contract_requires_enabled_ratio_and_matching_cohort(
    analytics_smoke_config: AnalyticsConfig,
) -> None:
    payload = analytics_smoke_config.model_dump(mode="json")
    payload["metrics"] = ["checkout_sessions"]
    payload["contributions"] = [
        {
            "name": "invalid-count",
            "metric": "checkout_sessions",
            "dimension": "region",
            "time_grain": "day",
        }
    ]
    with pytest.raises(ValidationError, match="must be a ratio"):
        AnalyticsConfig.model_validate(payload)

    payload = analytics_smoke_config.model_dump(mode="json")
    payload["cohorts"] = [item for item in payload["cohorts"] if item["name"] != "issuer"]
    payload["funnel_cohorts"] = [item for item in payload["funnel_cohorts"] if item != "issuer"]
    with pytest.raises(ValidationError, match="requires a one-dimensional cohort"):
        AnalyticsConfig.model_validate(payload)


def test_output_codec_validation() -> None:
    with pytest.raises(ValidationError, match="does not accept compression_level"):
        AnalyticsOutputConfig(compression="snappy", compression_level=6)
    assert (
        AnalyticsOutputConfig(compression="snappy", compression_level=None).compression == "snappy"
    )
    with pytest.raises(ValidationError, match="gzip compression_level"):
        AnalyticsOutputConfig(compression="gzip", compression_level=10)
    assert AnalyticsOutputConfig(compression="gzip", compression_level=9).compression == "gzip"


def test_load_errors_are_wrapped(tmp_path: Path) -> None:
    with pytest.raises(AnalyticsConfigError, match="cannot read analytics config"):
        load_analytics_config(tmp_path / "missing.yaml")

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("analytics_id: [", encoding="utf-8")
    with pytest.raises(AnalyticsConfigError, match="invalid YAML"):
        load_analytics_config(invalid)
