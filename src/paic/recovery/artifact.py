"""Atomic export, closed-world loading, and semantic replay for recovery artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from paic import __version__
from paic.artifacts.lease import artifact_reader
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.recovery.config import RecoveryConfig
from paic.recovery.engine import evaluate_recovery, verify_report
from paic.recovery.manifest import RecoveryArtifactFile, RecoveryArtifactManifest
from paic.recovery.models import RecoveryObservationSet, RecoveryReport
from paic.remediation.artifact import load_execution
from paic.remediation.artifact import manifest_sha256 as execution_manifest_sha256


class RecoveryArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedRecovery:
    manifest: RecoveryArtifactManifest
    config: RecoveryConfig
    observations: RecoveryObservationSet
    report: RecoveryReport


EXPECTED_PAYLOADS = {
    "recovery.config.resolved.json",
    "observation-set.json",
    "report.json",
    "metric-evaluations.jsonl",
}


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def manifest_sha256(path: str | Path) -> str:
    return file_sha256(Path(path) / "manifest.json")


def _write(path: Path, content: str) -> RecoveryArtifactFile:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)
    return RecoveryArtifactFile(
        relative_path=path.name,
        byte_size=path.stat().st_size,
        sha256=file_sha256(path),
    )


def export_recovery(
    config: RecoveryConfig,
    observations: RecoveryObservationSet,
    report: RecoveryReport,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> RecoveryArtifactManifest:
    verify_report(report)
    publisher = AtomicDirectoryPublisher(output_dir, overwrite=overwrite)
    try:
        with publisher as staged:
            files = [
                _write(
                    staged / "recovery.config.resolved.json",
                    config.model_dump_json(indent=2) + "\n",
                ),
                _write(
                    staged / "observation-set.json", observations.model_dump_json(indent=2) + "\n"
                ),
                _write(staged / "report.json", report.model_dump_json(indent=2) + "\n"),
                _write(
                    staged / "metric-evaluations.jsonl",
                    "".join(item.model_dump_json() + "\n" for item in report.metric_evaluations),
                ),
            ]
            manifest = RecoveryArtifactManifest(
                artifact_id=report.recovery_id,
                incident_id=report.incident_id,
                generator_version=__version__,
                status=report.decision,
                payload_sha256=report.report_sha256,
                bindings={
                    "execution_manifest": report.execution_manifest_sha256,
                    "execution_receipt": report.execution_receipt_sha256,
                    "config": report.config_sha256,
                    "observation_manifest": report.observation_manifest_sha256,
                },
                files=sorted(files, key=lambda item: item.relative_path),
            )
            manifest_path = staged / "manifest.json"
            with manifest_path.open("x", encoding="utf-8") as handle:
                handle.write(manifest.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(manifest_path, 0o600)
            marker = staged / "_SUCCESS"
            with marker.open("x", encoding="utf-8") as handle:
                handle.write(file_sha256(manifest_path) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(marker, 0o600)
            load_recovery(staged)
            publisher.commit()
            return manifest
    except ArtifactPublicationError as exc:
        raise RecoveryArtifactError(str(exc)) from exc


def _load_manifest(root: Path) -> RecoveryArtifactManifest:
    if not root.is_dir() or root.is_symlink():
        raise RecoveryArtifactError("recovery artifact root is not a regular directory")
    entries = list(root.iterdir())
    names = {item.name for item in entries}
    expected = EXPECTED_PAYLOADS | {"manifest.json", "_SUCCESS"}
    if names != expected:
        raise RecoveryArtifactError("recovery artifact contains missing or undeclared paths")
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise RecoveryArtifactError("recovery artifact contains a non-regular file")
    try:
        manifest = RecoveryArtifactManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RecoveryArtifactError(f"cannot load recovery manifest: {exc}") from exc
    declared = {item.relative_path for item in manifest.files}
    if declared != EXPECTED_PAYLOADS:
        raise RecoveryArtifactError("recovery manifest file set is invalid")
    for item in manifest.files:
        target = root / item.relative_path
        if target.stat().st_size != item.byte_size or file_sha256(target) != item.sha256:
            raise RecoveryArtifactError(
                f"recovery artifact file metadata mismatch: {item.relative_path}"
            )
    if (root / "_SUCCESS").read_text(encoding="utf-8").strip() != file_sha256(
        root / "manifest.json"
    ):
        raise RecoveryArtifactError("recovery success marker mismatch")
    return manifest


@artifact_reader
def load_recovery(path: str | Path) -> LoadedRecovery:
    root = Path(path)
    manifest = _load_manifest(root)
    try:
        config = RecoveryConfig.model_validate_json(
            (root / "recovery.config.resolved.json").read_text(encoding="utf-8")
        )
        observations = RecoveryObservationSet.model_validate_json(
            (root / "observation-set.json").read_text(encoding="utf-8")
        )
        report = RecoveryReport.model_validate_json(
            (root / "report.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RecoveryArtifactError(f"cannot load recovery artifact: {exc}") from exc
    try:
        verify_report(report)
    except RuntimeError as exc:
        raise RecoveryArtifactError(str(exc)) from exc
    lines = [
        line
        for line in (root / "metric-evaluations.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if [json.loads(line) for line in lines] != [
        item.model_dump(mode="json") for item in report.metric_evaluations
    ]:
        raise RecoveryArtifactError("recovery metric table differs from report")
    replayed = evaluate_recovery(
        config,
        observations,
        execution_manifest_sha256=report.execution_manifest_sha256,
        observation_manifest_sha256=report.observation_manifest_sha256,
        evaluated_at=report.evaluated_at,
    )
    if replayed != report:
        raise RecoveryArtifactError("recovery report does not match deterministic replay")
    expected_bindings = {
        "execution_manifest": report.execution_manifest_sha256,
        "execution_receipt": report.execution_receipt_sha256,
        "config": report.config_sha256,
        "observation_manifest": report.observation_manifest_sha256,
    }
    if (
        manifest.artifact_id != report.recovery_id
        or manifest.incident_id != report.incident_id
        or manifest.status != report.decision
        or manifest.payload_sha256 != report.report_sha256
        or manifest.bindings != expected_bindings
    ):
        raise RecoveryArtifactError("recovery manifest differs from report")
    return LoadedRecovery(manifest, config, observations, report)


@artifact_reader
def validate_recovery(
    path: str | Path,
    *,
    expected_execution_receipt_sha256: str | None = None,
    expected_execution_manifest_sha256: str | None = None,
    expected_incident_id: str | None = None,
    expected_executed_at: object | None = None,
    observations_dir: str | Path | None = None,
    analytics_dir: str | Path | None = None,
    execution_dir: str | Path | None = None,
) -> list[str]:
    # Import lazily to avoid the observations module's dependency on this
    # module's hashing helpers during import.  Observation validation is part
    # of the authoritative recovery contract, so its controlled failures must
    # be returned as validator issues rather than escaping to a caller such as
    # the TUI.
    from paic.recovery.observations import ObservationError

    try:
        if any(
            value is not None for value in (observations_dir, analytics_dir, execution_dir)
        ) and not all(
            value is not None for value in (observations_dir, analytics_dir, execution_dir)
        ):
            raise RecoveryArtifactError(
                "authoritative recovery validation requires observations, analytics, and execution artifacts"
            )
        loaded = load_recovery(path)
        if observations_dir is not None and analytics_dir is not None and execution_dir is not None:
            from paic.recovery.observations import load_observations, observation_manifest_sha256

            source_observations = load_observations(
                observations_dir, analytics_dir=analytics_dir, execution_dir=execution_dir
            )
            if source_observations != loaded.observations:
                raise RecoveryArtifactError("recovery artifact embeds another observation set")
            if (
                observation_manifest_sha256(observations_dir)
                != loaded.report.observation_manifest_sha256
            ):
                raise RecoveryArtifactError(
                    "recovery artifact is bound to another observation manifest"
                )
            execution = load_execution(execution_dir)
            actual_execution_manifest = execution_manifest_sha256(execution_dir)
            if (
                execution.receipt.incident_id != loaded.report.incident_id
                or execution.receipt.executed_at != loaded.observations.executed_at
                or execution.receipt.receipt_sha256 != loaded.report.execution_receipt_sha256
                or actual_execution_manifest != loaded.report.execution_manifest_sha256
            ):
                raise RecoveryArtifactError("recovery artifact execution identity is inconsistent")
        if (
            expected_execution_receipt_sha256 is not None
            and loaded.report.execution_receipt_sha256 != expected_execution_receipt_sha256
        ):
            raise RecoveryArtifactError("recovery artifact is bound to another execution receipt")
        if (
            expected_execution_manifest_sha256 is not None
            and loaded.report.execution_manifest_sha256 != expected_execution_manifest_sha256
        ):
            raise RecoveryArtifactError("recovery artifact is bound to another execution manifest")
        if expected_incident_id is not None and loaded.report.incident_id != expected_incident_id:
            raise RecoveryArtifactError("recovery artifact is bound to another incident")
        if (
            expected_executed_at is not None
            and loaded.observations.executed_at != expected_executed_at
        ):
            raise RecoveryArtifactError(
                "recovery artifact execution timestamp differs from receipt"
            )
    except (RecoveryArtifactError, ObservationError, OSError, ValueError) as exc:
        return [str(exc)]
    return []
