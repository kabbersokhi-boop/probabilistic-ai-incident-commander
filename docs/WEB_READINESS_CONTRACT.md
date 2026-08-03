# Web-readiness contract

The repository produces a static, read-only synthetic demonstration bundle. It is deliberately not a web server or UI. The authoritative export is `src/paic/web_readiness.py`; the JSON Schema is `schemas/web-readiness-bundle.schema.json`.

Generate and validate it with:

```bash
make web-bundle
make web-validate
```

The export loads `configs/tui/smoke.yaml`, runs the existing workspace validators and authoritative replay, and fails unless every configured stage is healthy. `bundle.json` is closed-world and deterministic: it contains schema version `1.0`, synthetic-data disclaimer, lifecycle/detection/operations/investigation/impact/remediation/recovery/evaluation section metadata, stage snapshots, sanitized artifact files, source hashes, and file sizes. `manifest.json` binds the bundle bytes; `SHA256SUMS` binds the manifest and bundle bytes.

Identical validated source bytes produce identical output bytes. Absolute paths, backslashes, NULs, symlinks, credentials, environment-like keys, evaluator answer keys, malformed JSON, and unsupported values are rejected or excluded by policy. The public artifact is not evidence of production performance and must not be extended with browser-supplied source paths or mutation endpoints.

The final web engineer consumes this contract only. They may add presentation, accessibility, and static hosting around the validated bundle, but may not move probability, authorization, recovery, evaluator, or artifact-validation authority into browser code.
