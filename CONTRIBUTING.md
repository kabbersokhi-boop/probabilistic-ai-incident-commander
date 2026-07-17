# Contributing

Contributions should keep the project reproducible, measurable, and safe.

## Development workflow

1. Create a focused feature branch or repair branch.
2. Preserve the machine-readable contracts unless the change explicitly updates an approved design.
3. Add or update tests for every behavioural change.
4. Run `make check` before requesting review.
5. Record material architectural changes in `docs/DECISIONS.md`.
6. Update `docs/CURRENT_STATUS.md` when public capabilities or limitations change.

## Quality expectations

- Do not weaken tests, coverage, security gates, or statistical assumptions simply to make a change pass.
- Keep generated datasets, secrets, virtual environments, caches, and build artifacts out of Git.
- Use fixed seeds in tests and examples.
- Document assumptions that affect statistical validity or business interpretation.
- Prefer a small, evaluated component over an unmeasured framework addition.

## Standard checks

```bash
make check
make simulate-smoke
make validate-smoke
```

See `docs/QUALITY_GATES.md` for the complete acceptance criteria.
