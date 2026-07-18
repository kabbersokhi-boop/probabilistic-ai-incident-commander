"""Generate a bounded synthetic dataset and build customer-impact evidence."""

from pathlib import Path

from paic.impact.config import load_impact_config
from paic.impact.engine import build_impact
from paic.impact.io import export_impact
from paic.simulator.config import load_simulation_config
from paic.simulator.engine import simulate
from paic.simulator.io import export_dataset

root = Path("data/generated")
source_dir = root / "impact-source-smoke"
impact_dir = root / "impact-smoke"

simulation = simulate(load_simulation_config("configs/simulation/impact-smoke.yaml"))
export_dataset(simulation, source_dir, overwrite=True)

impact = build_impact(source_dir, load_impact_config("configs/impact/smoke.yaml"))
export_impact(impact, impact_dir, overwrite=True)

print(f"source dataset: {source_dir}")
print(f"impact artifact: {impact_dir}")
