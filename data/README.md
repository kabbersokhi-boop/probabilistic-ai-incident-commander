# Generated data

Synthetic datasets and analytical artifacts are generated locally and are not committed to the repository.

## Source dataset

```bash
paic simulate \
  --config configs/simulation/standard.yaml \
  --output-dir data/generated/standard
```

The source dataset contains compressed Parquet tables, the exact resolved simulation configuration, table metadata, SHA-256 hashes, runtime information, and a manifest. The baseline generator writes no incident ground truth or injected-failure labels into the dataset.

## Analytical artifact

```bash
paic analytics build \
  --dataset-dir data/generated/standard \
  --config configs/analytics/standard.yaml \
  --output-dir data/generated/analytics-standard
```

The analytical artifact contains:

- `metric_observations.parquet`,
- `funnel_observations.parquet`,
- `contribution_observations.parquet`,
- `data_quality_results.parquet`,
- the resolved analytics configuration,
- the exported metric catalog,
- source and runtime identity,
- table and file hashes,
- a manifest-bound `_SUCCESS` marker.

Validate both layers before using an artifact:

```bash
paic dataset validate --dataset-dir data/generated/standard
paic analytics validate \
  --analytics-dir data/generated/analytics-standard \
  --dataset-dir data/generated/standard
```
