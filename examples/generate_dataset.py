"""Generate the smoke synthetic commerce dataset from Python."""

from pathlib import Path

from paic.simulator.config import load_simulation_config
from paic.simulator.engine import simulate
from paic.simulator.io import export_dataset

config = load_simulation_config(Path("configs/simulation/smoke.yaml"))
result = simulate(config)
manifest = export_dataset(result, Path("data/generated/example"), overwrite=True)
print(manifest.model_dump_json(indent=2))
