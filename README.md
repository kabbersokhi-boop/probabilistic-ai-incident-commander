# Probabilistic AI Incident Commander

## Public dashboard

The read-only public incident command dashboard lives in [`web/`](web/). It presents a deterministic synthetic incident bundle from detection through governed, simulated remediation and recovery verification. It does not control a production system, execute remediation, accept approvals, or make production-performance claims.

```bash
make web-bundle
make web-validate
cd web && npm ci && npm run build && npm run preview
```

It supports current evergreen desktop and mobile browsers, system/dark/light themes, keyboard navigation, reduced motion, and print. See [web-product architecture and deployment](docs/WEB_PRODUCT.md) and the [guided demo](docs/WEB_DEMO_SCRIPT.md).

[![CI](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/actions/workflows/ci.yml/badge.svg)](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/actions/workflows/ci.yml)

**Evidence-grounded, governed agentic AI for diagnosing commerce incidents under uncertainty.**

Probabilistic AI Incident Commander is a public, open-source reference implementation of an AI-assisted incident-response system. It combines deterministic analytics, statistical anomaly detection, governed investigation, probabilistic root-cause ranking, controlled simulated remediation, recovery verification, and reproducible evaluation.

The repository is public today. A hosted, read-only web product is the active delivery target tracked in [issue #29](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/issues/29).

> **Current boundary:** the implemented system operates on synthetic data and simulated remediations. It does not possess production infrastructure credentials or unrestricted mutation authority.

## What it does

```text
Detect -> Scope -> Investigate -> Test hypotheses -> Rank causes
       -> Request approval -> Simulate remediation -> Verify recovery -> Report
```

The system can:

- detect statistically unusual changes without asking a language model to guess from charts;
- identify affected cohorts, funnel steps, services, and operational changes;
- gather evidence through a deny-by-default, read-only tool gateway;
- maintain competing hypotheses and search for contradictory evidence;
- calculate and evaluate root-cause probabilities outside the language model;
- estimate affected customers, lost orders, churn exposure, and revenue risk;
- enforce exact human approval for reversible simulated actions;
- verify sustained recovery across primary and guardrail metrics;
- replay and evaluate the complete incident lifecycle against hidden ground truth.

## Current status

Phases 0 through 12 production engineering are complete on `main`.

Completed production-engineering units include:

- a reproducible, non-root, no-network, read-only container baseline;
- deterministic image inventory and CycloneDX supply-chain evidence;
- exact image and commit binding with retained CI artifacts;
- a direct digest-pinned Python base stage;
- strict base-policy validation and bounded review-first Dependabot updates;
- a versioned, deterministic web-readiness bundle with deployment, observability,
  backup, rollback, and release-integrity policies;
- exact-head resilience checks for interruption, resource pressure, restart bounds, and
  concurrent reads.

The only major product work left is the final accessible public web interface and its
verified static hosting. The release-integrity workflow has produced and verified GitHub
OIDC attestations for the validated static bundle on `main`; this repository does not
claim a hosted URL, a public OCI image signature, or a runtime API.

See:

- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md)
- [`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md)
- [Public web product readiness issue #29](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/issues/29)

## Why this project is different

- **Statistics detect anomalies.** Language models do not invent whether a chart is unusual.
- **Evidence precedes conclusions.** Every hypothesis has expected observations, tests, supporting evidence, and falsifiers.
- **Probability is measured.** Confidence is calculated and evaluated for calibration.
- **Authority stays in ordinary code.** SQL policy, permissions, approvals, mutation rules, recovery, and evaluation are deterministic.
- **Recovery is verified.** Successful execution is not treated as proof that an incident is resolved.
- **Benchmarks use hidden ground truth.** Results come from reproducible scenarios rather than one selected demo.

## Implemented capabilities

1. Executable product, safety, evaluation, and incident contracts.
2. Deterministic synthetic commerce and operational datasets.
3. Versioned analytics, cohorts, funnels, contributions, and data-quality checks.
4. Statistical anomaly and change detection with false-discovery control.
5. Customer impact, survival, causal, churn, and financial-risk analysis.
6. Source-bound operational evidence, lineage, changes, and runbooks.
7. A governed read-only SQL and tool gateway with hash-chained audit records.
8. Provider-neutral, bounded agentic investigation with deterministic posterior ranking.
9. Governed simulated remediation with exact human approval and replay protection.
10. Statistical recovery verification and automatic reopening after regression.
11. Hidden-benchmark evaluation, ablations, adversarial testing, and semantic replay.
12. A read-only terminal control room with deterministic snapshots and reliability testing.
13. Hardened container packaging and deterministic supply-chain evidence.

## Quick start

### Requirements

- Python 3.11 or newer
- `make` is optional
- Docker is optional for the hardened container path

### Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
```

### Validate the project

```bash
paic validate --spec-dir specs
paic summary --spec-dir specs
```

### Generate and inspect a smoke dataset

```bash
paic simulate \
  --config configs/simulation/smoke.yaml \
  --output-dir data/generated/smoke

paic dataset validate --dataset-dir data/generated/smoke
paic dataset summary --dataset-dir data/generated/smoke
```

### Build analytics and detection artifacts

```bash
paic analytics build \
  --dataset-dir data/generated/smoke \
  --config configs/analytics/smoke.yaml \
  --output-dir data/generated/analytics-smoke

paic detection build \
  --analytics-dir data/generated/analytics-smoke \
  --config configs/detection/smoke.yaml \
  --output-dir data/generated/detection-smoke
```

### Run the terminal control room

```bash
paic-tui --workspace .
```

### Build the hardened container

```bash
docker build \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  --build-arg VERSION=0.12.0 \
  --tag paic:local \
  .
```

Run credential-free validation under the hardened boundary:

```bash
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  paic:local validate --spec-dir /opt/paic/specs
```

Container details are documented in [`docs/CONTAINERS.md`](docs/CONTAINERS.md).

## Quality gates

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python -m pytest --cov=paic --cov-report=term-missing
make locks-validate locks-freshness
make web-bundle web-validate web-backup-restore
make deployment-validate deployment-rollback-smoke
```

CI also runs exact-head container resilience, deterministic evidence generation,
vulnerability and public-artifact policy, deployment/rollback policy, package builds,
backup/restore, release-attestation verification, adversarial checks, and authoritative
soak certification across Python 3.11 and 3.12.

## Architecture

```text
Synthetic commerce and operational data
                  |
                  v
Deterministic analytics and statistical detection
                  |
                  v
Source-bound incident state and operational evidence
                  |
                  v
Governed Tool Gateway <-> Bounded investigation agent
                  |
                  v
Deterministic probability and policy gates
                  |
                  v
Human approval -> simulated remediation
                  |
                  v
Statistical recovery verification and evaluation
                  |
                  v
                  Read-only terminal control room / final public web product
```

## Public web product plan

The hosted product will be a **read-only presentation and demonstration layer** over validated synthetic artifacts and authoritative replay functions. It will provide:

- incident overview and lifecycle status;
- anomaly, cohort, funnel, and impact views;
- evidence timelines and source lineage;
- competing hypotheses and probability ranking;
- governed remediation and approval state;
- recovery and evaluator results;
- accessible keyboard and screen-reader navigation;
- deterministic demo data requiring no provider credentials.

The browser will not receive production credentials, unrestricted SQL, shell access, approval authority, remediation authority, recovery authority, or evaluator authority.

The contract is documented in [`docs/WEB_READINESS_CONTRACT.md`](docs/WEB_READINESS_CONTRACT.md), with handoff details in [`docs/HANDOFF_WEB_PRODUCT.md`](docs/HANDOFF_WEB_PRODUCT.md). The bundle is generated from `configs/tui/smoke.yaml` and validated before it is suitable for hosting.

## Evaluation results

The standard deterministic detector benchmark currently reports:

| Measure | Result |
|---|---:|
| Injected scenarios | 10 |
| Scenario recall | 100% |
| Observation precision | 81.25% |
| False-positive rate | 0.24% |
| Mean detection delay | 1.2 periods |

These are synthetic evaluator results demonstrating reproducibility and control integrity, not production performance claims.

## Repository map

```text
configs/                Reproducible simulation, analytics, detection, and impact configurations
specs/                  Product, evaluation, safety, and incident contracts
src/paic/contracts/     Contract models and validation
src/paic/simulator/     Synthetic commerce generation and validation
src/paic/analytics/     Metrics, cohorts, funnels, contributions, and quality
src/paic/detection/     Statistical detection and benchmarks
src/paic/impact/        Customer and financial impact analysis
src/paic/evidence/      Operational evidence, lineage, and timelines
src/paic/tools/         Governed tools, SQL policy, and audit ledger
src/paic/investigation/ Agent orchestration, probability, and reports
src/paic/remediation/   Policy, approval, tokens, and simulated execution
src/paic/recovery/      Recovery verification and lifecycle reopening
src/paic/evaluation/    Hidden benchmarks, ablations, replay, and attacks
src/paic/tui/           Read-only terminal control room
schemas/                Generated JSON Schemas
tests/                  Unit, integrity, reliability, and adversarial tests
docs/                   Architecture, security, operations, and decisions
.github/                 CI workflows and contribution templates
```

## Security and authority model

The project operates entirely on synthetic systems and simulated remediations. Important boundaries include:

- no unrestricted credentials for the language model;
- read-only investigative SQL with parsed policy checks;
- approved schemas, limits, timeouts, and audit records;
- exact human approval for reversible sensitive actions;
- blocked high-risk actions;
- untrusted treatment of logs, runbooks, and retrieved text;
- explicit prompt-injection and fabricated-evidence defenses;
- deterministic recovery and evaluation authority outside the model.

See [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md).

## Contributing

Issues and pull requests are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing changes. New functionality must preserve deterministic tests, explicit contracts, documented assumptions, measurable acceptance criteria, and the existing authority boundaries.

## License

This project is available under the [MIT License](LICENSE).
