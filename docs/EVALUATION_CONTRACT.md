# Evaluation Contract

The machine-readable source is `specs/evaluation.yaml`.

## Benchmark rules

- At least 15 hidden incidents before the evaluation release
- At least 3 polished demonstration incidents
- At least 5 incident families
- Fixed random seeds and reproducible ground truth
- At least 4 candidate hypotheses per incident
- At least one plausible decoy change per incident
- Supporting and contradictory evidence must be defined before agent execution

## Measurement families

### Detection

Precision, recall, false-positive rate, and time to detect.

### Diagnosis

Top-1 and Top-3 root-cause accuracy, time to diagnosis, evidence precision, and unsupported-claim rate.

### Probability

Brier score and expected calibration error. The LLM cannot invent confidence values.

### Operations and security

Approval compliance, remediation success, recovery accuracy, unsafe-action block rate, and SQL safety block rate.

### Efficiency

Tool calls, SQL cost, latency, and model cost.

### Customer impact

Churn PR-AUC, churn Brier score, survival concordance, and incident-attributable revenue-impact error.

## Hard gates

The following eventually require 100 percent performance in the synthetic benchmark:

- destructive or out-of-policy SQL is blocked,
- unauthorized or prohibited actions are blocked,
- approval-gated actions execute only with valid exact-target approval.

These are policy requirements, not current measured results.

## Baselines and ablations

The project will compare threshold-only detection, a detector without an agent, a fixed investigation workflow, and agents without lineage, historical incidents, or contradiction search.

## Reporting rule

No README or resume claim may report a result until a reproducible benchmark command produces it from a recorded code, model, prompt, dataset, and incident version.
