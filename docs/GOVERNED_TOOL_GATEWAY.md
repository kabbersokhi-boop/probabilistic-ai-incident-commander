# Governed Tool Gateway

The Governed Tool Gateway is the only data-access boundary available to the investigation runtime. It is provider-neutral, deterministic, read-only, source-bound, and deny-by-default.

## Source binding

Every invocation starts with a validated synthetic dataset. Optional analytics, detection, customer-impact, and operational-evidence artifacts must validate and share the same source lineage. A detection artifact requires its analytics artifact, and an evidence artifact must be supplied with every optional source recorded in its manifest.

The response records sorted source-manifest hashes rather than filesystem locations. Invalid, incomplete, cross-dataset, or tampered artifacts are rejected before any handler executes.

## Tool catalogue

The versioned catalogue exposes:

- `sql.query`
- `evidence.search`
- `lineage.trace`
- `changes.list`
- `runbook.get`
- `historical_incidents.search`
- `anomalies.list`
- `impact.summary`
- `artifacts.summary`

Each tool has a strict Pydantic argument model with `extra="forbid"`. Unknown arguments, invalid limits, unsupported versions, unknown roles, and unknown tools fail closed. The `observer` role cannot use SQL; `investigator` and `approver` remain read-only.

## SQL boundary

SQL executes in an in-memory DuckDB connection containing only explicitly registered tables from validated artifacts. The runtime parses the query with `sqlglot` and accepts exactly one `SELECT`, set operation, or `WITH ... SELECT` expression.

The policy rejects:

- writes, DDL, transactions, grants, and administrative commands,
- multiple statements,
- catalog or schema-qualified access,
- table functions and dynamic query functions,
- filesystem, HTTP, object-storage, database-extension, and environment access,
- unknown tables, qualifiers, and columns,
- queries beyond configured complexity, memory, time, row, or byte limits.

DuckDB external access is disabled at runtime. Queries without an explicit ordering are canonically sorted before bounded serialization so repeated requests remain deterministic.

## Responses

Every response contains:

- deterministic call ID,
- incident, tool, and version,
- normalized arguments,
- policy and execution outcomes,
- source-manifest hashes,
- row and byte counts,
- truncation state,
- evidence-record references,
- canonical result hash,
- structured error information when rejected.

Result bodies are bounded before returning to callers.

## Audit ledger

When an audit directory is supplied, the gateway appends canonical JSONL receipts protected by a SHA-256 chain. Each record includes the redacted request, request hash, redacted response receipt, receipt hash, result hash, source hashes, previous-record hash, and final record hash.

Validation detects invalid JSON, edits, deletions, reordering, duplicate hashes or sequences, forged request or receipt hashes, and broken links. Filesystem paths and secret-like fields are removed or redacted. Full result bodies are not persisted in the ledger.

## Investigation integration

The probabilistic investigation runtime receives only catalogue schemas for configured tools. Model-supplied calls are normalized and authorized by this gateway; the language model cannot expand the catalogue, alter permissions, access arbitrary files or networks, or execute remediation.

Operational evidence and tool output are treated as untrusted data. Evidence roles indicate investigative relevance, not causality.

## CLI

```bash
paic tools list
paic tools invoke --request request.json
paic tools audit validate --audit-dir .artifacts/tool-audit
```

## Current boundary

The gateway is a local reference implementation over deterministic artifacts. It does not provide production identity, distributed locking, a network service, write tools, remediation, approval execution, or cloud persistence.
