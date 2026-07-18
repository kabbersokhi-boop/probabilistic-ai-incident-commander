"""Shared anomaly-detection result types."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.analytics.manifest import AnalyticsManifest
from paic.detection.config import DetectionConfig
from paic.detection.manifest import DetectionManifest

DetectionFrameMap = dict[str, pl.DataFrame]


@dataclass(frozen=True)
class DetectionBuildResult:
    config: DetectionConfig
    source_manifest: AnalyticsManifest
    source_manifest_sha256: str
    tables: DetectionFrameMap


@dataclass(frozen=True)
class LoadedDetection:
    manifest: DetectionManifest
    tables: DetectionFrameMap
