# Release checklist

- exact intended commit checked out and clean;
- Python 3.11 and 3.12 quality, type, test, branch-coverage, smoke, adversarial, and authoritative soak gates pass;
- all three locks are fresh, hashed, and accepted by the lock validator;
- package and digest-pinned container build succeed without uncontrolled runtime resolution;
- base policy, container evidence, SBOM, vulnerability, secret, and public-bundle scans pass;
- web bundle schema is regenerated and deterministic;
- bundle backup restores into a clean destination and validates;
- promotion and rollback evidence identifies immutable checksums;
- provenance/signing status is reported honestly, with any platform evidence independently verified;
- no unresolved critical/high finding exists without an exact, justified, unexpired exception;
- documentation and the final web-product handoff match the implementation;
- no UI or mutation service is included in this foundation release.
