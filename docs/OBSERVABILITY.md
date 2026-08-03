# Observability

The current public foundation is static, so its operational signals are build and publication evidence rather than a long-lived request service. Every bundle has a schema version, source bindings, manifest hash, and exact file hashes. CI records exact-head checkout, validation, scan, package, image, and soak results with bounded retention.

The final host should expose the bundle version and source commit as public metadata and collect host-level availability, latency, error, cache, and bandwidth signals without collecting bundle contents or credentials. Alerts should cover failed deployment validation, unavailable hosting, checksum mismatch, stale bundle generation, and error-rate/SLO breaches. The associated operator action is documented in `ROLLBACK.md`; no alert may authorize remediation.

If a service is introduced, add structured event names, severity, correlation/request IDs, health/readiness, dependency health, bounded metrics, and secret/path redaction tests before enabling it. The browser remains a read-only consumer.
