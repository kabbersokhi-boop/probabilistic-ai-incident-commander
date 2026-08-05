# Final web-product handoff

Stable contract: `schemas/web-readiness-bundle.schema.json`, implemented by `src/paic/web_readiness.py`, schema version `1.0`, bundle kind `paic-public-demo`.

Generate: `make web-bundle` (uses `configs/tui/smoke.yaml`). Validate: `make web-validate`. Sample output: `.artifacts/web-bundle/` after generation; it is ignored and must be regenerated, not committed. Backup/restore: `make web-backup-restore`.

Deployment policy: `deployment/static-site-policy.json`. Observability definitions:
`deployment/observability.json`. Validate both with `make deployment-validate`; exercise
the immutable promotion and rollback boundary with `make deployment-rollback-smoke`.

The deployment artifact is the closed-world directory containing `bundle.json`, `manifest.json`, and `SHA256SUMS`. Host it as immutable static content and expose its version/source metadata. The bundle is synthetic and read-only. Browser code receives no secrets, credentials, filesystem paths, unrestricted SQL, cloud/deployment authority, approval authority, remediation authority, recovery authority, or evaluator answer keys.

Remaining work is frontend-only: accessible responsive presentation, keyboard and screen-reader navigation, clear synthetic-data disclaimers, safe loading/error states, CSP/security headers at the host, bundle checksum verification in deployment, and static hosting verification. Do not add mutation routes or reimplement authoritative calculations in JavaScript.
