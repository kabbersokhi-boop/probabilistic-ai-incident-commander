# Expanded evaluation and adversarial testing

Phase 10 introduces a deterministic benchmark boundary. Visible incident inputs
and hidden answer keys are separate artifacts; the investigation runtime receives
only visible inputs, while scoring loads answer keys in the evaluator. The
benchmark grader is ordinary Python code and does not ask a language model to
grade itself.

The initial offline runner reports Top-1 accuracy, Top-3 recall, reciprocal
rank, Brier score, expected calibration error, abstention accuracy, selective
accuracy, required-evidence coverage, unsupported-claim counts, and tool-call
usage. Configuration records provider labels, a fixed seed, and ablation switches
without requiring a live provider. The adversarial suite covers prompt injection
in runbooks/history/provider text, fake operational instructions, destructive SQL,
path traversal, unsafe mutation authority, recovery-claim attempts, and compound
attacks. Artifact-level substitution and replay controls remain delegated to the
existing governed artifact and remediation/recovery validators.

Evaluation exports are closed-world, hash-bound, and replayable:

```sh
paic evaluation benchmark-validate --visible-dir configs/evaluation/smoke --answers-dir configs/evaluation/answers
paic evaluation run --visible-dir configs/evaluation/smoke --answers-dir configs/evaluation/answers --predictions configs/evaluation/smoke/predictions.json --config configs/evaluation/smoke/evaluation.json --output-dir .artifacts/evaluation-smoke
paic evaluation validate --run-dir .artifacts/evaluation-smoke
paic evaluation replay --run-dir .artifacts/evaluation-smoke
```

The fixtures are synthetic and prove evaluator integrity, not production model
quality, causality, latency, token usage, or cost. Optional live-provider
evaluation remains explicitly outside credential-free CI.

The standard offline benchmark contains 15 cases across checkout, payment,
latency, inventory, promotion, pipeline, regional, device, misleading-correlation,
multiple-cause, insufficient-evidence, and no-action-safe families. The
no-lineage ablation is executed against the same visible cases and deterministically
loses access to lineage evidence; the comparison reports its safety impact.
