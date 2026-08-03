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

The Dockerfile uses one direct, digest-pinned external base stage and derives the builder and runtime stages from it. The builder creates a wheel and dependency wheels. The runtime stage installs only those wheels, bundles read-only reference `specs`, `configs`, and `schemas`, and runs as UID/GID `10001:10001`.

The approved base policy allows Python 3.12 patch releases on slim Bookworm only. The concrete image remains pinned to a complete lowercase SHA-256 digest. Major or minor Python movement, Debian variant movement, mutable tags, external helper images, and base-image build-argument indirection are rejected.

## Base refresh policy

Validate the Dockerfile before building:

```bash
PYTHONPATH=src python -m paic.container_base_policy \
  --dockerfile Dockerfile \
  --policy configs/container-base-policy.json \
  --output .artifacts/container-base/container-base-evidence.json
```

The validator is dependency-free and checks that:

- there is exactly one external image and every `FROM` instruction names a unique stage;
- the external image is a direct `docker.io/library/python` reference with a complete digest;
- the tag remains in the approved Python 3.12 series and `slim-bookworm` variant;
- the `builder` and `runtime` stages derive directly from the approved `python-base` stage;
- the JSON policy has only the expected keys and strict value formats.

The output is deterministic JSON containing the approved registry, repository, series, variant, exact image tag, parsed Python patch version, digest, and stage graph. Exact-head container CI validates this policy before the image build and retains the evidence for 14 days.

Dependabot checks the root Dockerfile weekly, limits concurrent Docker update pull requests to two, and ignores Python major and minor version updates. Patch and digest changes remain reviewable pull requests. No auto-merge, registry push, broader workflow permission, or deployment authority is added.

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

## Container evidence

The exact-head container workflow collects three sanitized inputs after the hardened runtime and Compose checks pass:

- `image-inspect.json`: image ID, repository tags, configured user, entrypoint, command, and OCI labels only;
- `python-packages.json`: the installed Python package inventory from `pip inspect --local`;
- `debian-packages.tsv`: package, version, and architecture from `dpkg-query`.

It then runs the dependency-free evidence builder:

```bash
PYTHONPATH=src python -m paic.container_evidence build \
  --image-inspect .artifacts/container-evidence-inputs/image-inspect.json \
  --pip-inspect .artifacts/container-evidence-inputs/python-packages.json \
  --debian-packages .artifacts/container-evidence-inputs/debian-packages.tsv \
  --output-dir .artifacts/container-evidence
```

The generated bundle contains:

- `container-evidence.json`, which binds the image SHA-256 ID, OCI revision, version, source, configured command, and intended hardened runtime boundary;
- `sbom.cdx.json`, a deterministic CycloneDX 1.6 inventory of installed Python and Debian packages;
- the three sanitized source inventories;
- `SHA256SUMS`, covering every other file in the bundle.

Validate the bundle against an expected commit and image:

```bash
PYTHONPATH=src python -m paic.container_evidence validate \
  --bundle-dir .artifacts/container-evidence \
  --expected-revision "$(git rev-parse HEAD)" \
  --expected-image-id "$(docker image inspect paic:local --format '{{.Id}}')"
```

Identical input bytes produce identical bundle bytes. Validation rejects a wrong commit, wrong image ID, missing files, malformed package rows, duplicate package references, mismatched component counts, or any changed file hash.

The sanitized inspection record deliberately excludes image environment variables and layer history. The derived manifest and SBOM do not copy runner environment variables, image environment variables, provider keys, approval keys, cloud credentials, or registry credentials. The CI artifact is retained for 14 days and is evidence for review, not a signed provenance statement.

## Security boundary

The baseline enforces:

- fixed non-root user `10001:10001`;
- no container network;
- read-only root filesystem;
- a bounded writable `/tmp` tmpfs;
- all Linux capabilities dropped;
- `no-new-privileges`;
- credential-free deterministic validation;
- exact-image and exact-commit evidence binding;
- exact base-digest and compatibility-policy validation before build.

These controls reduce the container runtime and review boundaries. They do not make the synthetic reference implementation a production incident-management service and do not grant approval, remediation, recovery, shell, cloud, registry, or secret authority.

## Phase 12 boundary

The repository now includes hash-verified dependency locks, fail-closed Python/OS/image
vulnerability policy, deterministic public-bundle export and restore tooling, release
integrity hooks, deployment and rollback policy, and documentation for identity,
observability, and recovery. These controls are exercised by the exact-head workflows
where the platform can provide the required tooling.

The container remains a one-shot governed CLI image. It does not add a daemon, public
endpoint, database, cloud integration, secret store, or new operational authority.
Provenance attestations, keyless signing, and target-environment deployment evidence
must be independently verified for each actual release; configuration alone is not
treated as release evidence.
