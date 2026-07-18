from __future__ import annotations

from pathlib import Path

import pytest

from paic.evidence.config import EvidenceConfig, EvidenceConfigError, load_evidence_config


def test_evidence_config_loads(evidence_smoke_config: EvidenceConfig) -> None:
    assert evidence_smoke_config.incident.primary_service == "checkout-service"
    assert len(evidence_smoke_config.services) == 3
    assert len(evidence_smoke_config.lineage_edges) == 3


def test_config_rejects_unknown_lineage_node(evidence_smoke_config: EvidenceConfig) -> None:
    edge = evidence_smoke_config.lineage_edges[0].model_copy(
        update={"upstream_node_id": "missing-node"}
    )
    raw = evidence_smoke_config.model_dump(mode="python")
    raw["lineage_edges"] = [edge.model_dump(mode="python")]
    with pytest.raises(ValueError, match="unknown node"):
        EvidenceConfig.model_validate(raw)


def test_load_evidence_config_fails_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(EvidenceConfigError, match="cannot read"):
        load_evidence_config(missing)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("schema_version: [", encoding="utf-8")
    with pytest.raises(EvidenceConfigError, match="invalid YAML"):
        load_evidence_config(invalid)


def test_config_validators_reject_invalid_windows_and_duplicates(
    evidence_smoke_config: EvidenceConfig,
) -> None:
    raw = evidence_smoke_config.model_dump(mode="python")
    raw["incident"]["ended_at"] = raw["incident"]["started_at"]
    with pytest.raises(ValueError, match="ended_at"):
        EvidenceConfig.model_validate(raw)

    raw = evidence_smoke_config.model_dump(mode="python")
    raw["services"] = [raw["services"][0], raw["services"][0]]
    with pytest.raises(ValueError, match="unique"):
        EvidenceConfig.model_validate(raw)

    raw = evidence_smoke_config.model_dump(mode="python")
    raw["output"]["compression"] = "snappy"
    raw["output"]["compression_level"] = 6
    with pytest.raises(ValueError, match="does not accept"):
        EvidenceConfig.model_validate(raw)


def test_feature_flag_and_historical_windows_are_strict(
    evidence_smoke_config: EvidenceConfig,
) -> None:
    raw = evidence_smoke_config.model_dump(mode="python")
    raw["feature_flags"][0]["valid_to"] = raw["feature_flags"][0]["valid_from"]
    with pytest.raises(ValueError, match="valid_to"):
        EvidenceConfig.model_validate(raw)

    raw = evidence_smoke_config.model_dump(mode="python")
    raw["historical_incidents"][0]["ended_at"] = raw["historical_incidents"][0]["started_at"]
    with pytest.raises(ValueError, match="historical incident"):
        EvidenceConfig.model_validate(raw)
