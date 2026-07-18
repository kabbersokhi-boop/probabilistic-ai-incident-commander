"""Validate and bind source artifacts to one dataset lineage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paic.analytics.validation import validate_analytics_directory
from paic.detection.validation import validate_detection_directory
from paic.evidence.validation import validate_evidence_directory
from paic.impact.validation import validate_impact_directory
from paic.simulator.io import file_sha256, load_dataset
from paic.simulator.validation import validate_dataset_directory


class BindingError(ValueError):
    pass


@dataclass(frozen=True)
class BoundSources:
    dataset: Path
    analytics: Path | None
    detection: Path | None
    impact: Path | None
    evidence: Path | None
    hashes: dict[str, str]


def _valid(report: object) -> bool:
    return bool(getattr(report, "valid", False))


def bind_sources(
    dataset_dir: str | Path,
    analytics_dir: str | Path | None = None,
    detection_dir: str | Path | None = None,
    impact_dir: str | Path | None = None,
    evidence_dir: str | Path | None = None,
) -> BoundSources:
    dataset = Path(dataset_dir).resolve()
    if not _valid(validate_dataset_directory(dataset)):
        raise BindingError("dataset artifact failed validation")
    if detection_dir is not None and analytics_dir is None:
        raise BindingError("detection artifact requires analytics artifact")
    analytics = Path(analytics_dir).resolve() if analytics_dir else None
    detection = Path(detection_dir).resolve() if detection_dir else None
    impact = Path(impact_dir).resolve() if impact_dir else None
    evidence = Path(evidence_dir).resolve() if evidence_dir else None
    _dataset_manifest, _ = load_dataset(dataset)
    hashes = {"dataset": file_sha256(dataset / "manifest.json")}
    if analytics:
        report: object = validate_analytics_directory(analytics, dataset_dir=dataset)
        if not _valid(report):
            raise BindingError("analytics artifact failed validation or dataset binding")
        hashes["analytics"] = file_sha256(analytics / "manifest.json")
    if detection:
        report = validate_detection_directory(detection, analytics_dir=analytics)
        if not _valid(report):
            raise BindingError("detection artifact failed validation or analytics binding")
        hashes["detection"] = file_sha256(detection / "manifest.json")
    if impact:
        report = validate_impact_directory(impact, dataset_dir=dataset)
        if not _valid(report):
            raise BindingError("impact artifact failed validation or dataset binding")
        hashes["impact"] = file_sha256(impact / "manifest.json")
    if evidence:
        report = validate_evidence_directory(
            evidence,
            dataset_dir=dataset,
            analytics_dir=analytics,
            detection_dir=detection,
            impact_dir=impact,
        )
        if not _valid(report):
            raise BindingError("evidence artifact failed validation or source binding")
        hashes["evidence"] = file_sha256(evidence / "manifest.json")
    return BoundSources(dataset, analytics, detection, impact, evidence, hashes)
