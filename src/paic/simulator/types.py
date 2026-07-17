"""Shared simulator result types."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from paic.simulator.config import SimulationConfig

FrameMap = dict[str, pl.DataFrame]


@dataclass(frozen=True)
class SimulationResult:
    """In-memory deterministic simulation output."""

    config: SimulationConfig
    tables: FrameMap

    def table(self, name: str) -> pl.DataFrame:
        try:
            return self.tables[name]
        except KeyError as exc:
            raise KeyError(f"unknown simulation table: {name}") from exc
