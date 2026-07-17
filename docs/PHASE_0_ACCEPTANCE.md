# Phase 0 Acceptance Criteria

Phase 0 is complete only when:

- [x] The repository installs cleanly on Python 3.11 or newer.
- [x] All YAML contracts pass strict model validation.
- [x] Cross-contract validation reports no errors or warnings.
- [x] Five seed incidents have unique IDs and seeds.
- [x] Every incident has at least four hypotheses, a decoy, supporting evidence, contradictory evidence, correct remediation, and recovery rules.
- [x] Required evaluation metrics and baselines are present.
- [x] SQL and action safety policies match the approved boundaries.
- [x] JSON Schemas are regenerated without a diff.
- [x] Unit tests and coverage gate pass.
- [x] Ruff formatting and linting pass.
- [x] Strict mypy passes.
- [x] GitHub Actions passes on the integration commit (Python 3.11 and 3.12).
- [x] `CURRENT_STATUS.md` and `HANDOFF.md` reflect the merged state.

No simulator, LLM integration, cloud resource, or production database is required in Phase 0.
