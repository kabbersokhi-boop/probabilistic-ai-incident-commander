"""Example live investigation using NVIDIA NIM and validated local artifacts."""

from paic.investigation import (
    InvestigationRequest,
    Investigator,
    load_investigation_config,
)

config = load_investigation_config("configs/investigation/smoke.yaml")
request = InvestigationRequest(
    incident_id="checkout-address-validation-smoke",
    question="What most likely caused the checkout degradation, and what evidence contradicts it?",
    dataset_dir="data/generated/impact-source-smoke",
    impact_dir="data/generated/impact-smoke",
    evidence_dir="data/generated/evidence-smoke",
    audit_dir=".artifacts/investigation-tool-audit",
)
report, transcript = Investigator(config).run(request)
print(report.model_dump_json(indent=2))
print(f"transcript events: {len(transcript)}")
