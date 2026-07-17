"""Top-level orchestration for deterministic commerce simulation."""

from __future__ import annotations

from paic.simulator.config import SimulationConfig
from paic.simulator.dimensions import generate_dimensions
from paic.simulator.events import generate_commerce_events
from paic.simulator.operations import generate_operational_data
from paic.simulator.randomness import RandomFactory
from paic.simulator.schema import TABLE_ORDER, conform_frame
from paic.simulator.types import SimulationResult


def simulate(config: SimulationConfig) -> SimulationResult:
    """Generate a complete incident-free synthetic commerce dataset."""

    random = RandomFactory(config.seed)
    dimensions = generate_dimensions(config, random)
    commerce = generate_commerce_events(config, random, dimensions)
    operations = generate_operational_data(config, random, dimensions, commerce)

    tables = {**dimensions, **commerce, **operations}
    missing = [table for table in TABLE_ORDER if table not in tables]
    if missing:
        raise RuntimeError(f"simulator omitted required tables: {', '.join(missing)}")
    ordered_tables = {table: conform_frame(table, tables[table]) for table in TABLE_ORDER}
    return SimulationResult(config=config, tables=ordered_tables)
