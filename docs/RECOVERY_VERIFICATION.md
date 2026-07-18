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

## Observation authority

`paic recovery observations build` creates a separate closed-world observation
artifact. Baseline rows are selected from a validated analytics artifact; post-action
rows are generated deterministically from a strict evaluator scenario. They are
explicitly synthetic evaluator evidence, not production telemetry. The artifact binds
the analytics manifest and source lineage, execution receipt and manifest, incident,
execution timestamp, and generator-configuration digest. `evaluate` accepts this
validated directory rather than caller-authored rows or per-row hashes.

Only analytics rows strictly before the execution receipt timestamp are eligible
baseline evidence. Rows at or after execution are never copied into the
post-action window; post-action values come only from the resolved evaluator
scenario. The public observation validator requires both bound source
directories and reproduces the complete payload before reporting success.

Authoritative recovery validation requires all four artifacts:

```text
paic recovery validate --recovery-dir REPORT \
  --observations-dir OBSERVATIONS --analytics-dir ANALYTICS \
  --execution-dir EXECUTION
```

It checks the observation-manifest hash carried by the report, embedded
observation equality, analytics replay, execution identity and timestamp, and
the deterministic recovery replay. Structural checks are not a substitute for
source-authoritative validation.

The lifecycle's generation zero stores the resolved policy snapshot. Later immutable
generations contain state, report, and event; event hashes bind both state hashes,
the report, policy, transition, and previous event. Validation replays every
transition against that stored policy. This is local-filesystem integrity, not
distributed coordination or exactly-once delivery.

The general one-day analytics smoke profile remains unchanged. Phase 9 uses a
separate lightweight 14-day recovery source profile, so every recovery baseline is
genuinely derived from validated analytics windows. Its public CLI lifecycle proves
`insufficient_data`, `recovering`, sustained `recovered`, duplicate/stale rejection,
consecutive reopening, and isolated immediate severe reopening without fabricating
historical observations. The standard workflow uses the validated standard analytics
artifact and continues to label post-remediation rows as evaluator-generated
synthetic evidence rather than production telemetry.

## Trust and limitations

Before `current()` or `apply()` returns, the lifecycle store validates the
current generation and replays the committed lineage under its single lock.
Missing evidence emits `recovery_evidence_gap`; it does not increment the
regression counter or reopen an incident by default. Only observed failed or
qualifying degraded evaluations count toward consecutive reopening.

- Observation producers remain part of the trusted data boundary.
- Exactly-once lifecycle updates are scoped to one local filesystem store and its locking semantics.
- TOST and robust trend checks are evidence for synthetic recovery evaluation, not proof of production causality.
- Docker, hosted services, production identity, distributed coordination, and alert delivery remain later milestones.
