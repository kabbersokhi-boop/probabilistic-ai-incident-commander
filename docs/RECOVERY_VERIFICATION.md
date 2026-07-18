# Statistical Recovery Verification and Automatic Reopening

Phase 9 closes the incident lifecycle without giving the language model authority to declare success.

## Authority boundary

Remediation execution proves only that a governed action committed. Recovery is a separate deterministic decision over source-bound metric windows. The recovery engine does not call an LLM and cannot execute infrastructure actions.

## Recovery gates

Each configured series is either a primary recovery metric or a guardrail. A series is evaluated only after minimum baseline, post-execution, sample-size, and sustain-window requirements are met.

The verifier records:

- robust baseline median and MAD scale;
- a two-one-sided Welch equivalence test against an explicit recovery margin;
- the fraction of sustained observations inside the recovery band;
- robust deviation of the latest sustained window;
- Theil-Sen trend in adverse distance from baseline;
- improvement from the first post-action observation;
- severe guardrail breaches.

A recovered decision requires every primary metric and every configured guardrail to pass. Insufficient evidence is not treated as success.

## Automatic reopening

Recovery reports are applied to a locked local lifecycle store. The store uses immutable generations and an atomic current-generation pointer. A previously recovered incident reopens when either:

- a severe guardrail breach occurs and immediate reopening is enabled; or
- the configured number of consecutive non-recovered evaluations is reached.

A reopened incident remains reopened until a new governed incident cycle handles it. Phase 9 does not silently retry remediation.

## Artifact layout

```text
recovery-report/
├── _SUCCESS
├── manifest.json
├── recovery.config.resolved.json
├── observation-set.json
├── report.json
└── metric-evaluations.jsonl
```

Loading an artifact checks a closed-world layout, file hashes, success marker, source bindings, report hash, metric-table equality, and deterministic semantic replay.

## Trust and limitations

- Observation producers remain part of the trusted data boundary.
- Exactly-once lifecycle updates are scoped to one local filesystem store and its locking semantics.
- TOST and robust trend checks are evidence for synthetic recovery evaluation, not proof of production causality.
- Docker, hosted services, production identity, distributed coordination, and alert delivery remain later milestones.
