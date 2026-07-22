"""Durable, serialized publication of immutable artifact directories."""

from __future__ import annotations

import ctypes
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from paic.artifacts.lease import artifact_lease

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


def _fsync_payload_tree(root: Path) -> None:
    """Flush every regular payload and directory in deterministic order."""
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ArtifactPublicationError("staged payload must not contain symbolic links")
        if stat.S_ISREG(info.st_mode):
            with entry.open("rb") as handle:
                os.fsync(handle.fileno())
        elif stat.S_ISDIR(info.st_mode):
            _fsync_payload_tree(entry)
        else:
            raise ArtifactPublicationError("staged payload contains a non-regular entry")
    _fsync_directory(root)


def _rename_exchange(left: Path, right: Path) -> None:
    """Exchange two names without a missing-target window on Linux."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError("atomic directory exchange is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


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
    """Publish a complete tree under an exclusive per-target writer lock.

    Existing targets are exchanged with the staged tree using Linux ``renameat2``;
    readers therefore observe either complete generation. Platforms without the
    exchange primitive fail closed rather than exposing a two-rename gap. A lock
    file is never broken automatically; an operator must verify the writer is dead
    before removing a stale lock.
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
        self.lock_path = self.target.parent / f".{self.target.name}.lock"
        self._lock_fd: int | None = None
        self._rollback_failed = False
        self.committed = False
        self.durability_confirmed = False
        self._lease: AbstractContextManager[None] | None = None

    def _point(self, point: PublicationPoint) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def _release_lock(self) -> None:
        if self._lock_fd is None:
            return
        os.close(self._lock_fd)
        self._lock_fd = None
        with suppress(OSError):
            self.lock_path.unlink()

    def __enter__(self) -> Path:
        _assert_safe_target(self.target)
        if self.target.exists() and not self.overwrite:
            raise ArtifactPublicationError(f"output directory already exists: {self.target}")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        _assert_safe_target(self.target)
        if self.lock_path.is_symlink() or (
            self.lock_path.exists() and not self.lock_path.is_file()
        ):
            raise ArtifactPublicationError("artifact writer lock is not a regular file")
        try:
            self._lock_fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(self._lock_fd, f"{os.getpid()}\n".encode())
            os.fsync(self._lock_fd)
        except FileExistsError as exc:
            raise ArtifactPublicationError(
                f"artifact target is already locked: {self.lock_path}"
            ) from exc
        except OSError as exc:
            raise ArtifactPublicationError(f"cannot acquire artifact writer lock: {exc}") from exc
        self.staging = Path(
            tempfile.mkdtemp(prefix=f".{self.target.name}.staging-", dir=self.target.parent)
        )
        try:
            self._point("staging-created")
        except Exception:
            shutil.rmtree(self.staging, ignore_errors=True)
            self.staging = None
            self._release_lock()
            raise
        return self.staging

    def commit(self) -> PublicationResult:
        if self.staging is None:
            raise ArtifactPublicationError("publisher has not been entered")
        lease_entered = False
        try:
            self._lease = artifact_lease(self.target, exclusive=True)
            self._lease.__enter__()
            lease_entered = True
            _fsync_payload_tree(self.staging)
            self._point("payload-written")
            if self.target.exists():
                self._point("old-moved")
                _rename_exchange(self.target, self.staging)
                self.backup = self.staging
            else:
                os.replace(self.staging, self.target)
            self.committed = True
            self.staging = None
            self._point("new-committed")
            _fsync_directory(self.target.parent)
            self.durability_confirmed = True
            self._point("parent-synced")
            if self.backup is not None:
                shutil.rmtree(self.backup)
                self.backup = None
                _fsync_directory(self.target.parent)
        except Exception as exc:
            if not self.committed and self.backup is not None and not self.target.exists():
                try:
                    os.replace(self.backup, self.target)
                    self.backup = None
                except OSError as restore_exc:
                    self._rollback_failed = True
                    raise ArtifactPublicationError(
                        "artifact publication rollback failed; backup preserved at "
                        f"{self.backup}: {restore_exc}"
                    ) from exc
            state = "committed but durability is uncertain" if self.committed else "not committed"
            raise ArtifactPublicationError(f"artifact publication failed ({state}): {exc}") from exc
        finally:
            if self._lease is not None and lease_entered:
                self._lease.__exit__(None, None, None)
                self._lease = None
        return PublicationResult(self.target, True, self.durability_confirmed)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.staging is not None:
            shutil.rmtree(self.staging, ignore_errors=True)
            self.staging = None
        if exc is not None and not self.committed and self.backup is not None:
            if not self.target.exists():
                try:
                    os.replace(self.backup, self.target)
                except OSError as restore_exc:
                    self._rollback_failed = True
                    self._release_lock()
                    raise ArtifactPublicationError(
                        f"artifact publication rollback failed; backup preserved at {self.backup}"
                    ) from restore_exc
            if self.backup.exists():
                # Preserve a complete generation if restoration could not finish.
                self._release_lock()
                return
            self.backup = None
        self._release_lock()
