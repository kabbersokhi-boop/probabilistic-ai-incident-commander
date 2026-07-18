from __future__ import annotations

from pathlib import Path

import pytest

from paic.impact.config import ImpactConfigError, load_impact_config


def test_impact_config_loads(repo_root: Path) -> None:
    config = load_impact_config(repo_root / "configs" / "impact" / "smoke.yaml")
    assert config.incident.family == "checkout_failure"
    assert config.outcome.churn_horizon_days == 14
    assert config.causal.bootstrap_samples == 40


def test_impact_config_rejects_invalid_window(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """schema_version: '1.0'\nimpact_id: bad-impact\nincident:\n  incident_id: bad\n  family: checkout_failure\n  started_at: '2026-01-02T00:00:00Z'\n  ended_at: '2026-01-01T00:00:00Z'\n""",
        encoding="utf-8",
    )
    with pytest.raises(ImpactConfigError, match="ended_at"):
        load_impact_config(path)
