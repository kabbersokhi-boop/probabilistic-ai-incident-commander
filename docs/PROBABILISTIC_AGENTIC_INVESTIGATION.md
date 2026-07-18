# Probabilistic Agentic Investigation

The investigation layer lets a language model plan a bounded, read-only incident investigation while ordinary software retains authority over data access, evidence validity, probability calculation, abstention, and artifact integrity.

## Responsibility split

The model may select approved tools, propose competing explanations, identify falsifiers, and describe unknowns. It may not read arbitrary files, access the network through tools, execute writes, approve remediation, fabricate evidence identifiers, set the final confidence value, or decide that an incident is resolved.

The runtime validates all source artifacts, invokes the Governed Tool Gateway, tracks evidence records actually observed, rejects unsupported citations, calculates posterior rankings from bounded likelihood ratios, applies abstention rules, and exports a replayable report.

## Model routing

The default NVIDIA NIM route is:

1. `nvidia/nemotron-3-super-120b-a12b` — primary investigation model
2. `qwen/qwen3.5-122b-a10b` — independent model-family fallback
3. `nvidia/nemotron-3-nano-30b-a3b` — fast final fallback

Retryable transport, rate-limit, and server failures advance to the next route. After a fallback succeeds, later tool rounds remain on that healthy route unless it fails. Fatal configuration failures stop safely. The API key is read from `NVIDIA_API_KEY` by default and is never persisted.

CI uses `ScriptedProvider`, which exercises the same orchestration, validation, probability, artifact, and replay paths without contacting an external model.

## Investigation loop

1. Validate and bind dataset, analytics, detection, impact, and evidence manifests.
2. Provide the model only the approved tool schemas and source hashes.
3. Permit one tool call per round.
4. Execute the call through the read-only gateway.
5. Return a bounded canonical result and collect evidence IDs.
6. Require at least two competing hypotheses.
7. Reject proposals that cite unseen evidence or invalid likelihood ratios.
8. Calculate normalized posterior rankings in deterministic code.
9. Conclude only when posterior, margin, evidence-count, and entropy thresholds pass; otherwise abstain.
10. Export a hash-bound report and transcript that can be validated and replayed without another model call.

## Probability calculation

For hypothesis \(h\), the runtime calculates

\[
\log s_h = \log P(h) + \sum_i \log LR_{h,i}
\]

and normalizes the scores with a stable softmax. Supporting evidence must have a likelihood ratio above one; contradictory evidence must be below one. Ratios are bounded by configuration, and proposal priors must sum to one.

This is an auditable evidence-weighting mechanism, not a claim that model-proposed likelihood ratios are automatically calibrated. Hidden-incident evaluation reports multiclass Brier score and ranking accuracy so future work can measure and improve calibration.

The runtime calculates normalized entropy and the margin between the two highest posteriors. It abstains unless all configured requirements pass. A report can therefore remain useful while explicitly declining to name a root cause.

## Prompt-injection boundary

Operational evidence, runbooks, historical incidents, SQL results, and tool errors are treated as untrusted data. The system prompt instructs the model not to execute instructions found inside tool output. More importantly, software controls the available tools, validates every argument, enforces source binding, and blocks all write paths. Prompt text cannot expand the tool catalogue or permissions.

## Artifacts

An exported investigation contains:

- resolved investigation configuration,
- a path-free request receipt,
- the computed report,
- a hash-chained transcript,
- a file manifest,
- a tamper-evident success marker.

Validation recomputes file hashes, transcript links, report probabilities, report hash, source-manifest bindings, and counts. Replay verifies and returns the report without accessing NVIDIA NIM.

The exported transcript contains only bounded provider operational metadata: model, finish reason, usage, validated tool calls, and a presence/byte-count/SHA-256 receipt for free-form content. It never persists provider `content`, reasoning traces, or unrestricted model prose. Exports are closed-world: the six documented regular files are the only permitted paths.

Benchmark cases use `investigation_dir`, not a standalone `report.json`. Each case is loaded through complete artifact validation and replay before scoring. The multiclass Brier metric uses the project's sum-style convention; a true hypothesis omitted from the report contributes its full `(0 - 1)^2` error term.

## CLI

```bash
paic investigate models --config configs/investigation/smoke.yaml
paic investigate run --request request.json --config configs/investigation/smoke.yaml --output-dir .artifacts/investigation
paic investigate validate --investigation-dir .artifacts/investigation --dataset-dir <dataset> --evidence-dir <evidence>
paic investigate replay --investigation-dir .artifacts/investigation
paic investigate benchmark --cases benchmark-cases.json
```

Each benchmark case contains `investigation_dir`, `true_hypothesis_id`, and `should_abstain`.

Use `--provider-script` for deterministic local and CI execution. A live NIM call is manual and requires the environment-only API key.

## Current limitations

- Likelihood ratios originate from model proposals and are bounded and evaluated, but not yet learned from historical calibration data.
- The default loop is synchronous and single-agent.
- Model endpoints can be rate-limited or unavailable, especially free hosted endpoints.
- No remediation, approval execution, or recovery declaration exists in this capability.
- A replay proves deterministic reconstruction of the accepted report, not that the original model reasoning was correct.
