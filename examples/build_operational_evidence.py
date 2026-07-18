from pathlib import Path

from paic.evidence.config import load_evidence_config
from paic.evidence.engine import build_evidence
from paic.evidence.io import export_evidence

config = load_evidence_config("configs/evidence/smoke.yaml")
result = build_evidence("data/generated/impact-source-smoke", config)
export_evidence(result, Path("data/generated/evidence-smoke"), overwrite=True)
