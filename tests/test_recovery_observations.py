from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from paic.recovery import observations as observation_module
from paic.recovery.artifact import file_sha256
from paic.recovery.observations import (
    ObservationError,
    ObservationScenario,
    build_observations,
    load_observations,
    validate_observations,
)


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture  # type: ignore[untyped-decorator]
def bound_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    analytics_dir = tmp_path / "analytics"
    execution_dir = tmp_path / "execution"
    analytics_dir.mkdir()
    execution_dir.mkdir()
    (analytics_dir / "manifest.json").write_text("analytics", encoding="utf-8")
    table = pl.DataFrame(
        {
            "metric_name": ["checkout_conversion_rate", "checkout_conversion_rate"],
            "cohort_name": ["overall", "overall"],
            "period_start": [
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
            ],
            "value": [0.80, 0.82],
            "sample_size": [100, 101],
        }
    )
    receipt = SimpleNamespace(
        incident_id="incident-smoke",
        executed_at=datetime(2026, 1, 3, tzinfo=UTC),
        receipt_sha256=_sha("receipt"),
    )
    monkeypatch.setattr(
        observation_module,
        "load_analytics",
        lambda _: SimpleNamespace(tables={"metric_observations": table}),
    )
    monkeypatch.setattr(
        observation_module,
        "load_manifest",
        lambda _: SimpleNamespace(source_simulation_id="simulation-smoke"),
    )
    monkeypatch.setattr(
        observation_module,
        "load_execution",
        lambda _: SimpleNamespace(receipt=receipt),
    )
    monkeypatch.setattr(observation_module, "manifest_sha256", lambda _: _sha("execution-manifest"))
    return analytics_dir, execution_dir


def _scenario() -> ObservationScenario:
    return ObservationScenario.model_validate(
        {
            "observation_set_id": "source-bound-observations",
            "generated_at_offset_hours": 4,
            "post_interval_hours": 1,
            "series": [
                {
                    "metric_id": "checkout_conversion_rate",
                    "cohort": "overall",
                    "values": [0.81, 0.82],
                    "sample_size": 120,
                }
            ],
        }
    )


def _refresh_payload_hashes(artifact: Path) -> None:
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in manifest["files"]:
        manifest["files"][name] = file_sha256(artifact / name)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (artifact / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")


def test_observation_artifact_replays_bound_sources(
    bound_sources: tuple[Path, Path], tmp_path: Path
) -> None:
    analytics_dir, execution_dir = bound_sources
    artifact = tmp_path / "observations"
    built = build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    loaded = load_observations(artifact, analytics_dir=analytics_dir, execution_dir=execution_dir)
    assert loaded == built
    assert loaded.evaluator_generated is True
    assert len(loaded.observations) == 4
    assert (
        validate_observations(artifact, analytics_dir=analytics_dir, execution_dir=execution_dir)
        == []
    )


def test_observation_artifact_rejects_refreshed_hash_semantic_tampering(
    bound_sources: tuple[Path, Path], tmp_path: Path
) -> None:
    analytics_dir, execution_dir = bound_sources
    artifact = tmp_path / "observations"
    build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    payload_path = artifact / "observation-set.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["observations"][-1]["value"] = 0.99
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    _refresh_payload_hashes(artifact)
    assert validate_observations(
        artifact, analytics_dir=analytics_dir, execution_dir=execution_dir
    ) == ["observation payload is not reproducible from bound sources"]


def test_observation_builder_rejects_absent_analytics_series(
    bound_sources: tuple[Path, Path], tmp_path: Path
) -> None:
    analytics_dir, execution_dir = bound_sources
    invalid = _scenario().model_copy(
        update={
            "series": [
                _scenario().series[0].model_copy(update={"metric_id": "payment_approval_rate"})
            ]
        }
    )
    with pytest.raises(ObservationError, match="absent"):
        build_observations(invalid, analytics_dir, execution_dir, tmp_path / "observations")


def test_observation_scenario_and_artifact_guards(
    bound_sources: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analytics_dir, execution_dir = bound_sources
    duplicated = _scenario().model_dump(mode="json")
    duplicated["series"].append(duplicated["series"][0])
    with pytest.raises(ValueError, match="unique"):
        ObservationScenario.model_validate(duplicated)
    monkeypatch.setattr(observation_module, "metric_catalog", lambda: [])
    with pytest.raises(ObservationError, match="unknown"):
        build_observations(_scenario(), analytics_dir, execution_dir, tmp_path / "unknown")


def test_observation_validation_rejects_closed_world_and_binding_tampering(
    bound_sources: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analytics_dir, execution_dir = bound_sources
    artifact = tmp_path / "observations"
    build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    (artifact / "extra").write_text("no", encoding="utf-8")
    assert "undeclared" in validate_observations(artifact)[0]
    (artifact / "extra").unlink()
    (artifact / "_SUCCESS").write_text("wrong\n", encoding="utf-8")
    assert "marker" in validate_observations(artifact)[0]
    _refresh_payload_hashes(artifact)
    config_path = artifact / "observation.config.resolved.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["generated_at_offset_hours"] = 5
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _refresh_payload_hashes(artifact)
    assert "configuration hash" in validate_observations(artifact)[0]

    build_observations(_scenario(), analytics_dir, execution_dir, artifact, overwrite=True)
    changed_receipt = SimpleNamespace(
        incident_id="other-incident",
        executed_at=datetime(2026, 1, 4, tzinfo=UTC),
        receipt_sha256=_sha("other-receipt"),
    )
    monkeypatch.setattr(
        observation_module, "load_execution", lambda _: SimpleNamespace(receipt=changed_receipt)
    )
    assert "another execution" in validate_observations(artifact, execution_dir=execution_dir)[0]


def test_observation_publication_overwrite_and_post_commit_fsync(
    bound_sources: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analytics_dir, execution_dir = bound_sources
    artifact = tmp_path / "observations"
    build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    with pytest.raises(ObservationError, match="already exists"):
        build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    original_fsync = observation_module._fsync_dir
    calls = 0

    def fail_only_parent(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory confirmation unavailable")
        original_fsync(path)

    monkeypatch.setattr(observation_module, "_fsync_dir", fail_only_parent)
    build_observations(_scenario(), analytics_dir, execution_dir, artifact, overwrite=True)
    assert load_observations(artifact, analytics_dir=analytics_dir, execution_dir=execution_dir)


def test_analytics_bound_validation_requires_execution_binding(
    bound_sources: tuple[Path, Path], tmp_path: Path
) -> None:
    analytics_dir, execution_dir = bound_sources
    artifact = tmp_path / "observations"
    build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    assert validate_observations(artifact, analytics_dir=analytics_dir) == [
        "analytics-bound observation replay requires the bound execution artifact"
    ]


def test_observation_builder_selects_one_coarsest_analytics_grain(
    bound_sources: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analytics_dir, execution_dir = bound_sources
    table = pl.DataFrame(
        {
            "metric_name": ["checkout_conversion_rate"] * 3,
            "cohort_name": ["overall"] * 3,
            "time_grain": ["hour", "day", "day"],
            "period_start": [
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
            ],
            "value": [0.1, 0.8, 0.82],
            "sample_size": [100, 100, 101],
        }
    )
    monkeypatch.setattr(
        observation_module,
        "load_analytics",
        lambda _: SimpleNamespace(tables={"metric_observations": table}),
    )
    built = build_observations(_scenario(), analytics_dir, execution_dir, tmp_path / "observations")
    baseline = [item.value for item in built.observations if item.observed_at < built.executed_at]
    assert baseline == [0.8, 0.82]


def test_observation_validation_rejects_hash_binding_and_series_tampering(
    bound_sources: tuple[Path, Path], tmp_path: Path
) -> None:
    analytics_dir, execution_dir = bound_sources
    artifact = tmp_path / "observations"
    build_observations(_scenario(), analytics_dir, execution_dir, artifact)
    payload_path = artifact / "observation-set.json"
    payload_path.write_text("{}", encoding="utf-8")
    assert "hash mismatch" in validate_observations(artifact)[0]

    build_observations(_scenario(), analytics_dir, execution_dir, artifact, overwrite=True)
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_id"] = "other-observations"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (artifact / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    assert "bindings" in validate_observations(artifact)[0]

    build_observations(_scenario(), analytics_dir, execution_dir, artifact, overwrite=True)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["observations"][0]["metric_id"] = "payment_approval_rate"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    _refresh_payload_hashes(artifact)
    assert (
        "unknown analytics series"
        in validate_observations(
            artifact, analytics_dir=analytics_dir, execution_dir=execution_dir
        )[0]
    )
