"""Shared types for customer-impact artifacts."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.impact.config import ImpactConfig
from paic.impact.manifest import ImpactManifest
from paic.simulator.manifest import DatasetManifest

ImpactFrameMap = dict[str, pl.DataFrame]


@dataclass(frozen=True)
class ImpactBuildResult:
    config: ImpactConfig
    source_manifest: DatasetManifest
    source_manifest_sha256: str
    tables: ImpactFrameMap


@dataclass(frozen=True)
class LoadedImpact:
    manifest: ImpactManifest
    tables: ImpactFrameMap
