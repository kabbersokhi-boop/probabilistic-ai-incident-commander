# Probabilistic AI Incident Commander

An evidence-grounded agentic system that will detect commerce anomalies, investigate them across business and operational data, rank root causes probabilistically, recommend governed remediation, and verify recovery.

This repository currently contains **Phase 0: the executable product and evaluation contract**. It establishes the rules every later implementation must satisfy before the simulator, statistical engine, agent, or frontend is added.

## Phase 0 contents

- Strict project, evaluation, safety, and incident contracts in YAML
- Five seed incidents with hidden ground truth, decoys, competing hypotheses, evidence expectations, remediation, and recovery rules
- A Pydantic contract model and cross-file validator
- JSON Schema generation for external tooling
- Tests and continuous integration
- Architecture, roadmap, security, and handoff documents

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
make check
```

Useful commands:

```bash
paic-contract validate --spec-dir specs
paic-contract summary --spec-dir specs
paic-contract export-schemas --output-dir schemas
pytest -q
```

## Why Phase 0 is executable

Project plans drift when they exist only as prose. The contracts in `specs/` are validated by code and CI. Later phases must update the contract intentionally rather than silently changing:

- the incident lifecycle,
- ground-truth benchmark requirements,
- evaluation metrics,
- SQL and approval boundaries,
- incident hypotheses and recovery criteria.

## Repository map

```text
specs/                  Machine-readable project contracts
src/paic/contracts/     Pydantic models, loader, and validator
schemas/                Generated JSON Schemas
tests/                  Contract and CLI tests
docs/                   Product, architecture, evaluation, and handoff documents
.github/                 CI and contribution templates
```

## Current status

Phase 0 is complete when all checks in `docs/PHASE_0_ACCEPTANCE.md` pass. The next development unit is **Phase 1: Synthetic Commerce Environment**.

## Safety position

The portfolio project is a simulation. The LLM will never receive unrestricted database credentials. Investigative SQL is read-only and policy-gated. Reversible risk-level 2 remediations require exact human approval. Risk-level 3 actions are blocked.
