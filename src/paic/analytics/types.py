"""Shared analytical result types."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.analytics.config import AnalyticsConfig
from paic.analytics.manifest import AnalyticsManifest
from paic.simulator.manifest import DatasetManifest

AnalyticsFrameMap = dict[str, pl.DataFrame]
FactMap = dict[str, pl.DataFrame]


@dataclass(frozen=True)
class AnalyticsBuildResult:
    """In-memory output of the deterministic analytics engine."""

    config: AnalyticsConfig
    source_manifest: DatasetManifest
    source_manifest_sha256: str
    facts: FactMap
    tables: AnalyticsFrameMap


@dataclass(frozen=True)
class LoadedAnalytics:
    """Validated analytics artifact loaded from disk."""

    manifest: AnalyticsManifest
    tables: AnalyticsFrameMap
