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

## End-to-end source dataset check

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

## End-to-end analytical check

```bash
rm -rf .artifacts/analytics-smoke
python -m paic analytics build \
  --dataset-dir .artifacts/smoke \
  --config configs/analytics/smoke.yaml \
  --output-dir .artifacts/analytics-smoke
python -m paic analytics validate \
  --analytics-dir .artifacts/analytics-smoke \
  --dataset-dir .artifacts/smoke
python -m paic analytics summary \
  --analytics-dir .artifacts/analytics-smoke
```

The analytical artifact must:

- publish only registered metrics from supported source facts and cohorts,
- preserve value, numerator, denominator, sample size, and quality status,
- maintain unique analytical keys and one-row-per-entity fact cardinality,
- independently reconcile metric arithmetic and supported cohort totals,
- preserve funnel monotonicity and drop-off arithmetic,
- reconstruct adjacent-period metric changes from rate and mix effects,
- contain no non-finite values or observations marked invalid,
- bind the artifact to the exact source manifest, analytics configuration, metric catalog, and table bytes,
- reproduce identical logical and file-level outputs for identical inputs within the same runtime,
- detect path traversal and configuration, catalog, manifest, marker, metadata, or table tampering.

The standard configuration must also be run before merging changes that affect generation, joins, metric definitions, contribution analysis, or artifact export.

## End-to-end statistical detection check

```bash
rm -rf .artifacts/detection-smoke
python -m paic detection build \
  --analytics-dir .artifacts/analytics-smoke \
  --config configs/detection/smoke.yaml \
  --output-dir .artifacts/detection-smoke
python -m paic detection validate \
  --detection-dir .artifacts/detection-smoke \
  --analytics-dir .artifacts/analytics-smoke
python -m paic detection summary \
  --detection-dir .artifacts/detection-smoke
```

The detector must:

- use only observations earlier than the point being scored,
- preserve expected values, uncertainty intervals, p-values, q-values, change scores, and eligibility evidence,
- choose a distribution-aware predictive test from metric semantics,
- apply monotone Benjamini-Hochberg correction with `q >= p`,
- block alerts with inadequate history, samples, effect size, or detector support,
- keep a no-anomaly smoke build free of alerts,
- detect all ten standard evaluator scenarios,
- retain standard benchmark precision of at least 0.80 and false-positive rate below 0.005,
- reproduce identical logical tables and hashes from identical inputs in the same runtime,
- detect source-lineage, configuration, manifest, marker, schema, path, and table tampering.

## Independent detection audit

Review must independently:

- verify a ratio score against the beta-binomial predictive calculation,
- verify an overdispersed count selects the negative-binomial model,
- confirm small-cohort beta-binomial results are less overconfident than a fixed-probability binomial test,
- recompute one Benjamini-Hochberg family and confirm monotonic q-values,
- mutate a current or future observation and prove it does not affect an earlier baseline,
- run the standard benchmark twice and compare outputs.

## Independent analytical audit

At least representative metrics must be recomputed directly from source tables rather than validated only through the analytical engine. The review set currently includes:

- checkout conversion,
- payment approval,
- gross order value,
- average order value,
- inventory exact-match rate,
- pipeline row-retention rate.

For cohort changes, the review must verify that cohort numerators and denominators reconstruct the overall observation. For contribution changes, every period pair must reconstruct the observed overall change within numerical tolerance.

## Packaging check

```bash
python -m build
```

The resulting wheel must install in a clean Python 3.11 or 3.12 environment. The installed `paic` CLI must validate contracts, generate and validate the smoke dataset, build and validate its analytical artifact, and build and validate the smoke detector.

## Customer-impact quality gates

- Source dataset validation passes and covers the complete pre-incident and follow-up windows.
- Exposed and control cohorts are both non-empty; the standard benchmark has at least 20 customers in each.
- Survival and propensity optimizers converge.
- Durations, outcomes, monetary fields, and primary keys are valid.
- Weighted covariate balance is reported and the maximum absolute standardized mean difference is no greater than 0.25 for the standard benchmark.
- The main estimate lies inside its bootstrap interval.
- The known synthetic ATT lies inside the evaluated interval.
- The shifted-window placebo effect is smaller than the main effect.
- Financial components exactly reconstruct total impact.
- Repeated builds are deterministic within the same runtime.
- Manifest, source binding, configuration, marker, schema, row count, and table hashes are validated.
