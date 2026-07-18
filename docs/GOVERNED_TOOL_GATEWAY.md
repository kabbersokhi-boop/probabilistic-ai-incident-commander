# Governed Tool Gateway

The Governed Tool Gateway is a provider-neutral, read-only boundary for future investigation clients. It accepts strict JSON requests, validates every source artifact, checks dataset lineage, evaluates a deny-by-default policy, executes deterministic handlers, and returns a canonical JSON response.

The gateway never executes remediation, approvals, agent planning, network access, or extension loading. SQL is parsed with `sqlglot` and evaluated in an in-memory DuckDB connection over explicitly registered Polars tables. Results are bounded by row and byte limits.

Each invocation can append to `invocations.jsonl`. Records contain sequence and previous-record hashes, canonical request and result hashes, policy outcome, source manifest hashes, execution metadata, and a final record hash. Validation detects edits, deletion, reordering, duplicate sequence numbers, broken links, and forged hashes. Paths and source locations are not written to the ledger, and result bodies are intentionally excluded.

Evidence roles continue to describe investigative relevance, not proven causality. Causal outputs in the impact artifact remain synthetic benchmark diagnostics.
