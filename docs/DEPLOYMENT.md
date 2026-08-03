# Deployment foundation

The supported near-term target is static hosting of the validated web bundle. No public URL is claimed by this repository and no deployment credential is present in CI. Build, validation, backup, promotion, and verification are separate steps. A deployment record must include the source commit, bundle checksum, manifest, lock digest, and validation result.

Promote a validated directory with `scripts/static_artifact_ops.py promote`. The operation validates the source and staged copy before the target directory is switched, retaining the previous directory for rollback. Do not accept browser uploads, mutable image tags, unvalidated artifacts, or secrets in a public bundle.

If a runtime read-only service is later proved necessary, it must expose only typed read routes with bounded payloads, pagination, timeouts, rate limits, cache policy, request IDs, security headers, health/readiness/version endpoints, and structured secret-free audit records. There is no database or migration requirement in the current static design.
