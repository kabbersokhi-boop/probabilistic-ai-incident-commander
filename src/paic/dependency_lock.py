"""Fail-closed checks for generated, hash-locked requirement exports."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


class LockValidationError(ValueError):
    """Raised when a committed dependency lock is unsafe or stale-shaped."""


@dataclass(frozen=True)
class LockedRequirement:
    name: str
    marker: str | None
    hashes: tuple[str, ...]


_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[^\s;\\]+)"
    r"(?:\s*;\s*(?P<marker>[^\\]+?))?\s*\\?$"
)
_HASH = re.compile(r"^\s+--hash=sha256:[0-9a-f]{64}\s*\\?$")


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path) -> tuple[LockedRequirement, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LockValidationError(f"cannot read lock {path}: {exc}") from exc

    records: list[LockedRequirement] = []
    current: LockedRequirement | None = None
    hashes: list[str] = []

    def finish() -> None:
        nonlocal current, hashes
        if current is None:
            return
        if not hashes:
            raise LockValidationError(f"{path}: {current.name} has no archive hashes")
        records.append(LockedRequirement(current.name, current.marker, tuple(hashes)))
        current = None
        hashes = []

    for line_number, line in enumerate(lines, 1):
        if not line or line.startswith("#") or line.startswith("    #"):
            continue
        if line.startswith(("-e ", "--", "file:", "git+", "http:", "https:")):
            raise LockValidationError(f"{path}:{line_number}: unsupported mutable/local entry")
        match = _REQUIREMENT.fullmatch(line)
        if match:
            finish()
            current = LockedRequirement(
                name=match.group("name"), marker=match.group("marker"), hashes=()
            )
            continue
        if _HASH.fullmatch(line):
            if current is None:
                raise LockValidationError(f"{path}:{line_number}: hash without requirement")
            hashes.append(line.strip().removesuffix("\\").strip())
            continue
        raise LockValidationError(f"{path}:{line_number}: malformed lock entry")
    finish()

    seen: set[tuple[str, str | None]] = set()
    for record in records:
        key = (canonical_name(record.name), record.marker)
        if key in seen:
            raise LockValidationError(f"{path}: duplicate requirement {record.name!r}")
        seen.add(key)
    if not records:
        raise LockValidationError(f"{path}: lock contains no requirements")
    return tuple(records)


def declared_dependency_names(pyproject: Path) -> set[str]:
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise LockValidationError(f"cannot read pyproject {pyproject}: {exc}") from exc
    project = payload.get("project")
    if not isinstance(project, dict):
        raise LockValidationError("pyproject has no [project] table")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        raise LockValidationError("[project].dependencies must be a list")
    names: set[str] = set()
    for item in dependencies:
        if not isinstance(item, str):
            raise LockValidationError("dependency declarations must be strings")
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", item)
        if match is None:
            raise LockValidationError(f"malformed dependency declaration: {item!r}")
        names.add(canonical_name(match.group(1)))
    return names


def validate_lock_set(
    *, pyproject: Path, lock_paths: tuple[Path, ...], allowed_names: set[str] | None = None
) -> None:
    declared = declared_dependency_names(pyproject)
    all_records: list[LockedRequirement] = []
    for path in lock_paths:
        all_records.extend(parse_lock(path))
    locked_names = {canonical_name(record.name) for record in all_records}
    missing = sorted(declared - locked_names)
    if missing:
        raise LockValidationError("declared dependencies missing from lock: " + ", ".join(missing))
    if allowed_names is not None:
        unexpected = sorted(locked_names - {canonical_name(name) for name in allowed_names})
        if unexpected:
            raise LockValidationError("unexpected locked packages: " + ", ".join(unexpected))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("locks", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        validate_lock_set(pyproject=args.pyproject, lock_paths=tuple(args.locks))
    except LockValidationError as exc:
        print(f"dependency lock error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
