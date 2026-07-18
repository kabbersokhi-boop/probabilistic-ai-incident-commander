from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from paic.recovery.artifact import (
    RecoveryArtifactError,
    export_recovery,
    load_recovery,
    validate_recovery,
)
from paic.recovery.engine import evaluate_recovery
from paic.recovery.models import RecoveryReport
from test_recovery_unit import config, observations, sha


def build_artifact(path: Path) -> RecoveryReport:
    cfg = config()
    obs = observations()
    report = evaluate_recovery(cfg, obs, execution_manifest_sha256=sha("execution-manifest"))
    export_recovery(cfg, obs, report, path)
    return report


def test_recovery_artifact_round_trip_and_replay(tmp_path: Path) -> None:
    report = build_artifact(tmp_path / "recovery")
    loaded = load_recovery(tmp_path / "recovery")
    assert loaded.report == report
    assert (
        validate_recovery(tmp_path / "recovery", expected_execution_receipt_sha256=sha("receipt"))
        == []
    )


def test_closed_world_layout_rejects_extra_file(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    build_artifact(root)
    (root / "undeclared.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(RecoveryArtifactError, match="undeclared"):
        load_recovery(root)


def test_semantic_tamper_is_rejected_even_after_file_hash_refresh(tmp_path: Path) -> None:
    from paic.recovery.artifact import file_sha256
    from paic.recovery.engine import digest

    root = tmp_path / "recovery"
    build_artifact(root)
    report_path = root / "report.json"
    metric_path = root / "metric-evaluations.jsonl"
    value = json.loads(report_path.read_text(encoding="utf-8"))
    value["metric_evaluations"][0]["latest_center"] = 999.0
    payload = dict(value)
    payload.pop("report_sha256")
    value["report_sha256"] = digest(payload)
    report_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    metric_path.write_text(
        "".join(
            json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n"
            for item in value["metric_evaluations"]
        ),
        encoding="utf-8",
    )
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["payload_sha256"] = value["report_sha256"]
    for item in manifest["files"]:
        target = root / item["relative_path"]
        item["byte_size"] = target.stat().st_size
        item["sha256"] = file_sha256(target)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    issues = validate_recovery(root)
    assert issues and "deterministic replay" in issues[0]


def test_wrong_execution_binding_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    build_artifact(root)
    issues = validate_recovery(root, expected_execution_receipt_sha256=sha("another"))
    assert issues == ["recovery artifact is bound to another execution receipt"]


def test_artifact_rejects_missing_payload_and_symlink_root(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    build_artifact(root)
    (root / "report.json").unlink()
    with pytest.raises(RecoveryArtifactError, match="missing or undeclared"):
        load_recovery(root)
    link = tmp_path / "link"
    os.symlink(root, link)
    with pytest.raises(RecoveryArtifactError, match="regular directory"):
        load_recovery(link)


def test_artifact_rejects_metric_table_and_manifest_binding_tampering(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    build_artifact(root)
    metric_path = root / "metric-evaluations.jsonl"
    metric_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RecoveryArtifactError, match="metadata mismatch"):
        load_recovery(root)

    root = tmp_path / "recovery-two"
    build_artifact(root)
    manifest_path = root / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["incident_id"] = "other-incident"
    manifest_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(RecoveryArtifactError, match=r"metadata mismatch|success marker"):
        load_recovery(root)


def test_artifact_overwrite_survives_post_commit_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import paic.recovery.artifact as artifact_module

    root = tmp_path / "recovery"
    original_report = build_artifact(root)
    original_fsync = artifact_module._fsync_dir
    calls = 0

    def fail_only_parent(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("post-commit fsync unavailable")
        original_fsync(path)

    monkeypatch.setattr(artifact_module, "_fsync_dir", fail_only_parent)
    manifest = export_recovery(config(), observations(), original_report, root, overwrite=True)
    assert manifest.payload_sha256 == original_report.report_sha256
    assert load_recovery(root).report == original_report


def test_validate_recovery_checks_all_execution_identity_bindings(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    build_artifact(root)
    assert validate_recovery(root, expected_execution_manifest_sha256=sha("other")) == [
        "recovery artifact is bound to another execution manifest"
    ]
    assert validate_recovery(root, expected_incident_id="other-incident") == [
        "recovery artifact is bound to another incident"
    ]
    assert validate_recovery(root, expected_executed_at=datetime(2026, 1, 2, tzinfo=UTC)) == [
        "recovery artifact execution timestamp differs from receipt"
    ]


def test_authoritative_recovery_validation_replays_observation_and_execution_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import paic.recovery.artifact as artifact_module
    import paic.recovery.observations as observation_module

    cfg = config()
    obs = observations()
    root = tmp_path / "recovery"
    report = evaluate_recovery(cfg, obs, execution_manifest_sha256=sha("execution-manifest"))
    export_recovery(cfg, obs, report, root)
    monkeypatch.setattr(observation_module, "load_observations", lambda *_, **__: obs)
    monkeypatch.setattr(
        observation_module,
        "observation_manifest_sha256",
        lambda _: report.observation_manifest_sha256,
    )
    monkeypatch.setattr(
        artifact_module,
        "load_execution",
        lambda _: SimpleNamespace(
            receipt=SimpleNamespace(
                incident_id=obs.incident_id,
                executed_at=obs.executed_at,
                receipt_sha256=obs.execution_receipt_sha256,
            )
        ),
    )
    monkeypatch.setattr(
        artifact_module, "execution_manifest_sha256", lambda _: obs.execution_manifest_sha256
    )
    assert (
        validate_recovery(
            root,
            observations_dir=tmp_path / "observations",
            analytics_dir=tmp_path / "analytics",
            execution_dir=tmp_path / "execution",
        )
        == []
    )

    monkeypatch.setattr(
        observation_module,
        "load_observations",
        lambda *_, **__: obs.model_copy(update={"incident_id": "other-incident"}),
    )
    assert (
        "another observation set"
        in validate_recovery(
            root,
            observations_dir=tmp_path / "observations",
            analytics_dir=tmp_path / "analytics",
            execution_dir=tmp_path / "execution",
        )[0]
    )
    assert validate_recovery(root, observations_dir=tmp_path / "observations") == [
        "authoritative recovery validation requires observations, analytics, and execution artifacts"
    ]
