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
