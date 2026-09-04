# Deployment foundation

The supported target is immutable static hosting of the validated web bundle. The reference
deployment is available at
`https://kabbersokhi-boop.github.io/probabilistic-ai-incident-commander/`. No deployment
credential is stored in the repository. The machine-validated policy is
`deployment/static-site-policy.json`, with host signal and alert
definitions in `deployment/observability.json`. Build, validation, backup, promotion, and
verification are separate steps. A deployment record must include the source commit, bundle
checksum, manifest, lock digest, provenance/attestation result, and validation result.

Validate the policy with `make deployment-validate`, then promote a validated directory
with `scripts/static_artifact_ops.py promote`. The operation validates the source and staged
copy and uses an atomic sibling-directory exchange on supported Linux hosts, retaining the
previous directory for rollback. Run `make deployment-rollback-smoke` before release. Do
not accept browser uploads, mutable image tags, unvalidated artifacts, or secrets in a
public bundle.

If a runtime read-only service is later proved necessary, it must expose only typed read routes with bounded payloads, pagination, timeouts, rate limits, cache policy, request IDs, security headers, health/readiness/version endpoints, and structured secret-free audit records. There is no database or migration requirement in the current static design.
