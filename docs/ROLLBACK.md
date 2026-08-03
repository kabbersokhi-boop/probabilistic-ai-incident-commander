# Rollback runbook

1. Stop promotion if bundle validation, checksum, smoke, accessibility, or host readiness fails.
2. Identify the deployed bundle checksum and source commit from the deployment record.
3. Select the previous validated bundle or restore it from its integrity-checked archive.
4. Run `scripts/static_artifact_ops.py validate` and the host smoke check.
5. Promote the validated previous directory atomically and record the new deployment record.
6. Investigate the failed commit before re-promoting it.

Rollback changes the hosted read-only artifact only. It never changes source artifacts, approvals, simulated control state, recovery state, evaluator truth, or infrastructure credentials. A future service deployment must test both readiness-gated rollback and backup restore separately.
