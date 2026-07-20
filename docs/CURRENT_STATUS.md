# Current Status

Phases 0 through 10 are complete on `main`. Phase 11 is in progress and begins with a read-only terminal control room that presents existing validation and authoritative replay results without gaining operational authority.

The system remains a local, synthetic reference implementation. Language models may plan, select governed read-only tools, and propose hypotheses, but ordinary code owns probability, validation, authorization, mutation, recovery authority, and evaluation authority. The TUI is another read-only client of those ordinary-code boundaries.

CI is credential-free and runs Python 3.11 and 3.12 contracts, formatting, lint, strict typing, full tests with branch coverage, schema regeneration, smoke workflows, adversarial checks, and package build. Phase 11 adds deterministic TUI snapshots and workspace validation to that matrix. Live-provider evaluation remains optional and outside CI.

Phase 11 still requires endurance, interruption, corruption, terminal-compatibility, and shared artifact-publication hardening. Phase 12 will add Docker and production engineering. The public web product is deferred until those reliability gates pass. Synthetic benchmark results are evaluator evidence, not production performance claims.
