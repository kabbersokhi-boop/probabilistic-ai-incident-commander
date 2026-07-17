# Generated data

Synthetic datasets are generated locally and are not committed to the repository.

```bash
paic simulate \
  --config configs/simulation/standard.yaml \
  --output-dir data/generated/standard
```

Each dataset contains compressed Parquet tables, the exact resolved configuration, table and file metadata, SHA-256 hashes, and a manifest. The current baseline generator writes no incident ground truth or injected-failure labels into the dataset.
