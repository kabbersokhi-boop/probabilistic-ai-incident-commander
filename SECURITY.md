# Security Policy

## Report a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability
reporting feature for this repository. Include the affected commit, the smallest reproducible
example, the expected boundary, and the observed result. Do not include real credentials or
customer data.

You can expect an initial acknowledgement within seven days. A report can require more time to
reproduce and assess. Publication and remediation timing depend on severity and whether a safe fix
is available.

## Supported scope

Security fixes target the current `main` branch. This is a reference implementation, not a hosted
service. It does not process real customer data and it does not operate production infrastructure.

The public dashboard is static and read-only. Its data comes from a sanitized, validated,
checksum-bound bundle. The reference remediation path changes only local synthetic state.

## Trust boundaries

The model has no unrestricted filesystem, network, shell, database, cloud, approval, remediation,
recovery, or evaluator capability. Investigation uses explicit read-only tools. Deterministic code
validates source identity, citations, probability inputs, approval separation, action identity,
and recovery evidence.

These controls do not provide an operating-system sandbox or enterprise identity system. Read the
[security model](docs/SECURITY_MODEL.md), [container boundary](docs/CONTAINERS.md), and
[vulnerability policy](docs/VULNERABILITY_POLICY.md) for the implemented controls and residual
risks.
