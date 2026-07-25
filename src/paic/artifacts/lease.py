"""Fail-closed shared-reader/exclusive-writer artifact leases.

Every artifact is mapped to a stable coordination domain: the highest writable
ancestor below the filesystem root that cannot be renamed by an unprivileged
owner of the artifact tree.  The domain directory inode is the authoritative
reader/writer lock, so replacing an artifact parent or a diagnostic lock file
cannot create a second active read/write domain.

Admission uses a classic writer turnstile on the stable domain's parent
directory inode.  Because the gate is a directory inode rather than a writable
lock pathname, an unprivileged process cannot replace it.  Readers release the
gate immediately after acquiring the shared domain lock; writers retain it while
draining readers and for the duration of their exclusive lease.

The artifact-local ``.<name>.lease`` file is retained for diagnostics and
compatibility, but safety does not depend on its pathname remaining stable.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import errno
import hashlib
import inspect
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from typing import Literal, ParamSpec, TypeVar

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported non-POSIX platforms
    fcntl = None  # type: ignore[assignment]


class ArtifactLeaseError(RuntimeError):
    """Raised when a safe artifact lease cannot be acquired or released."""


T = TypeVar("T")
P = ParamSpec("P")

Owner = tuple[int, int | None]
ActiveDomain = tuple[bool, int, Owner]

_ACTIVE_DOMAINS: contextvars.ContextVar[dict[str, ActiveDomain] | None] = contextvars.ContextVar(
    "paic_active_artifact_lease_domains", default=None
)
_ACTIVE_PARENTS: contextvars.ContextVar[dict[str, tuple[int, int, int, Owner]] | None] = (
    contextvars.ContextVar("paic_active_artifact_lease_parents", default=None)
)
_ACTIVE_ROOT_FDS: contextvars.ContextVar[dict[int, Owner] | None] = contextvars.ContextVar(
    "paic_active_artifact_root_fds", default=None
)
_ACTIVE_ROOT_PATHS: contextvars.ContextVar[dict[str, tuple[tuple[int, Owner], ...]] | None] = (
    contextvars.ContextVar("paic_active_artifact_root_paths", default=None)
)


def _execution_owner() -> Owner:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), None if task is None else id(task)


def _active_domains() -> dict[str, ActiveDomain]:
    return _ACTIVE_DOMAINS.get() or {}


def _set_active_domain(key: str, value: ActiveDomain | None) -> None:
    state = dict(_active_domains())
    if value is None:
        state.pop(key, None)
    else:
        state[key] = value
    _ACTIVE_DOMAINS.set(state)


def _active_parents() -> dict[str, tuple[int, int, int, Owner]]:
    return _ACTIVE_PARENTS.get() or {}


def _set_active_parent(key: str, value: tuple[int, int, int, Owner] | None) -> None:
    state = dict(_active_parents())
    if value is None:
        state.pop(key, None)
    else:
        state[key] = value
    _ACTIVE_PARENTS.set(state)


def _active_root_fds() -> dict[int, Owner]:
    return _ACTIVE_ROOT_FDS.get() or {}


def _set_active_root_fd(fd: int, owner: Owner | None) -> None:
    state = dict(_active_root_fds())
    if owner is None:
        state.pop(fd, None)
    else:
        state[fd] = owner
    _ACTIVE_ROOT_FDS.set(state)


def _active_root_paths() -> dict[str, tuple[tuple[int, Owner], ...]]:
    return _ACTIVE_ROOT_PATHS.get() or {}


def _push_active_root(path: Path, fd: int, owner: Owner) -> None:
    state = dict(_active_root_paths())
    key = os.path.normcase(os.fspath(_canonical_root(path)))
    state[key] = (*state.get(key, ()), (fd, owner))
    _ACTIVE_ROOT_PATHS.set(state)


def _pop_active_root(path: Path, fd: int, owner: Owner) -> None:
    state = dict(_active_root_paths())
    key = os.path.normcase(os.fspath(_canonical_root(path)))
    current = list(state.get(key, ()))
    for index in range(len(current) - 1, -1, -1):
        if current[index] == (fd, owner):
            current.pop(index)
            break
    if current:
        state[key] = tuple(current)
    else:
        state.pop(key, None)
    _ACTIVE_ROOT_PATHS.set(state)


def _descriptor_root(fd: int) -> Path | None:
    for base in (Path("/proc/self/fd"), Path("/dev/fd")):
        if base.is_dir():
            return base / str(fd)
    return None


def artifact_path(path: str | Path) -> Path:
    """Return an owner-scoped descriptor-relative path for an active artifact root."""

    candidate = _canonical_root(path)
    owner = _execution_owner()
    for root_text, entries in sorted(
        _active_root_paths().items(), key=lambda item: len(item[0]), reverse=True
    ):
        if not entries or entries[-1][1] != owner:
            continue
        try:
            relative = candidate.relative_to(Path(root_text))
        except ValueError:
            continue
        descriptor = _descriptor_root(entries[-1][0])
        if descriptor is None:
            return candidate
        return descriptor / relative
    return candidate


def artifact_root_is_regular(path: str | Path) -> bool:
    """Check an artifact root without rejecting an internal descriptor anchor."""

    descriptor_fd = _descriptor_fd(path)
    owner = _execution_owner()
    if descriptor_fd is not None and _active_root_fds().get(descriptor_fd) == owner:
        try:
            return stat.S_ISDIR(os.fstat(descriptor_fd).st_mode)
        except OSError:
            return False
    candidate = _canonical_root(path)
    key = os.path.normcase(os.fspath(candidate))
    entries = _active_root_paths().get(key, ())
    if entries and entries[-1][1] == owner:
        try:
            return stat.S_ISDIR(os.fstat(entries[-1][0]).st_mode)
        except OSError:
            return False
    return not candidate.is_symlink() and candidate.is_dir()


def _descriptor_fd(value: object) -> int | None:
    if not isinstance(value, (str, os.PathLike)):
        return None
    parts = Path(os.fspath(value)).parts
    if len(parts) == 5 and parts[:4] == ("/", "proc", "self", "fd"):
        candidate = parts[4]
    elif len(parts) == 4 and parts[:3] == ("/", "dev", "fd"):
        candidate = parts[3]
    else:
        return None
    try:
        return int(candidate)
    except ValueError:
        return None


def _is_active_descriptor_root(value: object) -> bool:
    fd = _descriptor_fd(value)
    return fd is not None and _active_root_fds().get(fd) == _execution_owner()


def _canonical_root(root: str | Path) -> Path:
    """Normalize lexical aliases without resolving the artifact itself."""

    return Path(os.path.abspath(os.fspath(root)))


def _lease_path(root: Path) -> Path:
    return root.parent / f".{root.name}.lease"


def _identity_name(root: Path) -> str:
    key = hashlib.sha256(os.fsencode(os.fspath(root))).hexdigest()
    return f".paic-artifact-lease-v2.{os.geteuid()}.{key}.identity"


def _identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _validate_parent_path(parent: Path) -> None:
    """Reject symlink/non-directory ancestors before opening a directory fd."""

    absolute = parent.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ArtifactLeaseError("artifact lease parent is unsafe")


def _validate_parent_info(info: os.stat_result) -> None:
    if not stat.S_ISDIR(getattr(info, "st_mode", 0)):
        raise ArtifactLeaseError("artifact lease parent is unsafe")
    if getattr(info, "st_uid", None) != os.geteuid() or info.st_mode & 0o022:
        raise ArtifactLeaseError("artifact lease parent ownership or permissions are unsafe")


def _permission_bits(info: os.stat_result) -> int:
    euid = os.geteuid()
    if info.st_uid == euid:
        return (info.st_mode >> 6) & 0o7
    groups = set(os.getgroups()) | {os.getegid()}
    if info.st_gid in groups:
        return (info.st_mode >> 3) & 0o7
    return info.st_mode & 0o7


def _entry_is_replaceable(path: Path) -> bool:
    """Whether this user can rename ``path`` through its parent directory."""

    parent = path.parent
    if parent == path:
        return False
    try:
        parent_info = os.stat(parent, follow_symlinks=False)
        path_info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ArtifactLeaseError(f"cannot inspect artifact lease domain: {exc}") from exc
    permissions = _permission_bits(parent_info)
    if permissions & 0o3 != 0o3:
        return False
    if parent_info.st_mode & stat.S_ISVTX:
        euid = os.geteuid()
        return euid == 0 or euid in {parent_info.st_uid, path_info.st_uid}
    return True


def _stable_domain(parent: Path) -> Path:
    """Return an ancestor whose directory entry is not user-replaceable."""

    candidate = parent
    while (
        candidate.parent != candidate
        and candidate.parent.parent != candidate.parent
        and _entry_is_replaceable(candidate)
    ):
        candidate = candidate.parent
    return candidate


def _open_directory(path: Path, *, private: bool) -> tuple[int, os.stat_result]:
    _validate_parent_path(path)
    fd: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if private:
            _validate_parent_info(info)
        elif not stat.S_ISDIR(getattr(info, "st_mode", 0)):
            raise ArtifactLeaseError("artifact lease domain is unsafe")
        current = os.stat(path, follow_symlinks=False)
        if not _identity(info, current):
            raise ArtifactLeaseError("artifact lease directory changed during acquisition")
        return fd, info
    except ArtifactLeaseError:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        raise
    except (OSError, AttributeError) as exc:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        raise ArtifactLeaseError(f"cannot open artifact lease directory: {exc}") from exc


def _open_parent(parent: Path) -> tuple[int, os.stat_result]:
    _validate_parent_path(parent)
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactLeaseError(f"cannot create artifact lease parent: {exc}") from exc
    return _open_directory(parent, private=True)


def _revalidate_directory(path: Path, fd: int, expected: os.stat_result, *, message: str) -> None:
    try:
        _validate_parent_path(path)
        descriptor_info = os.fstat(fd)
        path_info = os.stat(path, follow_symlinks=False)
    except (OSError, ArtifactLeaseError) as exc:
        raise ArtifactLeaseError(message) from exc
    if not _identity(expected, descriptor_info) or not _identity(expected, path_info):
        raise ArtifactLeaseError(message)


def _open_lock_file(directory_fd: int, path: Path, directory_info: os.stat_result) -> int:
    """Open a regular, singly-linked, current-user lock file relative to a directory."""

    name = path.name
    fd: int | None = None
    try:
        try:
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ArtifactLeaseError("artifact coordination file must be regular")
            if before.st_nlink != 1 or before.st_uid != os.geteuid() or before.st_mode & 0o022:
                raise ArtifactLeaseError("artifact coordination file ownership is unsafe")
        except FileNotFoundError:
            before = None
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
        ):
            raise ArtifactLeaseError("artifact coordination file must be regular")
        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _identity(info, after) or not _identity(directory_info, os.fstat(directory_fd)):
            raise ArtifactLeaseError("artifact coordination file changed during acquisition")
        if before is not None and not _identity(before, info):
            raise ArtifactLeaseError("artifact coordination file changed during acquisition")
        return fd
    except ArtifactLeaseError:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        raise
    except (OSError, AttributeError) as exc:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        raise ArtifactLeaseError(f"cannot open artifact coordination file: {exc}") from exc


def _revalidate_lock_file(
    directory_path: Path,
    directory_fd: int,
    path: Path,
    fd: int,
    directory_info: os.stat_result,
) -> None:
    try:
        descriptor_info = os.fstat(fd)
        path_info = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        directory_now = os.fstat(directory_fd)
        directory_path_now = os.stat(directory_path, follow_symlinks=False)
    except OSError as exc:
        raise ArtifactLeaseError("artifact coordination file disappeared during locking") from exc
    if (
        not stat.S_ISREG(descriptor_info.st_mode)
        or descriptor_info.st_nlink != 1
        or descriptor_info.st_uid != os.geteuid()
        or descriptor_info.st_mode & 0o022
        or not _identity(descriptor_info, path_info)
        or not _identity(directory_info, directory_now)
        or not _identity(directory_info, directory_path_now)
    ):
        raise ArtifactLeaseError("artifact coordination file changed during locking")


def _read_identity(fd: int) -> tuple[int, int, int, int] | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 256).decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ArtifactLeaseError("cannot read artifact coordination identity") from exc
    if not raw:
        return None
    try:
        parts = tuple(int(part) for part in raw.strip().split(":"))
    except ValueError as exc:
        raise ArtifactLeaseError("artifact coordination identity is invalid") from exc
    if len(parts) != 4 or any(part < 0 for part in parts):
        raise ArtifactLeaseError("artifact coordination identity is invalid")
    return parts[0], parts[1], parts[2], parts[3]


def _write_identity(fd: int, value: tuple[int, int, int, int]) -> None:
    payload = (":".join(str(part) for part in value) + "\n").encode("ascii")
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        written = os.write(fd, payload)
        if written != len(payload):
            raise OSError("short write")
        os.fsync(fd)
    except OSError as exc:
        raise ArtifactLeaseError("cannot update artifact coordination identity") from exc


def _lock(fd: int, *, exclusive: bool, timeout_seconds: float | None, phase: str) -> None:
    if fcntl is None:
        raise ArtifactLeaseError("artifact leases require POSIX flock support")
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if timeout_seconds is None:
        try:
            fcntl.flock(fd, operation)
            return
        except OSError as exc:
            raise ArtifactLeaseError(f"cannot acquire artifact lease during {phase}") from exc
    if timeout_seconds < 0:
        raise ArtifactLeaseError("artifact lease timeout cannot be negative")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(fd, operation | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {errno.EAGAIN, errno.EACCES}:
                raise ArtifactLeaseError(f"cannot acquire artifact lease during {phase}") from exc
            if time.monotonic() >= deadline:
                raise ArtifactLeaseError(
                    f"timed out acquiring {phase} artifact lease after {timeout_seconds:.3f}s"
                ) from exc
            time.sleep(0.01)


def _release_descriptor(name: str, fd: int | None, locked: bool) -> ArtifactLeaseError | None:
    if fd is None:
        return None
    error: ArtifactLeaseError | None = None
    if locked and fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:
            error = ArtifactLeaseError(f"cannot release artifact lease during {name}")
            error.__cause__ = exc
    try:
        os.close(fd)
    except OSError as exc:
        if error is None:
            error = ArtifactLeaseError(f"cannot close artifact lease during {name}")
            error.__cause__ = exc
    return error


class _ArtifactLease:
    def __init__(self, root: str | Path, *, exclusive: bool, timeout_seconds: float | None = None):
        self.root = _canonical_root(root)
        self.parent = self.root.parent
        self.exclusive = exclusive
        self.timeout_seconds = timeout_seconds
        self.domain: Path | None = None
        self.domain_key: str | None = None
        self.parent_fd: int | None = None
        self.parent_info: os.stat_result | None = None
        self.domain_fd: int | None = None
        self.domain_info: os.stat_result | None = None
        self.gate: Path | None = None
        self.gate_fd: int | None = None
        self.gate_info: os.stat_result | None = None
        self.identity_fd: int | None = None
        self.lease_fd: int | None = None
        self.root_fd: int | None = None
        self.root_info: os.stat_result | None = None
        self.gate_locked = False
        self.domain_locked = False
        self.lease_locked = False
        self._reentrant = False
        self._active = False

    def _prepare_domain(self) -> None:
        if fcntl is None:
            raise ArtifactLeaseError("artifact leases require POSIX flock support")
        _validate_parent_path(self.parent)
        try:
            self.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArtifactLeaseError(f"cannot create artifact lease parent: {exc}") from exc
        _validate_parent_path(self.parent)
        try:
            parent_info = os.stat(self.parent, follow_symlinks=False)
        except OSError as exc:
            raise ArtifactLeaseError(f"cannot inspect artifact lease parent: {exc}") from exc
        _validate_parent_info(parent_info)
        self.domain = _stable_domain(self.parent)
        self.gate = self.domain.parent
        if self.gate == self.domain:
            raise ArtifactLeaseError("artifact lease domain has no stable turnstile parent")
        self.domain_key = os.path.normcase(os.fspath(self.domain))

    def _open_root_anchor(self) -> None:
        if self.parent_fd is None or self.parent_info is None:
            raise ArtifactLeaseError("artifact lease parent was not acquired")
        if self.root_fd is not None:
            if self.root_info is None:
                raise ArtifactLeaseError("artifact root anchor is incomplete")
            try:
                current = os.stat(self.root.name, dir_fd=self.parent_fd, follow_symlinks=False)
            except OSError as exc:
                raise ArtifactLeaseError("artifact root changed during acquisition") from exc
            if not _identity(self.root_info, current):
                raise ArtifactLeaseError("artifact root changed during acquisition")
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            self.root_fd = os.open(self.root.name, flags, dir_fd=self.parent_fd)
        except (FileNotFoundError, NotADirectoryError):
            self.root_fd = None
            self.root_info = None
            return
        except OSError as exc:
            raise ArtifactLeaseError(f"cannot anchor artifact root: {exc}") from exc
        self.root_info = os.fstat(self.root_fd)
        if not stat.S_ISDIR(self.root_info.st_mode):
            raise ArtifactLeaseError("artifact root must be a regular directory")
        current = os.stat(self.root.name, dir_fd=self.parent_fd, follow_symlinks=False)
        if not _identity(self.root_info, current):
            raise ArtifactLeaseError("artifact root changed during acquisition")

    def anchored_root(self) -> Path:
        if self.root_fd is None:
            return self.root
        return _descriptor_root(self.root_fd) or self.root

    def _parent_key(self) -> str:
        return os.path.normcase(os.fspath(self.parent))

    def _check_active_parent(self) -> None:
        if self.parent_info is None:
            raise ArtifactLeaseError("artifact lease parent was not acquired")
        current = _active_parents().get(self._parent_key())
        if current is None:
            return
        dev, ino, _count, owner = current
        if owner == _execution_owner() and (dev, ino) != (
            self.parent_info.st_dev,
            self.parent_info.st_ino,
        ):
            raise ArtifactLeaseError("artifact lease parent changed before nested acquisition")

    def _record_active_parent(self) -> None:
        if self.parent_info is None:
            raise ArtifactLeaseError("artifact lease parent was not acquired")
        key = self._parent_key()
        owner = _execution_owner()
        current = _active_parents().get(key)
        if current is None:
            _set_active_parent(key, (self.parent_info.st_dev, self.parent_info.st_ino, 1, owner))
            return
        dev, ino, count, current_owner = current
        if current_owner != owner or (dev, ino) != (
            self.parent_info.st_dev,
            self.parent_info.st_ino,
        ):
            raise ArtifactLeaseError("artifact lease parent changed during nested acquisition")
        _set_active_parent(key, (dev, ino, count + 1, owner))

    def _release_active_parent(self) -> None:
        key = self._parent_key()
        current = _active_parents().get(key)
        if current is None:
            return
        dev, ino, count, owner = current
        if owner == _execution_owner():
            _set_active_parent(key, None if count <= 1 else (dev, ino, count - 1, owner))

    def _enter_reentrant_if_safe(self) -> bool:
        assert self.domain_key is not None
        current = _active_domains().get(self.domain_key)
        if current is None:
            return False
        active_exclusive, count, owner = current
        if owner != _execution_owner():
            return False
        if self.exclusive and not active_exclusive:
            raise ArtifactLeaseError("cannot acquire nested exclusive artifact lease")
        if self.parent_fd is None or self.parent_info is None:
            self.parent_fd, self.parent_info = _open_parent(self.parent)
        self._check_active_parent()
        if self.root_fd is None:
            self._open_root_anchor()
        self._reentrant = True
        self._active = True
        self._record_active_parent()
        if self.root_fd is not None:
            _set_active_root_fd(self.root_fd, owner)
        _set_active_domain(self.domain_key, (active_exclusive, count + 1, owner))
        return True

    def _mark_active(self) -> None:
        assert self.domain_key is not None
        owner = _execution_owner()
        current = _active_domains().get(self.domain_key)
        if current is None:
            _set_active_domain(self.domain_key, (self.exclusive, 1, owner))
        else:
            if current[2] != owner:
                raise ArtifactLeaseError("artifact lease context owner changed")
            _set_active_domain(
                self.domain_key, (current[0] or self.exclusive, current[1] + 1, owner)
            )
        self._record_active_parent()
        if self.root_fd is not None:
            _set_active_root_fd(self.root_fd, owner)
            _push_active_root(self.root, self.root_fd, owner)
        self._active = True

    def _unmark_active(self) -> None:
        if not self._active or self.domain_key is None:
            return
        current = _active_domains().get(self.domain_key)
        if current is None:
            self._active = False
            return
        if self.root_fd is not None:
            owner = _execution_owner()
            _pop_active_root(self.root, self.root_fd, owner)
            _set_active_root_fd(self.root_fd, None)
        self._release_active_parent()
        if current[1] <= 1:
            _set_active_domain(self.domain_key, None)
        else:
            _set_active_domain(self.domain_key, (current[0], current[1] - 1, current[2]))
        self._active = False

    def validate_current_parent(self) -> None:
        if self.parent_fd is None or self.parent_info is None:
            raise ArtifactLeaseError("artifact lease parent was not acquired")
        _revalidate_directory(
            self.parent,
            self.parent_fd,
            self.parent_info,
            message="artifact lease parent changed while the lease was active",
        )

    def prepare_anchor(self) -> None:
        if self.domain is None:
            self._prepare_domain()
        if self.parent_fd is None or self.parent_info is None:
            self.parent_fd, self.parent_info = _open_parent(self.parent)
        if self.root_fd is None:
            self._open_root_anchor()

    def acquire_intent(self) -> None:
        if self.domain is None:
            self._prepare_domain()
        if self._enter_reentrant_if_safe():
            return
        assert self.domain is not None and self.gate is not None
        if self.parent_fd is None or self.parent_info is None:
            self.parent_fd, self.parent_info = _open_parent(self.parent)
        self.domain_fd, self.domain_info = _open_directory(self.domain, private=False)
        self.gate_fd, self.gate_info = _open_directory(self.gate, private=False)
        try:
            _lock(
                self.gate_fd,
                exclusive=True,
                timeout_seconds=self.timeout_seconds,
                phase="artifact writer turnstile",
            )
            self.gate_locked = True
            _revalidate_directory(
                self.gate,
                self.gate_fd,
                self.gate_info,
                message="artifact writer turnstile changed during locking",
            )
            self.validate_current_parent()
        except Exception:
            self.release()
            raise

    def acquire_body(self) -> None:
        if self._reentrant:
            self.validate_current_parent()
            self.lease_locked = True
            return
        if (
            self.parent_fd is None
            or self.parent_info is None
            or self.domain is None
            or self.domain_fd is None
            or self.domain_info is None
            or not self.gate_locked
        ):
            raise ArtifactLeaseError("artifact lease intent was not acquired")
        try:
            if self.exclusive:
                _lock(
                    self.domain_fd,
                    exclusive=True,
                    timeout_seconds=self.timeout_seconds,
                    phase="artifact coordination domain",
                )
                self.domain_locked = True
                _revalidate_directory(
                    self.domain,
                    self.domain_fd,
                    self.domain_info,
                    message="artifact coordination domain changed during locking",
                )
                self.validate_current_parent()

            self.lease_fd = _open_lock_file(
                self.parent_fd, _lease_path(self.root), self.parent_info
            )
            lease_info = os.fstat(self.lease_fd)
            self.identity_fd = _open_lock_file(
                self.domain_fd, self.domain / _identity_name(self.root), self.domain_info
            )
            current_identity = (
                self.parent_info.st_dev,
                self.parent_info.st_ino,
                lease_info.st_dev,
                lease_info.st_ino,
            )
            recorded_identity = _read_identity(self.identity_fd)
            identity_changed = recorded_identity != current_identity
            if not self.exclusive:
                _lock(
                    self.domain_fd,
                    exclusive=identity_changed,
                    timeout_seconds=self.timeout_seconds,
                    phase="artifact coordination domain",
                )
                self.domain_locked = True
                _revalidate_directory(
                    self.domain,
                    self.domain_fd,
                    self.domain_info,
                    message="artifact coordination domain changed during locking",
                )
                self.validate_current_parent()
            _revalidate_lock_file(
                self.parent,
                self.parent_fd,
                _lease_path(self.root),
                self.lease_fd,
                self.parent_info,
            )
            if identity_changed:
                _write_identity(self.identity_fd, current_identity)
                if not self.exclusive:
                    try:
                        assert fcntl is not None
                        fcntl.flock(self.domain_fd, fcntl.LOCK_SH)
                    except OSError as exc:
                        raise ArtifactLeaseError(
                            "cannot downgrade artifact coordination domain"
                        ) from exc
            _lock(
                self.lease_fd,
                exclusive=self.exclusive,
                timeout_seconds=self.timeout_seconds,
                phase="artifact data",
            )
            _revalidate_lock_file(
                self.parent,
                self.parent_fd,
                _lease_path(self.root),
                self.lease_fd,
                self.parent_info,
            )
            self.validate_current_parent()
            self._open_root_anchor()
            self.lease_locked = True
            self._mark_active()
            if not self.exclusive:
                error = _release_descriptor(
                    "artifact writer turnstile", self.gate_fd, self.gate_locked
                )
                self.gate_fd = None
                self.gate_locked = False
                if error is not None:
                    raise error
        except Exception:
            self.release()
            raise

    def __enter__(self) -> None:
        try:
            self.acquire_intent()
            self.acquire_body()
        except Exception:
            self.release()
            raise
        return None

    def release(self) -> None:
        if self._reentrant:
            error: ArtifactLeaseError | None = None
            try:
                self.validate_current_parent()
            except ArtifactLeaseError as exc:
                error = exc
            self._unmark_active()
            root_error = _release_descriptor("artifact root", self.root_fd, False)
            close_error = _release_descriptor("artifact parent", self.parent_fd, False)
            self.root_fd = None
            self.root_info = None
            self.parent_fd = None
            self.parent_info = None
            self._reentrant = False
            if error is not None:
                raise error
            if root_error is not None:
                raise root_error
            if close_error is not None:
                raise close_error
            return
        errors: list[ArtifactLeaseError] = []
        self._unmark_active()
        for name, fd, locked in (
            ("artifact root", self.root_fd, False),
            ("artifact data", self.lease_fd, self.lease_locked),
            ("artifact identity", self.identity_fd, False),
            ("artifact parent", self.parent_fd, False),
            ("artifact coordination domain", self.domain_fd, self.domain_locked),
            ("artifact writer turnstile", self.gate_fd, self.gate_locked),
        ):
            error = _release_descriptor(name, fd, locked)
            if error is not None:
                errors.append(error)
        self.root_fd = self.lease_fd = self.identity_fd = self.parent_fd = self.domain_fd = (
            self.gate_fd
        ) = None
        self.root_info = None
        self.lease_locked = self.domain_locked = self.gate_locked = False
        if errors:
            raise errors[0]

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> Literal[False]:
        try:
            self.release()
        except ArtifactLeaseError as release_error:
            if isinstance(exc, BaseException):
                exc.add_note(str(release_error))
                return False
            raise
        return False


def artifact_lease(
    root: str | Path,
    *,
    exclusive: bool,
    timeout_seconds: float | None = None,
) -> _ArtifactLease:
    """Hold a fair, anchored kernel lease for a complete logical read/write."""

    return _ArtifactLease(root, exclusive=exclusive, timeout_seconds=timeout_seconds)


def _reader_wrapper(
    func: Callable[P, T], root_names: tuple[str, ...], *, strict_first: bool
) -> Callable[P, T]:
    signature = inspect.signature(func)
    parameters = signature.parameters
    if strict_first:
        ordered = tuple(parameters.values())
        if not ordered:
            raise TypeError("artifact_reader requires a function with an artifact-root parameter")
        first = ordered[0]
        if first.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            raise TypeError("artifact_reader requires a concrete artifact-root parameter")
        if first.name in {"self", "cls"}:
            raise TypeError("artifact_reader does not support method receivers")
        if first.default is not inspect.Parameter.empty:
            raise TypeError("artifact_reader requires a non-optional artifact-root parameter")
    for name in root_names:
        parameter = parameters.get(name)
        if parameter is None or parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            raise TypeError(f"artifact reader root parameter is invalid: {name}")

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        roots = [bound.arguments.get(name) for name in root_names]
        with artifact_reader_leases(roots):
            return func(*bound.args, **bound.kwargs)

    return wrapped


def artifact_reader(func: Callable[P, T]) -> Callable[P, T]:
    """Protect a public loader's complete multi-file read with a shared lease."""

    signature = inspect.signature(func)
    parameters = tuple(signature.parameters.values())
    root_name = parameters[0].name if parameters else ""
    return _reader_wrapper(func, (root_name,), strict_first=True)


def artifact_readers(*root_parameters: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Protect explicitly named required or optional artifact-root parameters."""

    if not root_parameters:
        raise TypeError("artifact_readers requires at least one root parameter")

    def decorate(func: Callable[P, T]) -> Callable[P, T]:
        return _reader_wrapper(func, tuple(root_parameters), strict_first=False)

    return decorate


@contextlib.contextmanager
def artifact_reader_leases(
    roots: Sequence[str | Path | None],
) -> Iterator[dict[str, Path]]:
    """Acquire shared leases in deterministic canonical order."""

    ordered: dict[str, Path] = {}
    for root in roots:
        if root is not None and not _is_active_descriptor_root(root):
            path = _canonical_root(root)
            ordered.setdefault(os.path.normcase(os.fspath(path)), path)
    leases = [(key, _ArtifactLease(ordered[key], exclusive=False)) for key in sorted(ordered)]
    try:
        for _key, lease in leases:
            lease.prepare_anchor()
    except Exception:
        for _key, lease in reversed(leases):
            with contextlib.suppress(ArtifactLeaseError):
                lease.release()
        raise
    with ExitStack() as stack:
        anchored: dict[str, Path] = {}
        for key, lease in leases:
            stack.enter_context(lease)
            anchored[key] = lease.anchored_root()
        yield anchored
