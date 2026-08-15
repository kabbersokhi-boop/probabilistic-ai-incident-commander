# Probabilistic AI Incident Commander

**An evidence-grounded incident-response system for investigating commerce failures under uncertainty, while deterministic software retains control of evidence, probability, approvals, execution, recovery, and evaluation.**

In the included scenario, a synthetic Indian marketplace declares a checkout incident in India South. PAIC scopes affected customers, inspects an address-validation rollout and a plausible payment-service decoy, compares competing causes, calculates their posterior probabilities, governs a simulated rollback, and verifies recovery from later metric windows.

The unusual part is the authority boundary: a model can choose bounded read-only tools and propose an explanation, but it cannot invent a valid citation, directly set the accepted probability, approve or execute a change, or declare that the business recovered.

[![CI](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/actions/workflows/ci.yml/badge.svg)](https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander/actions/workflows/ci.yml)

> **Synthetic reference environment.** No record represents a real customer or company, and every remediation changes local simulated state only. The dashboard is implemented, but no public deployment is currently verified; use the [local demo](#run-the-dashboard-locally).

![PAIC read-only incident dashboard showing lifecycle, diagnosis, governed execution, recovery, and measured impact](web/e2e/dashboard.spec.ts-snapshots/overview-desktop-linux.png)

## The incident you are investigating

The demo models an online marketplace with checkout, payments, inventory, fulfilment, seller-feed, and returns services. Its declared incident window is **16–17 January 2026**, scoped to **India South**.

Two changes make the investigation non-trivial:

- at 23:40 UTC, 20 minutes before the incident window, `address_validation.strict_mode` changes from `false` to `true` for India South and the corresponding feature flag is enabled;
- six hours earlier, a global payment retry timeout changes from 1200 ms to 1300 ms.

The configured evidence says the address-validation rollout aligns with the affected service, stage, region, and time. The payment change is retained as a competing explanation, but its scope and checkout stage do not align. The investigator searches the governed evidence store for both, then submits two structured hypotheses with evidence references, falsifiers, priors, and bounded likelihood ratios.

Ordinary Python code validates that every citation was actually observed, applies the configured likelihood-ratio bounds, normalizes the scores, and decides whether the evidence is strong enough to conclude or abstain. In the reproducible smoke artifact, equal 50% priors become:

| Hypothesis | Posterior | Evidence in the accepted report |
|---|---:|---|
| Primary checkout-service change regression | 99.47% | two supporting address-validation records |
| Unrelated downstream degradation | 0.53% | two contradicting records, including the payment decoy |

That produces 94.72% computed confidence after the entropy penalty. This is a result for one scripted synthetic investigation, not a production root-cause accuracy claim.

The rest of the recorded incident lifecycle is equally concrete:

- impact analysis finds **53 exposed synthetic customers** and **14 failed checkout interactions** in the declared incident window;
- deterministic policy allows one high-risk, single-service rollback only after **two approvals from distinct configured groups**;
- the simulated executor changes `service/checkout-address-validator` from revision `2026.07.18-bad` to `2026.07.17-good` and records an inverse action;
- recovery is not inferred from successful execution: four post-action observations for checkout conversion and the payment-approval guardrail are tested against their baselines, both pass sustained recovery criteria, and the incident is recorded as recovered.

The lifecycle engine can later reopen a recovered incident after a severe guardrail breach or the configured number of consecutive failed evaluations. It does not silently retry remediation.

## Follow the complete lifecycle

```text
Generated commerce and operational records
                    |
                    v
Build metrics and test for abnormal behaviour
                    |
                    v
Scope customers, cohorts, funnel stages, and impact
                    |
                    v
Search source-bound changes, health records, lineage, and runbooks
                    |
                    v
Propose competing hypotheses and actively retain falsifiers
                    |
                    v
Validate citations and calculate posterior probabilities
                    |
                    v
Apply deterministic remediation policy and exact approvals
                    |
                    v
Commit a reversible simulated state transition
                    |
                    v
Verify sustained recovery on primary and guardrail metrics
                    |
                    v
Score synthetic runs against evaluator-only ground truth
                    |
                    v
Export a sanitized bundle to the read-only React dashboard
```

## Where the data comes from

PAIC generates its own artificial commerce world. The impact profile used by the public bundle covers 40 days and contains 400 customers, 12 sellers, 120 products, four warehouses, six promotions, and 8,538 checkout sessions. It models four Indian regions, Android/iOS/web clients, acquisition channels, payment methods, issuers, carriers, orders, inventory, delivery scans, returns, refunds, service deployments, seller feeds, and analytical pipeline runs.

Reproducibility means more than calling a random-number generator with a seed. Generation is bound to a validated YAML configuration, package version, and top-level seed; independent random streams are namespaced by domain so a new draw in one subsystem does not reorder all others. Manifests retain schemas, primary and foreign keys, row counts, time bounds, dependency versions, and SHA-256 hashes for the generated Parquet tables.

### The benchmark boundaries matter

The repository deliberately separates three different ideas that are easy to blur in an AI demo:

1. **Baseline commerce data** models healthy normal variation. The simulator currently records no raw-event incident injection.
2. **Detector benchmarks** apply ten deterministic perturbations to a copy of metric observations. They do not rewrite the underlying commerce tables.
3. **Impact benchmarks** apply a known synthetic outcome effect in the analysis copy so estimators can be checked against both potential outcomes.

The public incident bundle combines validated smoke artifacts for metrics/detection, impact/evidence/investigation, remediation/recovery, and evaluation. Its one-day detection smoke input has insufficient history, so its two detector observations are correctly marked ineligible and it exports no anomaly event or change point. The **Detection** page therefore demonstrates explicit no-evidence handling; the separate standard detector benchmark demonstrates actual anomaly detection. The configured incident window, operational changes, investigation, impact projection, remediation, and recovery should not be misread as a single raw-event causal simulation.

This separation provides known ground truth, deterministic replay, safe adversarial testing, and measurable evaluation without customer privacy risk. It also makes the limitations inspectable instead of hiding them behind a persuasive narrative.

## What the AI does—and what it does not do

PAIC includes an OpenAI-compatible provider layer with NVIDIA NIM and Groq configurations, routing/fallback, budgets, and structured tool calls. The credential-free demo and CI use an offline scripted provider, so rebuilding the dashboard does not call an external model and the displayed run is not evidence of live-provider quality.

### Model-assisted investigation

When a live provider is used, the model can:

- choose one allowed investigative tool per round;
- search operational evidence, changes, anomalies, lineage, history, runbooks, impact summaries, or approved in-memory tables;
- construct multiple hypotheses with priors, supporting or contradicting evidence, bounded likelihood ratios, explicit unknowns, and falsifiers;
- recommend the next read-only check or submit a structured investigation proposal.

Provider prose and hidden reasoning are not persisted. Artifacts retain bounded model-attempt metadata, structured tool calls, accepted proposals, result hashes, and source bindings.

### Deterministic and statistical responsibilities

Regular code—not the model—owns:

- synthetic generation, schema validation, analytics, funnels, cohorts, and data-quality checks;
- distribution-aware anomaly scoring, false-discovery correction, CUSUM and sequential signals;
- customer exposure, survival, causal diagnostics, and financial-impact calculations;
- tool authorization, SQL parsing, source-lineage validation, limits, and audit receipts;
- citation validity, posterior normalization, entropy, confidence, and the conclude/abstain gate;
- remediation risk, exact approvals, token binding, replay protection, and state mutation;
- equivalence tests, sustain windows, guardrails, recovery, and lifecycle reopening;
- hidden-answer scoring, calibration, safety checks, semantic replay, and artifact validation.

### Outside model authority

The model has no unrestricted filesystem, network, shell, database, cloud, deployment, approval, or recovery capability. It cannot expand its tool catalogue, cite evidence that no successful tool call returned, bypass an abstention gate, turn natural language into approval, mutate production infrastructure, change evaluator answer keys, or convert an execution receipt into proof of recovery.

## What you see in the dashboard

The frontend is not a control plane. It is a static React/TypeScript reader over a versioned `paic-public-demo` bundle.

At build time, [`src/paic/web_readiness.py`](src/paic/web_readiness.py) validates every configured stage, excludes evaluator answer keys and secret/path-like fields, projects bounded tables for presentation, and emits `bundle.json`, `manifest.json`, and `SHA256SUMS`. [`web/scripts/prepare-bundle.mjs`](web/scripts/prepare-bundle.mjs) copies that closed-world export into the Vite public directory. The browser fetches only `data/bundle.json` and an optional deployment commit file; it has no backend, dynamic data source, account, analytics service, or provider connection.

Each route answers a question in the incident story:

| View | Question it answers |
|---|---|
| **Overview** | What is the recorded lifecycle state, leading diagnosis, policy outcome, execution state, recovery state, impact, and bundle integrity? |
| **Detection** | What did the statistical detector observe, and did it have enough history to make an anomaly decision? |
| **Investigation** | What explanations competed, what evidence or falsifiers belong to each, and how were they ranked? |
| **Evidence** | Which source-bound operational events form the timeline? |
| **Impact** | Which synthetic customers were exposed, which interactions failed, and what do the segment and causal diagnostics estimate? |
| **Remediation & Recovery** | What governed action was recorded, how many approvals were required, and did independent recovery tests pass? |
| **Evaluation** | How did the small synthetic benchmark score diagnosis, calibration, coverage, abstention, citations, and authority violations? |
| **System & Limitations** | Which commit and artifact are being displayed, and what authority is explicitly absent from the browser? |

Missing fields render as unavailable. Malformed or unsupported bundles produce an error state rather than substituted data.

## Why this is more than `Prompt -> LLM -> answer`

The hard problem is not producing plausible incident prose. It is deciding what may become authoritative.

**Evidence is a data product.** Generated datasets, derived metrics, detector outputs, impact estimates, evidence records, and investigation reports are separate closed-world artifacts. Their manifests bind source lineage, configuration, schemas, file sizes, and hashes. A tool response is rejected if its validated sources change during the call.

**Uncertainty is executable.** The model proposes hypotheses and evidence weights, but the probability module validates observed citations, constrains likelihood ratios, computes normalized posteriors and entropy, and abstains when thresholds are not met. Reports can be reconstructed from their proposal and trace.

**Authority is explicit.** The tool gateway is deny-by-default and read-only. Investigative SQL is parsed into an AST, limited to registered in-memory tables, and denied access to files, HTTP, extensions, catalogs, multi-statements, and mutations. Remediation has a separate policy and transaction path; the browser has none of it.

**Action success and business recovery are different facts.** A committed simulated rollback produces a receipt and inverse action. Only later source-bound observations can satisfy recovery equivalence, sample, trend, sustain, and guardrail rules.

**The evaluator is not the model.** Visible cases and hidden answers live in separate roots. Deterministic scoring, semantic replay, real ablations, and adversarial cases test unsupported citations, unsafe authority claims, prompt injection, destructive SQL, path traversal, and artifact substitution.

## Architecture and authority boundaries

```text
                         REASONING SIDE
 Optional live LLM ── structured tool calls / hypotheses / likelihood ratios
        |                                  |
        | no direct data credentials       v
        +----------------------- Governed Tool Gateway
                                      | read-only, parsed, bounded,
                                      | role-checked, source-bound, audited
                                      v
TRUTH SIDE                   Validated synthetic artifacts
──────────                   dataset -> analytics -> detection
                                      -> impact -> evidence
                                               |
                                               v
                              Deterministic probability + abstention
                                               |
                                               v
AUTHORITY SIDE               Remediation policy + exact approvals
──────────────                                |
                              simulated atomic state transition
                                               |
                              statistical recovery + reopening
                                               |
                              hidden-answer deterministic evaluation
                                               |
                                               v
PUBLIC SIDE                  sanitized hash-bound export
───────────                                |
                              static read-only React dashboard
```

The most important boundary is horizontal: model reasoning may influence a proposal, but only deterministic code can turn validated sources into an accepted conclusion, authorized simulated state transition, recovery decision, or evaluation result.

## Engineering depth

### Applied AI and agent orchestration

The investigation runtime has strict Pydantic message/tool schemas, ordered single-tool rounds, token/round/tool/response budgets, provider route classification and fallback, offline scripted replay, structured abstention, and transcript hashing. It preserves tool calls and integrity receipts instead of model chain-of-thought.

### Statistics and data

The analytics layer defines versioned metrics, cohorts, funnels, contribution analysis, and source-quality gates. Detection uses robust seasonal/rolling baselines, distribution-aware predictive tests, Benjamini–Hochberg correction, CUSUM, and sequential likelihood evidence. Impact analysis includes Kaplan–Meier curves, Cox modelling, propensity matching, stabilized weighting, difference-in-differences, placebo checks, balance diagnostics, and bootstrap intervals. Recovery uses robust baselines, two one-sided equivalence tests, Theil–Sen trends, sustain windows, and guardrails.

### Safety and governance

The read-only gateway enforces strict argument models, roles, source bindings, parsed SQL policy, time/row/byte/complexity limits, and a redacted SHA-256-chained ledger. Simulated remediation is limited to rollback, feature-flag, and configuration-restore actions; plans are bound to the exact investigation and control state. High-risk execution requires distinct trusted approver groups, per-identity attestations, a short-lived exact-plan token, one-time nonces, and local atomic generation pointers.

### Reproducibility and evaluation

Artifacts use resolved configs, manifests, checksums, success markers, closed-world validation, and deterministic replay. Evaluation separates visible cases from hidden answer keys and refuses mismatched benchmark lineage. The suite includes a 15-case standard offline benchmark, a no-lineage ablation, paired bootstrap comparison, and 12 adversarial boundary cases.

### Delivery and supply chain

GitHub Actions validates the exact pull-request head on Python 3.11 and 3.12. Committed dependency locks require hashes and freshness checks. The container runs as UID/GID `10001:10001` and is exercised with no network, a read-only root filesystem, dropped capabilities, `no-new-privileges`, and a bounded temporary filesystem. Separate workflows retain vulnerability evidence, validate a digest-pinned Python base policy, generate CycloneDX evidence, exercise backup/restore and rollback, run bounded resilience/soak checks, and create keyless provenance for release bundles.

### Web product and accessibility

The static React 19 application supports desktop and mobile layouts, system/light/dark themes, keyboard navigation, a skip link, semantic landmarks and tables, visible focus, reduced motion, print styles, and accessible chart alternatives. Vitest covers bundle parsing and pages; Playwright covers deep links, console errors, visual baselines, and automated axe accessibility checks. A compressed JavaScript budget and public-build secret/path scan run in CI.

## Synthetic evaluation results

The standard detector benchmark applies ten known perturbations to metric copies and reconstructs these results from the generated detector artifact:

| Detector measure | Synthetic result |
|---|---:|
| Scenarios | 10 |
| Scenario recall | 100% |
| Observation precision | 81.25% |
| False-positive rate | 0.24% |
| Mean detection delay | 1.2 periods |

The dashboard's separate smoke evaluator uses only three scripted cases: a checkout diagnosis, an insufficient-evidence case that should abstain, and a prompt-injection/approval-control case. Its checked-in predictions produce 100% Top-1 accuracy, a 0.175 Brier score, 66.67% coverage, 22.5% expected calibration error, zero unsupported claims, and zero authorized prohibited actions.

These numbers demonstrate deterministic scoring, abstention, and control integrity on small synthetic fixtures. They do **not** measure a live language model, real incident-response accuracy, causal validity in production, latency, cost, or production safety. See [anomaly detection](docs/ANOMALY_DETECTION.md) and [evaluation](docs/EVALUATION.md) for definitions and reproduction commands.

## Security and trust model

The design rule is simple:

> **AI may reason about evidence. It does not get to redefine evidence, create authority, or decide that its own action succeeded.**

This is enforced in code rather than left to the system prompt:

- retrieved text—including runbooks and historical incidents—is untrusted data;
- only successful governed tool calls add citable evidence IDs;
- SQL is parsed and executed only over registered in-memory read models;
- model-proposed probabilities are inputs to bounded deterministic calculation, never accepted output fields;
- natural-language approval is invalid, and requester self-approval is denied;
- remediation mutates only local validated control-state artifacts;
- execution and recovery are separate source-bound decisions;
- hidden answers are excluded from agent tools and the public bundle;
- the browser receives no secrets, arbitrary URLs, filesystem paths, or mutation interface.

The full boundary, including deliberate local-only and single-filesystem limitations, is in [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md).

## Run the dashboard locally

### Requirements

- Git
- Python 3.11 or 3.12
- Node.js 22.22.2 or newer and npm

```bash
git clone https://github.com/kabbersokhi-boop/probabilistic-ai-incident-commander.git
cd probabilistic-ai-incident-commander

python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .

make web-bundle web-validate

cd web
npm ci
npm run build
npm run preview
```

Open the URL printed by Vite, normally [`http://localhost:4173/`](http://localhost:4173/).

`make web-bundle` builds the complete credential-free smoke lifecycle before creating the public export; it does not contact an LLM provider. The frontend copies that validated bundle into its static build. For deployment details and hosting limitations, see [`docs/WEB_PRODUCT.md`](docs/WEB_PRODUCT.md).

## A guided reviewer walkthrough

Use the application in this order:

```text
Overview -> Detection -> Investigation -> Evidence -> Impact
         -> Remediation & Recovery -> Evaluation -> System & Limitations
```

As you move through it, answer:

1. Which facts are generated commerce data, configured operational context, evaluator perturbations, or simulated post-action observations?
2. Why does the Detection view decline to call its one-day smoke input anomalous?
3. Which two root-cause hypotheses compete, and which returned evidence moves their probabilities?
4. Who is counted as exposed, and which impact values are synthetic estimates rather than accounting facts?
5. Why does the high-risk rollback require two independent approvals?
6. Which tests establish recovery after execution?
7. What can the model propose, and what can only deterministic software authorize or declare?

For a shorter scripted tour, see [`docs/WEB_DEMO_SCRIPT.md`](docs/WEB_DEMO_SCRIPT.md).

## Repository guide

```text
src/paic/simulator/      Synthetic marketplace generation and validation
src/paic/analytics/      Metrics, cohorts, funnels, contributions, and quality
src/paic/detection/      Statistical detection and detector benchmarks
src/paic/impact/         Exposure, survival, causal, churn, and financial estimates
src/paic/evidence/       Operational facts, service health, lineage, and timelines
src/paic/tools/          Governed read-only tools, SQL policy, and audit ledger
src/paic/investigation/ Provider routing, orchestration, probability, and reports
src/paic/remediation/   Policy, approvals, replay protection, and simulated execution
src/paic/recovery/      Recovery tests and lifecycle reopening
src/paic/evaluation/    Hidden benchmarks, calibration, ablations, and attacks
src/paic/web_readiness.py  Sanitized deterministic public-bundle exporter
web/                    Static React/TypeScript dashboard
configs/                Reproducible runtime and benchmark profiles
specs/                  Product, incident, evaluation, and safety contracts
schemas/                Generated JSON Schemas for artifacts and requests
tests/                  Unit, integration, integrity, reliability, and adversarial tests
.github/workflows/      Exact-head quality, web, container, security, and release gates
```

## Current status and limitations

The Python incident lifecycle and read-only web interface are implemented on `main`. GitHub Pages deployment is configured, but repository metadata reports Pages disabled, the only Pages workflow run failed, and the expected site currently returns HTTP 404. No **Live Demo** link is published until a deployment is both successful and reachable.

Important scope boundaries remain:

- this is a local synthetic reference system, not a production incident-management service;
- the smoke dashboard is a composition of validated stage artifacts, not one causally coupled raw-event incident simulation;
- the displayed investigation uses a scripted provider; optional live providers require credentials and separate evaluation;
- impact methods adjust only for observed synthetic covariates, and their estimates are not business forecasts;
- approvals use a local registry and environment-held HMAC keys rather than SSO or a managed key service;
- state transaction and exactly-once guarantees are scoped to one local filesystem store;
- GitHub Pages cannot supply the security headers described by the deployment policy.

## Further technical reading

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — subsystem and artifact architecture
- [`docs/GOVERNED_TOOL_GATEWAY.md`](docs/GOVERNED_TOOL_GATEWAY.md) — tools, SQL, source binding, and audit
- [`docs/PROBABILISTIC_AGENTIC_INVESTIGATION.md`](docs/PROBABILISTIC_AGENTIC_INVESTIGATION.md) — provider and hypothesis workflow
- [`docs/CONTROLLED_REMEDIATION.md`](docs/CONTROLLED_REMEDIATION.md) — approval and simulated transaction model
- [`docs/RECOVERY_VERIFICATION.md`](docs/RECOVERY_VERIFICATION.md) — statistical recovery and reopening
- [`docs/CONTAINERS.md`](docs/CONTAINERS.md) and [`docs/PROVENANCE_AND_SIGNING.md`](docs/PROVENANCE_AND_SIGNING.md) — runtime and supply-chain controls
- [`docs/WEB_PRODUCT.md`](docs/WEB_PRODUCT.md) — bundle, browser, accessibility, and deployment

## License

Licensed under the [MIT License](LICENSE).
