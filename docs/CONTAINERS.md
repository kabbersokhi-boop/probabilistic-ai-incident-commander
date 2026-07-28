# Container Baseline

Phase 12 begins with a deliberately small container boundary for the existing PAIC command-line interface. The image packages the governed local reference implementation; it does not add a daemon, public endpoint, database, cloud integration, secret store, or new operational authority.

## Build

```bash
docker build \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  --build-arg VERSION=0.12.0 \
  --tag paic:local \
  .
```

The Dockerfile uses a multi-stage build. The builder creates a wheel and dependency wheels. The runtime stage installs only those wheels, bundles read-only reference `specs`, `configs`, and `schemas`, and runs as UID/GID `10001:10001`.

The Python base is pinned to the multi-platform digest for Python 3.12.13 slim Bookworm. Automated base refresh, dependency lockfiles, SBOM generation, provenance attestations, and signing remain later Phase 12 supply-chain units.

## Hardened validation

```bash
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  paic:local validate --spec-dir /opt/paic/specs
```

The same boundary can render the default read-only contract summary:

```bash
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  paic:local
```

No provider credentials, approval keys, cloud credentials, or external network access are needed for these checks.

## Compose

Validate the Compose model and run the one-shot service:

```bash
PAIC_VCS_REF="$(git rev-parse HEAD)" docker compose config --quiet
PAIC_VCS_REF="$(git rev-parse HEAD)" docker compose build validate
PAIC_VCS_REF="$(git rev-parse HEAD)" docker compose run --rm validate
```

The Compose service uses `pull_policy: never`, so the run must use the locally built image rather than contacting a registry. It has no ports, networks, persistent volumes, or restart loop and exits after validating the bundled contracts.

## Security boundary

The baseline enforces:

- fixed non-root user `10001:10001`;
- no container network;
- read-only root filesystem;
- a bounded writable `/tmp` tmpfs;
- all Linux capabilities dropped;
- `no-new-privileges`;
- credential-free deterministic validation.

These controls reduce the container runtime boundary. They do not make the synthetic reference implementation a production incident-management service and do not grant approval, remediation, recovery, shell, cloud, or secret authority.

## Remaining Phase 12 work

The following are explicitly outside this unit:

- automated digest refresh and compatibility review;
- locked and hashed Python dependencies;
- SBOM, vulnerability policy, provenance, and image signing;
- persistent services and durable storage;
- workload identity and secret delivery;
- metrics, logs, traces, and alerting;
- backup and restore;
- deployment manifests and rollout testing;
- container interruption, restart, resource-pressure, and endurance certification.
