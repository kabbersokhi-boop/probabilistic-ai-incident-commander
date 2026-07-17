# Probabilistic AI Incident Commander

**Evidence-grounded, governed agentic AI for diagnosing commerce incidents under uncertainty.**

Probabilistic AI Incident Commander is an open reference implementation of an autonomous operations system that combines statistical anomaly detection, analytical investigation, probabilistic root-cause ranking, controlled remediation, and recovery verification.

The system is designed to answer more than *“What changed?”* It aims to determine:

- where the impact is concentrated,
- which explanations are supported or contradicted by evidence,
- how likely each root cause is,
- what customers and revenue are at risk,
- which remediation is permitted,
- and whether the business actually recovered.

```text
Detect -> Scope -> Investigate -> Test hypotheses -> Rank causes
       -> Request approval -> Remediate -> Verify recovery -> Report
```

## Why this project exists

Most AI operations demos stop at alert summarization or chart explanation. This project is being built to demonstrate a more rigorous pattern:

- **Statistics detect anomalies.** The language model does not guess from a chart.
- **The agent investigates.** It selects controlled tools, gathers evidence, and adapts its plan.
- **Probability expresses uncertainty.** Root-cause confidence is calculated and evaluated, not invented in prose.
- **Policies govern actions.** SQL access, permissions, approvals, and remediation boundaries are enforced by normal software.
- **Recovery is measured.** An incident is not resolved merely because an action completed successfully.

## Who it is for

The project is intended for engineers and technical teams interested in:

- agentic and applied AI systems,
- AI evaluation and observability,
- data and analytics engineering,
- statistical anomaly detection,
- incident response and site reliability,
- commerce, payments, fulfilment, and marketplace operations,
- safe human-in-the-loop automation.

It is also designed as a reproducible technical case study for evaluating how probabilistic models and language-model agents can cooperate inside a controlled software system.

## Target system

```text
Synthetic commerce activity
          |
          v
Metric and cohort calculations
          |
          v
Deterministic statistical detectors
          |
          v
Structured incident state
          |
          v
Incident Commander agent
          |
          +------ Safe SQL
          +------ Logs and service metrics
          +------ Deployments and configuration history
          +------ Data and service lineage
          +------ Historical incidents and runbooks
          +------ Statistical validation tools
          |
          v
Probabilistic root-cause ranking
          |
          v
Policy and human approval gate
          |
          v
Simulated remediation
          |
          v
Statistical recovery verification
          |
          v
Evidence-backed incident report
```

## Example investigation

A checkout conversion metric falls sharply for Android customers in one region shortly after several operational changes.

The completed system will:

1. detect that the decline is statistically unusual,
2. identify the affected geography, device, and application version,
3. inspect the conversion funnel to locate the failing step,
4. generate multiple falsifiable hypotheses,
5. query approved data through a read-only SQL gateway,
6. inspect deployments, logs, configuration changes, and lineage,
7. search for evidence that contradicts each explanation,
8. rank likely root causes probabilistically,
9. estimate affected customers, lost orders, churn exposure, and revenue risk,
10. request human approval for a reversible remediation,
11. apply the remediation in the simulation,
12. verify sustained recovery across primary and guardrail metrics,
13. generate an evidence-linked incident report.

## What is implemented today

The repository currently provides the executable foundation that future components must satisfy:

- machine-readable product, evaluation, safety, and incident contracts,
- five deterministic seed incidents with hidden ground truth,
- realistic decoy changes and competing hypotheses,
- expected supporting and contradictory evidence,
- remediation and recovery criteria,
- strict Pydantic models and cross-file validation,
- JSON Schema generation for external tooling,
- command-line validation and contract summaries,
- automated tests and continuous integration,
- architecture, security, evaluation, and development decision records.

The next capabilities being added are the synthetic commerce environment, analytical metric layer, and statistical anomaly-detection engine.

## Quick start

### Requirements

- Python 3.11 or newer
- `make` is optional; all checks can also be run with Python commands

### Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Validate the repository contracts

```bash
paic-contract validate --spec-dir specs
paic-contract summary --spec-dir specs
```

### Run the full quality suite

```bash
make check
```

Equivalent individual commands:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python -m pytest --cov=paic --cov-report=term-missing
```

### Export JSON Schemas

```bash
paic-contract export-schemas --output-dir schemas
```

## Repository map

```text
specs/                  Product, evaluation, safety, and incident contracts
src/paic/contracts/     Pydantic models, loaders, and cross-contract validation
schemas/                Generated JSON Schemas
tests/                  Contract, CLI, validation, and invariant tests
docs/                   Architecture, evaluation, security, and decision records
.github/                 Continuous integration and contribution templates
```

## Core design principles

### Deterministic detection

Anomaly detection, metric calculation, statistical testing, permissions, approval enforcement, and recovery verification are implemented in ordinary code. Language models are used for planning, hypothesis generation, tool selection, interpretation, and report generation.

### Evidence before conclusions

Every root-cause hypothesis must define expected observations, planned tests, supporting evidence, contradictory evidence, and a reason to accept, reject, or retain the hypothesis.

### Explicit uncertainty

The system will rank explanations probabilistically and measure calibration against hidden ground-truth incidents. A confidence value is useful only when its reliability is evaluated.

### Bounded autonomy

The agent may investigate autonomously through approved tools. It may recommend sensitive actions, but policy code decides whether an action is allowed, requires human approval, or is permanently blocked.

### Measured recovery

Successful execution is not the same as successful remediation. Recovery requires adequate sample size, sustained improvement, return toward an expected statistical range, and healthy guardrail metrics.

## Safety model

This project operates entirely on synthetic systems and simulated remediations.

The intended safety boundaries include:

- no unrestricted database credentials for the language model,
- read-only investigative SQL,
- parsed and policy-checked queries,
- approved schemas, row limits, timeouts, and audit logs,
- exact human approval for reversible sensitive actions,
- blocked high-risk actions,
- untrusted treatment of logs, runbooks, and retrieved text,
- explicit protection against prompt injection and fabricated evidence.

## Evaluation

The project is evaluation-first. Each synthetic incident has hidden ground truth so that system performance can be measured objectively.

Planned measurements include:

- anomaly-detection precision, recall, and detection delay,
- root-cause Top-1 and Top-3 accuracy,
- probability calibration,
- evidence quality and unsupported-claim rate,
- tool-call count, latency, and model cost,
- SQL safety and unauthorized-action block rates,
- remediation success and recovery-verification accuracy,
- churn, survival, and customer-impact model quality,
- ablations with lineage, history, contradiction search, and other components removed.

## Development status

This repository is under active development. Shipped capabilities are documented separately from planned capabilities, and the README will evolve as runnable simulation, statistical, agentic, and interface components are added.

The goal is a reproducible end-to-end demonstration that can be started locally, inject a hidden incident, show the investigation in real time, request approval, simulate remediation, verify recovery, and publish measured evaluation results.

## Contributing

Issues and pull requests are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing changes. New functionality should preserve deterministic tests, explicit contracts, documented assumptions, and measurable acceptance criteria.

## License

See [`LICENSE`](LICENSE) for licensing information.
