# Current Status

Phase 10 expanded evaluation and adversarial testing is in progress. The initial
offline benchmark runner separates visible cases from hidden answer keys,
computes deterministic diagnosis, calibration, abstention, and safety metrics,
and exports closed-world replayable artifacts. Live provider evaluation is not
part of credential-free CI.

The repository provides deterministic contracts, synthetic commerce data, analytics, statistical anomaly detection, customer-impact modelling, operational evidence and lineage, a read-only Governed Tool Gateway, probabilistic agentic investigation, governed simulated remediation, and deterministic recovery verification with automatic reopening.

The investigation runtime uses provider-neutral OpenAI-compatible adapters, calls only governed read-only tools, proposes competing hypotheses, and produces a source-bound report. Probability, confidence, entropy, abstention, validation, and replay remain deterministic.

A validated concluded investigation can now feed an untrusted strict remediation proposal. Deterministic policy checks evidence support, confidence, state preconditions, action allowlists, blast radius, risk, and approval quorum. Decisions are hash chained and independently HMAC-attested by identities in a trusted local approver registry. A short-lived separate HMAC token authorizes atomic reversible mutation through a canonical control-state store. The source state remains immutable, unreachable prepared generations are inert, receipts are closed-world artifacts, and rollback requires a fresh approved plan.

CI remains credential-free. Provider live tests are optional. The remediation smoke generates ephemeral process-local signing material at runtime and never targets production infrastructure.

Recovery is decided by ordinary deterministic code from source-bound primary and guardrail observations. Execution success cannot declare recovery. Minimum sample sizes, equivalence margins, robust distance, sustained windows, adverse trends, severe breaches, immutable lifecycle generations, replay, and duplicate/stale report rejection are enforced. The local lifecycle store is atomic only within one locked filesystem lineage; it is not distributed coordination.

Not yet implemented: production identity and key management, persistent services, UI, Docker, or hosted infrastructure. Results remain synthetic deterministic evaluation results.
