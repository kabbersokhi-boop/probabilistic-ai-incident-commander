# Security Model

The machine-readable source is `specs/safety.yaml`.

## Trust boundaries

- Logs, runbooks, historical incidents, seller fields, and retrieved documents are untrusted data.
- Natural language is never accepted as approval.
- The LLM does not receive direct database, cloud, deployment, or secret credentials.
- Tool arguments are validated by deterministic code before execution.

## SQL policy

Investigative SQL will use a read-only role, approved schemas, parsed statement types, row and result limits, timeouts, parameterized values, query-plan checks, cancellation, and audit records.

## Action policy

- Risk 0: read-only investigation, automatic
- Risk 1: safe simulation, automatic and logged
- Risk 2: limited reversible action, exact human approval required
- Risk 3: high-risk or irreversible operation, blocked

## Audit requirement

Every state transition, tool call, SQL decision, evidence record, approval decision, remediation attempt, and recovery decision must be traceable.

## Phase 0 limitation

Phase 0 defines and validates policy. Enforcement code is scheduled for Phase 6 and Phase 8.
