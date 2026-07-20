"""Crash-consistent publication of immutable artifact directories."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PublicationPoint = Literal[
    "staging-created",
    "payload-written",
    "old-moved",
    "new-committed",
    "parent-synced",
]
FailureHook = Callable[[PublicationPoint], None]


class ArtifactPublicationError(RuntimeError):
    """Raised when an artifact directory cannot be published safely."""


@dataclass(frozen=True)
class PublicationResult:
    target: Path
    committed: bool
    durability_confirmed: bool


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_safe_target(target: Path) -> None:
    absolute = target.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parent.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ArtifactPublicationError("artifact parent traverses a symbolic link")
        if current.exists() and not current.is_dir():
            raise ArtifactPublicationError("artifact parent contains a non-directory component")
    if target.is_symlink():
        raise ArtifactPublicationError("artifact target must not be a symbolic link")
    if target.exists() and not target.is_dir():
        raise ArtifactPublicationError("artifact target must be a directory")


class AtomicDirectoryPublisher:
    """Build beside the target, then atomically exchange complete generations.

    Before commit, failures preserve the previous generation. After commit, failures
    report that the new generation is visible but durability could not be confirmed.
    """

    def __init__(
        self,
        target: str | Path,
        *,
        overwrite: bool = False,
        failure_hook: FailureHook | None = None,
    ) -> None:
        self.target = Path(target)
        self.overwrite = overwrite
        self.failure_hook = failure_hook
        self.staging: Path | None = None
        self.backup: Path | None = None
        self.committed = False
        self.durability_confirmed = False

    def _point(self, point: PublicationPoint) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def __enter__(self) -> Path:
        _assert_safe_target(self.target)
        if self.target.exists() and not self.overwrite:
            raise ArtifactPublicationError(f"output directory already exists: {self.target}")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        _assert_safe_target(self.target)
        self.staging = Path(
            tempfile.mkdtemp(prefix=f".{self.target.name}.staging-", dir=self.target.parent)
        )
        try:
            self._point("staging-created")
        except Exception:
            shutil.rmtree(self.staging, ignore_errors=True)
            self.staging = None
            raise
        return self.staging

    def commit(self) -> PublicationResult:
        if self.staging is None:
            raise ArtifactPublicationError("publisher has not been entered")
        self._point("payload-written")
        if self.target.exists():
            self.backup = Path(
                tempfile.mkdtemp(prefix=f".{self.target.name}.backup-", dir=self.target.parent)
            )
            self.backup.rmdir()
            os.replace(self.target, self.backup)
            self._point("old-moved")
        try:
            os.replace(self.staging, self.target)
            self.committed = True
            self.staging = None
            self._point("new-committed")
            _fsync_directory(self.target.parent)
            self.durability_confirmed = True
            self._point("parent-synced")
        except Exception as exc:
            if not self.committed and self.backup is not None and not self.target.exists():
                with suppress(OSError):
                    os.replace(self.backup, self.target)
                    self.backup = None
            state = "committed but durability is uncertain" if self.committed else "not committed"
            raise ArtifactPublicationError(f"artifact publication failed ({state}): {exc}") from exc
        if self.backup is not None:
            shutil.rmtree(self.backup)
            self.backup = None
            with suppress(OSError):
                _fsync_directory(self.target.parent)
        return PublicationResult(self.target, True, self.durability_confirmed)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.staging is not None:
            shutil.rmtree(self.staging, ignore_errors=True)
            self.staging = None
        if exc is not None and not self.committed and self.backup is not None:
            if not self.target.exists():
                with suppress(OSError):
                    os.replace(self.backup, self.target)
            if self.backup.exists():
                shutil.rmtree(self.backup, ignore_errors=True)
            self.backup = None
