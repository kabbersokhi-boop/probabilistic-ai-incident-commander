"""Durable, serialized publication of immutable artifact directories."""

from __future__ import annotations

import ctypes
import os
import secrets
import shutil
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from paic.artifacts.lease import ArtifactLeaseError, _ArtifactLease, artifact_lease

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


def _rename_exchange_at(parent_fd: int, left_name: str, right_name: str) -> None:
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
    if renameat2(parent_fd, os.fsencode(left_name), parent_fd, os.fsencode(right_name), 2) != 0:
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
        raise ArtifactPublicationError("artifact target must not be a symlink (symbolic link)")
    if target.exists() and not target.is_dir():
        raise ArtifactPublicationError("artifact target must be a directory")


class AtomicDirectoryPublisher:
    """Publish a complete tree under an exclusive anchored writer lease."""

    def __init__(
        self,
        target: str | Path,
        *,
        overwrite: bool = False,
        failure_hook: FailureHook | None = None,
    ) -> None:
        self.target = Path(target)
        self._root = self.target.absolute()
        self.overwrite = overwrite
        self.failure_hook = failure_hook
        self.staging: Path | None = None
        self.backup: Path | None = None
        self.lock_path = self._root.parent / f".{self._root.name}.lock"
        self._lock_fd: int | None = None
        self._lock_identity: tuple[int, int] | None = None
        self._rollback_failed = False
        self.committed = False
        self.durability_confirmed = False
        self._lease: _ArtifactLease | None = None
        self._parent_anchor_fd: int | None = None
        self._parent_anchor_identity: tuple[int, int] | None = None

    def _point(self, point: PublicationPoint) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)

    def _open_parent_anchor(self) -> int:
        if self._parent_anchor_fd is not None:
            return self._parent_anchor_fd
        descriptor: int | None = None
        try:
            if self._lease is not None and self._lease.parent_fd is not None:
                descriptor = os.dup(self._lease.parent_fd)
            else:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(self._root.parent, flags)
            info = os.fstat(descriptor)
            current = os.stat(self._root.parent, follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_dev != current.st_dev
                or info.st_ino != current.st_ino
            ):
                raise ArtifactPublicationError("artifact publication parent changed")
            self._parent_anchor_fd = descriptor
            self._parent_anchor_identity = (info.st_dev, info.st_ino)
            return descriptor
        except ArtifactPublicationError:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            raise ArtifactPublicationError(
                f"cannot acquire artifact publication parent: {exc}"
            ) from exc

    def _parent_fd(self) -> int:
        return self._open_parent_anchor()

    def _validate_parent_anchor(self) -> None:
        descriptor = self._parent_fd()
        try:
            anchored = os.fstat(descriptor)
            current = os.stat(self._root.parent, follow_symlinks=False)
        except OSError as exc:
            raise ArtifactPublicationError("artifact publication parent changed") from exc
        expected = self._parent_anchor_identity
        if (
            expected is None
            or not stat.S_ISDIR(anchored.st_mode)
            or expected != (anchored.st_dev, anchored.st_ino)
            or expected != (current.st_dev, current.st_ino)
        ):
            raise ArtifactPublicationError("artifact publication parent changed")
        if self._lease is not None:
            self._lease.validate_current_parent()

    def _close_parent_anchor(self) -> None:
        if self._parent_anchor_fd is None:
            return
        descriptor = self._parent_anchor_fd
        self._parent_anchor_fd = None
        self._parent_anchor_identity = None
        try:
            os.close(descriptor)
        except OSError as exc:
            raise ArtifactPublicationError("cannot close artifact publication parent") from exc

    def _abort_enter(self, exc: BaseException) -> None:
        try:
            self.__exit__(type(exc), exc, exc.__traceback__)
        except BaseException as cleanup_error:
            exc.add_note(f"publisher entry cleanup failed: {cleanup_error}")

    def _parent_entry(self, name: str) -> Path:
        """Return a lexical sibling path; descriptor-relative syscalls are authoritative."""
        return self._root.parent / name

    def _target_exists(self) -> bool:
        try:
            info = os.stat(self._root.name, dir_fd=self._parent_fd(), follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(info.st_mode):
            raise ArtifactPublicationError("artifact target must be a directory")
        return True

    def _make_staging(self) -> Path:
        for _ in range(128):
            name = f".{self._root.name}.staging-{secrets.token_hex(8)}"
            try:
                os.mkdir(name, 0o700, dir_fd=self._parent_fd())
            except FileExistsError:
                continue
            return self._parent_entry(name)
        raise ArtifactPublicationError("cannot allocate a unique staging directory")

    def _release_lock(self) -> None:
        if self._lock_fd is None:
            return
        close_error: OSError | None = None
        try:
            os.close(self._lock_fd)
        except OSError as exc:
            close_error = exc
        self._lock_fd = None
        try:
            current = os.stat(
                self.lock_path.name,
                dir_fd=self._parent_fd(),
                follow_symlinks=False,
            )
            if self._lock_identity == (current.st_dev, current.st_ino):
                os.unlink(self.lock_path.name, dir_fd=self._parent_fd())
        except (OSError, ArtifactPublicationError):
            pass
        self._lock_identity = None
        if close_error is not None:
            raise ArtifactPublicationError("cannot close artifact writer lock") from close_error

    def __enter__(self) -> Path:
        try:
            _assert_safe_target(self._root)
            self._root.parent.mkdir(parents=True, exist_ok=True)
            _assert_safe_target(self._root)
            self._lease = artifact_lease(self._root, exclusive=True)
            self._lease.__enter__()
            self._open_parent_anchor()
            self._validate_parent_anchor()
            if self._target_exists() and not self.overwrite:
                raise ArtifactPublicationError(f"output directory already exists: {self.target}")
            try:
                lock_info = os.stat(
                    self.lock_path.name,
                    dir_fd=self._parent_fd(),
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                lock_info = None
            if lock_info is not None and not stat.S_ISREG(lock_info.st_mode):
                raise ArtifactPublicationError("artifact writer lock is not a regular file")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            self._lock_fd = os.open(
                self.lock_path.name,
                flags,
                0o600,
                dir_fd=self._parent_fd(),
            )
            lock_info = os.fstat(self._lock_fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
                raise ArtifactPublicationError("artifact writer lock is not a regular file")
            self._lock_identity = (lock_info.st_dev, lock_info.st_ino)
            os.write(self._lock_fd, f"{os.getpid()}\n".encode())
            os.fsync(self._lock_fd)
            self._validate_parent_anchor()
            self.staging = self._make_staging()
            self._validate_parent_anchor()
            self._point("staging-created")
            return self.staging
        except FileExistsError as exc:
            self._abort_enter(exc)
            raise ArtifactPublicationError(
                f"artifact target is already locked: {self.lock_path}"
            ) from exc
        except ArtifactPublicationError as exc:
            self._abort_enter(exc)
            raise
        except ArtifactLeaseError as exc:
            self._abort_enter(exc)
            raise ArtifactPublicationError(
                f"cannot acquire artifact publication lease: {exc}"
            ) from exc
        except OSError as exc:
            self._abort_enter(exc)
            raise ArtifactPublicationError(f"cannot acquire artifact writer lock: {exc}") from exc
        except Exception as exc:
            self._abort_enter(exc)
            raise

    def commit(self) -> PublicationResult:
        if self.staging is None:
            raise ArtifactPublicationError("publisher has not been entered")
        try:
            self._validate_parent_anchor()
            _fsync_payload_tree(self.staging)
            self._point("payload-written")
            self._validate_parent_anchor()
            staging_name = self.staging.name
            if self._target_exists():
                self._point("old-moved")
                _rename_exchange_at(self._parent_fd(), self._root.name, staging_name)
                self.backup = self._parent_entry(staging_name)
            else:
                os.replace(
                    staging_name,
                    self._root.name,
                    src_dir_fd=self._parent_fd(),
                    dst_dir_fd=self._parent_fd(),
                )
            self.committed = True
            self.staging = None
            self._point("new-committed")
            os.fsync(self._parent_fd())
            self.durability_confirmed = True
            self._point("parent-synced")
            if self.backup is not None:
                shutil.rmtree(self.backup)
                self.backup = None
                os.fsync(self._parent_fd())
            self._validate_parent_anchor()
        except Exception as exc:
            if not self.committed and self.backup is not None and not self._target_exists():
                backup = self.backup
                try:
                    os.replace(
                        backup.name,
                        self._root.name,
                        src_dir_fd=self._parent_fd(),
                        dst_dir_fd=self._parent_fd(),
                    )
                    self.backup = None
                    os.fsync(self._parent_fd())
                except OSError as restore_exc:
                    self._rollback_failed = True
                    raise ArtifactPublicationError(
                        "artifact publication rollback failed; backup preserved at "
                        f"{self._root.parent / backup.name}: {restore_exc}"
                    ) from exc
            if self.committed and self.durability_confirmed:
                state = "committed and durable; post-commit cleanup failed"
            elif self.committed:
                state = "committed but durability is uncertain"
            else:
                state = "not committed"
            raise ArtifactPublicationError(f"artifact publication failed ({state}): {exc}") from exc
        return PublicationResult(self.target, True, self.durability_confirmed)

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        pending_error: BaseException | None = None
        try:
            if self.staging is not None:
                try:
                    shutil.rmtree(self.staging)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    raise ArtifactPublicationError(
                        f"artifact staging cleanup failed: {cleanup_exc}"
                    ) from cleanup_exc
                self.staging = None
            if exc is not None and not self.committed and self.backup is not None:
                if not self._target_exists():
                    try:
                        os.replace(
                            self.backup.name,
                            self._root.name,
                            src_dir_fd=self._parent_fd(),
                            dst_dir_fd=self._parent_fd(),
                        )
                        os.fsync(self._parent_fd())
                    except OSError as restore_exc:
                        self._rollback_failed = True
                        raise ArtifactPublicationError(
                            "artifact publication rollback failed; backup preserved at "
                            f"{self._root.parent / self.backup.name}"
                        ) from restore_exc
                if not self.backup.exists():
                    self.backup = None
        except BaseException as cleanup_error:
            pending_error = cleanup_error
        finally:
            try:
                self._release_lock()
            except BaseException as lock_error:
                if pending_error is None:
                    pending_error = lock_error
            if self._lease is not None:
                try:
                    self._lease.__exit__(exc_type, exc, traceback)
                except BaseException as lease_error:
                    if pending_error is None:
                        pending_error = lease_error
                finally:
                    self._lease = None
            try:
                self._close_parent_anchor()
            except BaseException as anchor_error:
                if pending_error is None:
                    pending_error = anchor_error
        if pending_error is not None:
            raise pending_error
