from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paic.simulator.io import (
    DatasetIOError,
    export_dataset,
    load_dataset,
    load_manifest,
)
from paic.simulator.types import SimulationResult
from paic.simulator.validation import validate_dataset_directory


def test_export_round_trip_and_manifest_integrity(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    output = tmp_path / "dataset"
    manifest = export_dataset(smoke_result, output)
    assert manifest.simulation_id == smoke_result.config.simulation_id
    assert manifest.incident_injections == 0
    assert manifest.runtime.python_version
    assert manifest.runtime.packages["polars"]
    assert manifest.runtime.packages["numpy"]
    assert len(manifest.tables) == 17
    assert (output / "_SUCCESS").is_file()
    assert (output / "config.resolved.json").is_file()
    assert (
        manifest.config_sha256
        == hashlib.sha256((output / "config.resolved.json").read_bytes()).hexdigest()
    )
    assert (output / "_SUCCESS").read_text(encoding="utf-8").strip() == manifest.config_sha256

    loaded_manifest, tables = load_dataset(output)
    assert loaded_manifest == manifest
    assert tables["customers"].equals(smoke_result.table("customers"))
    assert validate_dataset_directory(output).valid


def test_export_requires_explicit_overwrite(tmp_path: Path, smoke_result: SimulationResult) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)
    with pytest.raises(DatasetIOError, match="already exists"):
        export_dataset(smoke_result, output)
    export_dataset(smoke_result, output, overwrite=True)
    assert validate_dataset_directory(output).valid

    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(DatasetIOError, match="artifact target must be a directory"):
        export_dataset(smoke_result, file_path, overwrite=True)


def test_manifest_and_table_loading_fail_cleanly(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    with pytest.raises(DatasetIOError, match="cannot read dataset manifest"):
        load_manifest(tmp_path / "missing")

    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)
    (output / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(DatasetIOError, match="invalid dataset manifest"):
        load_manifest(output)

    export_dataset(smoke_result, output, overwrite=True)
    (output / "tables" / "customers.parquet").unlink()
    with pytest.raises(DatasetIOError, match="missing dataset table"):
        load_dataset(output)


def test_directory_validation_detects_hash_and_config_drift(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)

    manifest_path = output / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tables"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    report = validate_dataset_directory(output)
    assert not report.valid
    assert any(issue.code == "manifest.hash_mismatch" for issue in report.issues)

    export_dataset(smoke_result, output, overwrite=True)
    config_path = output / "config.resolved.json"
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_config["seed"] += 1
    config_path.write_text(json.dumps(raw_config), encoding="utf-8")
    report = validate_dataset_directory(output)
    assert not report.valid
    assert any(issue.code == "dataset.config_hash" for issue in report.issues)


def test_directory_validation_handles_missing_and_invalid_files(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    report = validate_dataset_directory(tmp_path / "missing")
    assert not report.valid
    assert report.issues[0].code == "dataset.load"

    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)
    (output / "config.resolved.json").write_text("{}", encoding="utf-8")
    report = validate_dataset_directory(output)
    assert not report.valid
    assert any(issue.code == "dataset.config" for issue in report.issues)


def test_directory_validation_detects_success_marker_and_manifest_metadata_drift(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)

    (output / "_SUCCESS").unlink()
    report = validate_dataset_directory(output)
    assert any(issue.code == "dataset.success_marker" for issue in report.issues)

    export_dataset(smoke_result, output, overwrite=True)
    manifest_path = output / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tables"][0]["byte_size"] += 1
    raw["tables"][0]["primary_key"] = []
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    report = validate_dataset_directory(output)
    codes = {issue.code for issue in report.issues}
    assert "manifest.byte_size" in codes
    assert "manifest.primary_key" in codes


def test_manifest_rejects_unsafe_or_ambiguous_table_paths(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)
    manifest_path = output / "manifest.json"

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tables"][0]["relative_path"] = "../outside.parquet"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DatasetIOError, match="inside the dataset directory"):
        load_manifest(output)

    export_dataset(smoke_result, output, overwrite=True)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tables"].append(dict(raw["tables"][0]))
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DatasetIOError, match="table names must be unique"):
        load_manifest(output)


def test_directory_validation_detects_manifest_config_identity_drift(
    tmp_path: Path, smoke_result: SimulationResult
) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)
    manifest_path = output / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["simulation_id"] = "different-simulation"
    raw["seed"] += 1
    raw["logical_end_at"] = raw["logical_start_at"].replace("00:00:00", "01:00:00")
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    report = validate_dataset_directory(output)
    codes = {issue.code for issue in report.issues}
    assert "manifest.simulation_id" in codes
    assert "manifest.seed" in codes
    assert "manifest.logical_end" in codes


def test_overwrite_failure_preserves_generation_and_cleans_publication_debris(
    tmp_path: Path,
    smoke_result: SimulationResult,
    rich_result: SimulationResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)
    previous = (output / "tables" / "customers.parquet").read_bytes()

    class FailingPublisher:
        def __init__(self, target: Path, *, overwrite: bool) -> None:
            from paic.artifacts.publication import AtomicDirectoryPublisher

            self._publisher = AtomicDirectoryPublisher(
                target, overwrite=overwrite, failure_hook=lambda point: _fail(point)
            )

        def __enter__(self) -> Path:
            return self._publisher.__enter__()

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            self._publisher.__exit__(exc_type, exc, traceback)

        def commit(self) -> object:
            return self._publisher.commit()

    def _fail(point: str) -> None:
        if point == "payload-written":
            raise RuntimeError("injected before commit")

    monkeypatch.setattr("paic.simulator.io.AtomicDirectoryPublisher", FailingPublisher)
    with pytest.raises(RuntimeError, match="injected before commit"):
        export_dataset(rich_result, output, overwrite=True)
    assert (output / "tables" / "customers.parquet").read_bytes() == previous
    assert validate_dataset_directory(output).valid
    assert not list(tmp_path.glob(".dataset.staging-*"))
    assert not list(tmp_path.glob(".dataset.backup-*"))


def test_overwrite_success_and_post_visibility_failure_are_explicit(
    tmp_path: Path,
    smoke_result: SimulationResult,
    rich_result: SimulationResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "dataset"
    export_dataset(smoke_result, output)

    export_dataset(rich_result, output, overwrite=True)
    assert validate_dataset_directory(output).valid
    assert not list(tmp_path.glob(".dataset.staging-*"))
    assert not list(tmp_path.glob(".dataset.backup-*"))

    def _fail_after_visibility(point: str) -> None:
        if point == "new-committed":
            raise RuntimeError("injected after visibility")

    class PostVisibilityPublisher:
        def __init__(self, target: Path, *, overwrite: bool) -> None:
            from paic.artifacts.publication import AtomicDirectoryPublisher

            self._publisher = AtomicDirectoryPublisher(
                target, overwrite=overwrite, failure_hook=_fail_after_visibility
            )

        def __enter__(self) -> Path:
            return self._publisher.__enter__()

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            self._publisher.__exit__(exc_type, exc, traceback)

        def commit(self) -> object:
            return self._publisher.commit()

    monkeypatch.setattr("paic.simulator.io.AtomicDirectoryPublisher", PostVisibilityPublisher)
    with pytest.raises(DatasetIOError, match="committed but durability is uncertain"):
        export_dataset(smoke_result, output, overwrite=True)
    assert validate_dataset_directory(output).valid


def test_overwrite_rejects_symlink_target(tmp_path: Path, smoke_result: SimulationResult) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "dataset"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(DatasetIOError, match="symbolic link"):
        export_dataset(smoke_result, link, overwrite=True)
