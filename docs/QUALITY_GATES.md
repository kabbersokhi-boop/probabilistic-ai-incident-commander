# Quality Gates

A change is ready for review only when the applicable checks below pass.

## Repository checks

```bash
python -m paic validate --spec-dir specs
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python -m pytest --cov=paic --cov-report=term-missing
```

Coverage must remain at or above the configured threshold. Tests, assertions, statistical assumptions, and safety gates must not be weakened simply to obtain a passing run.

## Generated schema check

```bash
rm -rf schemas-generated
python -m paic export-schemas --output-dir schemas-generated
diff -ru schemas schemas-generated
rm -rf schemas-generated
```

## End-to-end simulator check

```bash
rm -rf .artifacts/smoke
python -m paic simulate \
  --config configs/simulation/smoke.yaml \
  --output-dir .artifacts/smoke
python -m paic dataset validate --dataset-dir .artifacts/smoke
python -m paic dataset summary --dataset-dir .artifacts/smoke
```

The generated dataset must:

- contain every canonical table,
- contain zero incident injections,
- pass schema, key, relationship, temporal, financial, inventory, and baseline-health validation,
- reproduce identical logical tables for the same seed and configuration,
- produce a different population when the seed changes,
- detect manifest, configuration, row-count, or file-hash drift.

## Packaging check

```bash
python -m build
```

The resulting wheel must install in a clean Python 3.11 or 3.12 environment, and the installed `paic` CLI must validate contracts and generate the smoke dataset.
