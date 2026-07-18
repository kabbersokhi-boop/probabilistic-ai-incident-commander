"""Types for operational evidence artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.evidence.config import EvidenceConfig
from paic.evidence.manifest import EvidenceManifest
from paic.simulator.manifest import DatasetManifest

EvidenceFrameMap = dict[str, pl.DataFrame]


@dataclass(frozen=True)
class EvidenceBuildResult:
    config: EvidenceConfig
    source_dataset_manifest: DatasetManifest
    source_dataset_manifest_sha256: str
    source_analytics_manifest_sha256: str | None
    source_detection_manifest_sha256: str | None
    source_impact_manifest_sha256: str | None
    tables: EvidenceFrameMap


@dataclass(frozen=True)
class LoadedEvidence:
    manifest: EvidenceManifest
    tables: EvidenceFrameMap
