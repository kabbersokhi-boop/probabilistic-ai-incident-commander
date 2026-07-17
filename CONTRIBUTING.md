# Contributing

## Development workflow

1. Create a feature branch for one phase or a narrowly scoped repair.
2. Preserve the machine-readable contracts unless the pull request explicitly changes the approved design.
3. Add or update tests for every behavioural change.
4. Run `make check` before requesting review.
5. Record architectural changes in `docs/DECISIONS.md`.
6. Update `docs/CURRENT_STATUS.md` and `docs/HANDOFF.md` before merge.

Do not weaken tests, security hard gates, or statistical assumptions merely to make a change pass.
