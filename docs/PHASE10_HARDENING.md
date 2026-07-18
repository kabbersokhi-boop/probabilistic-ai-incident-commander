# Phase 10 hardening overlay

This implementation repairs correctness and security gaps identified at PR #10 head
`cb862a5b5fb93735b00127facf0271cc3e4448c2`.

Key changes:

- rejects non-finite probabilities and duplicate identifiers;
- defines primary versus acceptable-alternate accuracy explicitly;
- computes multiclass Brier score over the true label even when omitted;
- adds clipped log loss, reliability bins, coverage, selective risk, tool failures,
  tool-budget failures, remediation/recovery correctness, and authority violations;
- separates source and effective benchmark hashes so real ablations remain paired;
- stores evaluator inputs in closed-world artifacts and performs source-bound semantic replay;
- uses durable staging, atomic rename, and overwrite restoration for local publication;
- requires exact ordered case IDs plus benchmark and answer-key lineage for comparisons;
- adds deterministic paired bootstrap confidence intervals and replayable comparison artifacts;
- makes the no-lineage ablation use different predictions rather than a metadata-only label;
- routes destructive SQL cases through the real AST SQL policy and path cases through a
  deterministic safe-path boundary; lexical text detection is explicitly supplemental.

Authoritative CLI replay, summary, and comparison require independent source inputs. The
artifact-only replay API is available solely for diagnostics and is not provenance validation.
After publication, a parent-directory fsync or backup-cleanup error raises a distinct
committed-but-undurable error; callers must not retry overwrite automatically.

Additional source bindings:

- provider identity and sanitized provider configuration are canonically hashed;
- case-level allowed tools plus the resolved tool-call budget are canonically hashed;
- provider token/cost/latency fields require explicit `provider_response` provenance;
- evidence ablations redact matching incident text and remove matching tool access, rather than
  only deleting an evidence identifier;
- the adversarial Make target also runs focused existing Phase 8 and Phase 9 failure-path tests
  for plan tampering, token/state replay, recovery semantic tampering, and source substitution.
