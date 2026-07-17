# Handoff

## Phase 0 objective

Integrate the executable project contract into a new or existing GitHub repository without redesigning the approved scope.

## Integration detail

The initial integration was committed directly to `main` by explicit repository-owner instruction. The originally requested feature branch and draft pull request are therefore not applicable to this integration.

## Required commands

```bash
python -m pip install -e ".[dev]"
python -m paic validate --spec-dir specs
python -m paic summary --spec-dir specs
python -m pytest --cov=paic --cov-report=term-missing
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
rm -rf schemas-generated
python -m paic export-schemas --output-dir schemas-generated
diff -ru schemas schemas-generated
```

## Allowed repairs

- Packaging and import fixes
- Formatting, lint, typing, and test corrections
- CI compatibility corrections
- Documentation corrections that do not change the approved product contract

## Escalate instead of silently changing

- Workflow stages
- Evaluation metric definitions or hard gates
- Incident ground truth
- Safety and approval boundaries
- Statistical or causal claims
- The decision to use one principal agent

## Completion report

Record exact commands, outcomes, code changes, deviations, remaining limitations, branch, commit, pull request, and CI result.
