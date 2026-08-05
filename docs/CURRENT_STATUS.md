# Current Status

Phases 0 through 12 are complete on `main`. Phase 12.1 delivered a hardened,
credential-free container baseline for the existing governed command-line interface.
Phase 12.2 added deterministic image inventory, a CycloneDX 1.6 SBOM, exact image and
revision binding, and retained exact-head evidence. Phase 12.3 completed review-first
Python base refresh automation without weakening digest pinning or runtime boundaries.
Phase 12.4 completed hash-verified dependency locks, vulnerability policy, a deterministic
static web-readiness bundle, integrity-checked backup/restore, deployment/rollback policy,
observability definitions, resilience checks, and exact-head release gates.

The system remains a local, synthetic reference implementation. Language models may plan, select governed read-only tools, and propose hypotheses, but ordinary code owns probability, validation, authorization, mutation, recovery authority, and evaluation authority. Container packaging and supply-chain evidence do not broaden those boundaries.

CI is credential-free and runs Python 3.11 and 3.12 contracts, formatting, lint, strict
typing, full tests with branch coverage, schema regeneration, smoke workflows, adversarial
checks, package build, backup/restore, deployment rollback, and Phase 11 authoritative
soak certification. Phase 12 adds an exact-head image build, no-network/read-only/
non-root validation, interruption and resource-pressure checks, deterministic container
evidence, and a deterministic Python base-policy artifact retained for 14 days.

The Dockerfile exposes one direct digest-pinned Python 3.12 slim Bookworm base stage. A strict dependency-free validator rejects mutable or malformed references, wrong Python series or Debian variants, unexpected external stages, duplicate stages, and invalid policy. Dependabot proposes bounded weekly Docker updates, while major and minor Python-series movement is ignored so patch refreshes remain explicit review events.

The repository does not claim a hosted URL, a public OCI image signature, a runtime
service, a database, or production credentials. The `Release integrity evidence`
workflow has verified GitHub OIDC attestations for `bundle.json`, `manifest.json`, and
`SHA256SUMS` on the exact `main` commit. The final web phase is limited to an accessible
static read-only presentation and host verification over the validated bundle. Synthetic
benchmark results are evaluator evidence, not production performance claims.
