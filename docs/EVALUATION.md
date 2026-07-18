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
without requiring a live provider. The initial adversarial boundary treats
unsupported claims, invalid evidence citations, and hidden-answer substitution
as failures; later cases extend this corpus to prompt injection, path traversal,
symlinks, stale approvals, token replay, and forged recovery evidence.

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
