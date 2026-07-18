# Current Status

The repository provides deterministic contracts, synthetic commerce data, analytics, statistical anomaly detection, customer-impact modelling, operational evidence and lineage, a read-only Governed Tool Gateway, and probabilistic agentic investigation.

The investigation runtime can use an ordered NVIDIA NIM model route, call only governed read-only tools, propose competing hypotheses, seek contradictory evidence, and produce a source-bound report. Evidence identifiers are verified against actual tool results. Posterior ranking, entropy, confidence, and abstention are calculated outside the model and can be replayed without another API call.

CI uses deterministic scripted model responses and therefore requires no external credentials. Live NIM execution is an optional manual integration test using an environment-only key.

Not yet implemented: remediation execution, approval tokens, recovery verification, production credentials, persistent services, UI, Docker, or hosted infrastructure.
