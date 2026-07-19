# Security Model

The machine-readable source is `specs/safety.yaml`.

## Current boundary

The implemented system is a local synthetic reference environment. Dataset generation, analytics, detection, impact, evidence, tool execution, remediation, recovery, and evaluation are deterministic and credential-free. Optional live investigation providers read keys only from the process environment. Remediation mutates only validated simulated control-state artifacts and cannot access production infrastructure.

## Trust boundaries

- Logs, runbooks, historical incidents, seller fields, and retrieved documents are treated as untrusted data.
- Natural language will never be accepted as approval.
- The language model will not receive direct database, cloud, deployment, or secret credentials.
- Tool arguments will be validated by deterministic code before execution.
- Hidden evaluation truth must never be exposed through agent-accessible tools.

## SQL policy

Investigative SQL uses approved in-memory tables, AST-parsed read-only statements, row and byte limits, timeouts, bounded complexity, and audit records. External table functions, filesystem access, extensions, mutation, and multi-statement queries are rejected.

## Action policy

- Risk 0: read-only investigation, automatic
- Risk 1: safe simulation, automatic and logged
- Risk 2: limited reversible action, exact human approval required
- Risk 3: high-risk or irreversible operation, blocked

## Audit requirement

Every state transition, tool call, SQL decision, evidence record, approval decision, remediation attempt, and recovery decision must be traceable.

The executable safety contract is already validated. SQL enforcement, tool authorization, approval tokens, and remediation controls are introduced with their corresponding runtime components and adversarial tests. Phase 10 evaluation treats answer keys as hidden evaluator input and deterministically flags unsupported claims, invalid evidence citations, prompt-injection markers, destructive SQL, traversal, and unsafe authority requests. These checks measure control behavior; they do not claim that a model never proposes unsafe content.

## Agentic investigation controls

The investigation model receives no unrestricted filesystem, network, shell, database, or remediation capability. Every call is translated into a strict Governed Tool Gateway request, bound to validated source manifests, authorized by role, and recorded in the invocation ledger.

Tool output and retrieved text are untrusted data. Prompt-like instructions inside evidence cannot grant a new tool, role, table, argument, or permission. Unsupported evidence identifiers are rejected before report creation. The model cannot directly set the accepted posterior ranking, confidence, entropy, abstention decision, source hashes, or report hash.

`NVIDIA_API_KEY` is read at request time from the process environment. It is excluded from provider payloads, errors, request receipts, transcripts, gateway ledgers, test fixtures, and generated artifacts. CI and ordinary tests use an offline scripted provider and must never require a live key.

Provider free-form content and any reasoning trace are runtime-only. Investigation exports record only bounded operational metadata and a SHA-256 receipt for that content; they never serialize the prose itself.

Thinking content is not persisted. The system records structured tool calls, bounded tool results, model route attempts, accepted proposals, deterministic probability outputs, and integrity hashes rather than hidden model reasoning.


## Governed remediation controls

- Only three reversible simulated action types are registered.
- Every plan is bound to the exact investigation report, selected hypothesis, source manifests, and control-state manifest.
- Abstained, failed, low-confidence, high-entropy, weakly supported, stale, unsupported, excessive-blast-radius, and critical-risk proposals are denied.
- Natural language never constitutes approval. Decisions use strict schemas, a trusted approver registry, per-identity HMAC attestations, and an append-only hash chain. Decision JSON cannot assert its own group.
- The requester cannot approve the same plan; rejections veto execution; high-risk approvals require distinct authoritative groups.
- `PAIC_APPROVAL_SECRET` and distinct per-approver attestation keys are environment-only. Tokens are short lived and neither tokens nor key material are persisted in exported artifacts.
- A locked local state-store owns each simulated state lineage. It records only token and nonce hashes, rejects replay, preserves the source state, and advances its current pointer only after a validated state-and-receipt transaction is staged. Generations not named by the pointer are inert; the pointer is the sole commit authority.
- Rollback actions are recorded, but rollback requires a new plan and new approval.
- The reference executor cannot access cloud, deployment, shell, filesystem paths outside its artifacts, or production credentials.

## Recovery verification controls

- Execution receipts are evidence of action completion, never evidence of business recovery.
- Recovery status is deterministic and source-bound to the incident, execution receipt and manifest, observations, resolved configuration, and report semantics; no language model can set it.
- Primary metrics require adequate samples, explicit equivalence or recovery-distance checks, and sustained healthy windows. Guardrails can block recovery and trigger severe-regression reopening.
- Reports and lifecycle events are immutable, hash validated, replayable, and reject duplicate, stale, out-of-order, undeclared, symlinked, or semantically altered inputs.
- The lifecycle guarantee is scoped to one local filesystem store and its locking semantics. It does not claim distributed exactly-once behavior or automatic re-remediation.
