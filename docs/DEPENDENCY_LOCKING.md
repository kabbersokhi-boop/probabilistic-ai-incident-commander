# Dependency locking

`pyproject.toml` is the human-edited declaration. `uv.lock` is the resolver lock; the three requirements exports are committed installation locks:

- `requirements.lock`: production runtime distributions;
- `requirements-build.lock`: package/container build tooling;
- `requirements-dev.lock`: test, lint, type, audit, and build tooling.

Every applicable distribution has an exact version and SHA-256 hashes. Marker-specific records are retained for Python 3.11 and 3.12. The container builds wheels from the locks and installs the runtime without network access or dependency resolution.

After changing declarations, use `uv lock`, then regenerate all exports with the commands in the Makefile. Run `make locks-validate locks-freshness`. Review the complete lock diff, yanked-release warnings, Python markers, hashes, and transitive additions. Never hand-edit generated locks or accept editable, VCS, local-path, mutable-URL, unhashed, or duplicate entries.

The lock gate is a freshness check, not a claim of universal cross-platform wheel availability. CI tests the supported Linux runner on Python 3.11 and 3.12; other platforms must be verified separately.
