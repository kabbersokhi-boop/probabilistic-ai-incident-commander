# Backup and restore

The validated static bundle is the release artifact. It can be backed up and restored with an integrity manifest:

```bash
make web-backup-restore
python scripts/static_artifact_ops.py backup --bundle .artifacts/web-bundle --archive .artifacts/web-bundle.tar.gz
python scripts/static_artifact_ops.py restore --archive .artifacts/web-bundle.tar.gz --output .artifacts/web-bundle-restored
```

The archive manifest records its SHA-256. Restore rejects a missing or mismatched manifest, path traversal, non-file archive members, and invalid bundle contents, and validates the restored bundle before making it visible. Demo data is regenerated from the committed smoke configuration, so it is reproducible rather than irreplaceable state.

For the static demo, the recovery-point objective is the latest validated bundle and the recovery-time objective is the time needed to regenerate or restore it in the hosting environment. A failed rollout is a rollback; a lost or corrupted artifact is a restore/regeneration. The clean-environment restore test is part of the exact-head web-readiness gate.
