# Security Model

The machine-readable source is `specs/safety.yaml`.

## Current boundary

The implemented simulator is local, deterministic, incident-free, and requires no secrets or external credentials. Generated datasets contain synthetic identifiers and fictional entities only.

## Trust boundaries

- Logs, runbooks, historical incidents, seller fields, and retrieved documents will be treated as untrusted data.
- Natural language will never be accepted as approval.
- The language model will not receive direct database, cloud, deployment, or secret credentials.
- Tool arguments will be validated by deterministic code before execution.
- Hidden evaluation truth must never be exposed through agent-accessible tools.

## SQL policy

Investigative SQL will use a read-only role, approved schemas, parsed statement types, row and result limits, timeouts, parameterized values, query-plan checks, cancellation, and audit records.

## Action policy

- Risk 0: read-only investigation, automatic
- Risk 1: safe simulation, automatic and logged
- Risk 2: limited reversible action, exact human approval required
- Risk 3: high-risk or irreversible operation, blocked

## Audit requirement

Every state transition, tool call, SQL decision, evidence record, approval decision, remediation attempt, and recovery decision must be traceable.

The executable safety contract is already validated. SQL enforcement, tool authorization, approval tokens, and remediation controls are introduced with their corresponding runtime components and adversarial tests.

## Agentic investigation controls

The investigation model receives no unrestricted filesystem, network, shell, database, or remediation capability. Every call is translated into a strict Governed Tool Gateway request, bound to validated source manifests, authorized by role, and recorded in the invocation ledger.

Tool output and retrieved text are untrusted data. Prompt-like instructions inside evidence cannot grant a new tool, role, table, argument, or permission. Unsupported evidence identifiers are rejected before report creation. The model cannot directly set the accepted posterior ranking, confidence, entropy, abstention decision, source hashes, or report hash.

`NVIDIA_API_KEY` is read at request time from the process environment. It is excluded from provider payloads, errors, request receipts, transcripts, gateway ledgers, test fixtures, and generated artifacts. CI and ordinary tests use an offline scripted provider and must never require a live key.

Thinking content is not persisted. The system records structured tool calls, bounded tool results, model route attempts, accepted proposals, deterministic probability outputs, and integrity hashes rather than hidden model reasoning.
