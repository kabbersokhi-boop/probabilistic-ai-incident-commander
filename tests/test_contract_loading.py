from __future__ import annotations

from pathlib import Path

import pytest

from paic.contracts.loader import ContractBundle, ContractLoadError, load_contract_bundle


def test_loads_complete_bundle(bundle: ContractBundle) -> None:
    assert bundle.project.project.name == "Probabilistic AI Incident Commander"
    assert bundle.project.project.version == "0.2.0"
    assert len(bundle.incidents) == 5
    assert len(bundle.evaluation.metrics) >= 20
    assert bundle.safety.default_deny is True


def test_missing_contract_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ContractLoadError, match="missing required contract files"):
        load_contract_bundle(tmp_path)


def test_incident_ids_and_seeds_are_unique(bundle: ContractBundle) -> None:
    incident_ids = [incident.incident_id for incident in bundle.incidents]
    seeds = [incident.random_seed for incident in bundle.incidents]
    assert len(incident_ids) == len(set(incident_ids))
    assert len(seeds) == len(set(seeds))


def test_ground_truth_is_represented_but_not_marked_on_hypotheses(
    bundle: ContractBundle,
) -> None:
    for incident in bundle.incidents:
        hypothesis_ids = {hypothesis.hypothesis_id for hypothesis in incident.candidate_hypotheses}
        assert incident.hidden_ground_truth.root_cause_hypothesis_id in hypothesis_ids
        for hypothesis in incident.candidate_hypotheses:
            assert "is_ground_truth" not in type(hypothesis).model_fields


def test_invalid_yaml_fails(tmp_path: Path, spec_dir: Path) -> None:
    import shutil

    shutil.copytree(spec_dir, tmp_path / "specs")
    (tmp_path / "specs" / "project.yaml").write_text("project: [", encoding="utf-8")
    with pytest.raises(ContractLoadError, match="invalid YAML"):
        load_contract_bundle(tmp_path / "specs")


def test_invalid_model_and_missing_incidents_fail(tmp_path: Path, spec_dir: Path) -> None:
    import shutil

    copied = tmp_path / "invalid-model"
    shutil.copytree(spec_dir, copied)
    (copied / "project.yaml").write_text("schema_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(ContractLoadError, match="invalid contract"):
        load_contract_bundle(copied)

    copied = tmp_path / "no-incidents"
    shutil.copytree(spec_dir, copied)
    shutil.rmtree(copied / "incidents")
    with pytest.raises(ContractLoadError, match="no incident contracts"):
        load_contract_bundle(copied)
