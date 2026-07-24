"""Per-artifact shared-reader/exclusive-writer leases.

The lease protocol has three layers, acquired in this order:

* a persistent turnstile file, acquired exclusively by every entrant and held
  for the complete lease; this also prevents a replacement lock pathname from
  creating a second active lock domain;
* the artifact parent directory, which anchors the pathname and prevents a
  cooperating process from switching to a replacement lock inode while the
  artifact is active;
* the persistent artifact lease file itself, retained for compatibility and
  diagnostics.

The protocol assumes the artifact parent is owned by the current user and is
not group/world writable.  A privileged process that can replace the parent
directory is outside this user-space threat model; such a replacement is
detected by parent identity checks and fails closed for the process holding the
old directory descriptor.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import inspect
import os
import stat
import time
from collections.abc import Callable, Iterator, Sequence
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

_ACTIVE_PARENTS: contextvars.ContextVar[dict[str, tuple[bool, int]] | None] = (
    contextvars.ContextVar("paic_active_artifact_lease_parents", default=None)
)


def _active_parents() -> dict[str, tuple[bool, int]]:
    state = _ACTIVE_PARENTS.get()
    if state is None:
        state = {}
        _ACTIVE_PARENTS.set(state)
    return state


def _canonical_root(root: str | Path) -> Path:
    """Normalize lexical aliases without resolving the artifact itself."""

    return Path(os.path.abspath(os.fspath(root)))


def _lease_path(root: Path) -> Path:
    return root.parent / f".{root.name}.lease"


def _intent_path(root: Path) -> Path:
    return root.parent / f".{root.name}.lease.intent"


def _identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _validate_parent_path(parent: Path) -> None:
    """Reject symlink/non-directory ancestors before opening the parent fd."""

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


def _open_parent(parent: Path) -> tuple[int, os.stat_result]:
    _validate_parent_path(parent)
    fd: int | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        _validate_parent_path(parent)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(parent, flags)
        info = os.fstat(fd)
        _validate_parent_info(info)
        current = os.stat(parent, follow_symlinks=False)
        if not _identity(info, current):
            raise ArtifactLeaseError("artifact lease parent changed during acquisition")
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
        raise ArtifactLeaseError(f"cannot open artifact lease parent: {exc}") from exc


def _open_lock_file(parent_fd: int, path: Path, parent_info: os.stat_result) -> int:
    """Open a regular, singly-linked, current-user lock file relative to parent."""

    name = path.name
    fd: int | None = None
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ArtifactLeaseError("artifact coordination file must be regular")
            if before.st_nlink != 1 or before.st_uid != os.geteuid() or before.st_mode & 0o022:
                raise ArtifactLeaseError("artifact coordination file ownership is unsafe")
        except FileNotFoundError:
            before = None
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
        ):
            raise ArtifactLeaseError("artifact coordination file must be regular")
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _identity(info, after) or not _identity(parent_info, os.fstat(parent_fd)):
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


def _revalidate_lock_file(parent_fd: int, path: Path, fd: int, parent_info: os.stat_result) -> None:
    """Confirm the pathname still names the locked descriptor after flock."""

    try:
        descriptor_info = os.fstat(fd)
        path_info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        parent_now = os.fstat(parent_fd)
    except OSError as exc:
        raise ArtifactLeaseError("artifact coordination file disappeared during locking") from exc
    if (
        not stat.S_ISREG(descriptor_info.st_mode)
        or descriptor_info.st_nlink != 1
        or descriptor_info.st_uid != os.geteuid()
        or descriptor_info.st_mode & 0o022
        or not _identity(descriptor_info, path_info)
        or not _identity(parent_info, parent_now)
    ):
        raise ArtifactLeaseError("artifact coordination file changed during locking")


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


class _ArtifactLease:
    def __init__(self, root: str | Path, *, exclusive: bool, timeout_seconds: float | None = None):
        self.root = _canonical_root(root)
        self.parent = self.root.parent
        self.exclusive = exclusive
        self.timeout_seconds = timeout_seconds
        self.parent_fd: int | None = None
        self.intent_fd: int | None = None
        self.lease_fd: int | None = None
        self.intent_locked = False
        self.parent_locked = False
        self.lease_locked = False
        self.parent_info: os.stat_result | None = None
        self._reentrant = False

    def _enter_reentrant_if_safe(self) -> bool:
        active = _active_parents()
        key = os.fspath(self.parent)
        current = active.get(key)
        if current is None:
            return False
        active_exclusive, count = current
        if self.exclusive and not active_exclusive:
            raise ArtifactLeaseError("cannot acquire nested exclusive artifact lease")
        self._reentrant = True
        active[key] = (active_exclusive, count + 1)
        return True

    def _mark_active(self) -> None:
        active = _active_parents()
        key = os.fspath(self.parent)
        current = active.get(key)
        if current is None:
            active[key] = (self.exclusive, 1)
        else:
            active[key] = (current[0] or self.exclusive, current[1] + 1)

    def _unmark_active(self) -> None:
        active = _active_parents()
        key = os.fspath(self.parent)
        current = active.get(key)
        if current is None:
            return
        if current[1] <= 1:
            active.pop(key, None)
        else:
            active[key] = (current[0], current[1] - 1)

    def acquire_intent(self) -> None:
        if fcntl is None:
            raise ArtifactLeaseError("artifact leases require POSIX flock support")
        if self._enter_reentrant_if_safe():
            self.intent_locked = True
            return
        self.parent_fd, self.parent_info = _open_parent(self.parent)
        self.intent_fd = _open_lock_file(self.parent_fd, _intent_path(self.root), self.parent_info)
        try:
            _lock(
                self.intent_fd,
                # The turnstile is intentionally exclusive for readers too.
                # Holding it for the complete reader scope prevents a replaced
                # data-lock pathname from admitting a second active domain.
                exclusive=True,
                timeout_seconds=self.timeout_seconds,
                phase="writer intent" if self.exclusive else "reader intent",
            )
            _revalidate_lock_file(
                self.parent_fd, _intent_path(self.root), self.intent_fd, self.parent_info
            )
            self.intent_locked = True
        except Exception:
            self.release()
            raise

    def acquire_body(self) -> None:
        if self._reentrant:
            self.lease_locked = True
            return
        if self.parent_fd is None or self.parent_info is None or not self.intent_locked:
            raise ArtifactLeaseError("artifact lease intent was not acquired")
        try:
            _lock(
                self.parent_fd,
                exclusive=self.exclusive,
                timeout_seconds=self.timeout_seconds,
                phase="artifact parent",
            )
            self.parent_locked = True
            self.lease_fd = _open_lock_file(
                self.parent_fd, _lease_path(self.root), self.parent_info
            )
            _lock(
                self.lease_fd,
                exclusive=self.exclusive,
                timeout_seconds=self.timeout_seconds,
                phase="artifact data",
            )
            _revalidate_lock_file(
                self.parent_fd, _lease_path(self.root), self.lease_fd, self.parent_info
            )
            self.lease_locked = True
            self._mark_active()
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

    def _release_fd(self, name: str, fd: int | None, locked: bool) -> ArtifactLeaseError | None:
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

    def release(self) -> None:
        if self._reentrant:
            self._unmark_active()
            self._reentrant = False
            return
        errors: list[ArtifactLeaseError] = []
        error = self._release_fd("artifact data", self.lease_fd, self.lease_locked)
        if error is not None:
            errors.append(error)
        error = self._release_fd("artifact parent", self.parent_fd, self.parent_locked)
        if error is not None:
            errors.append(error)
        error = self._release_fd("writer intent", self.intent_fd, self.intent_locked)
        if error is not None:
            errors.append(error)
        self.lease_fd = self.parent_fd = self.intent_fd = None
        self.lease_locked = self.parent_locked = self.intent_locked = False
        self._unmark_active()
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


def artifact_reader(func: Callable[P, T]) -> Callable[P, T]:
    """Protect a public loader's complete multi-file read with a shared lease."""

    signature = inspect.signature(func)
    parameters = tuple(signature.parameters.values())
    if not parameters:
        raise TypeError("artifact_reader requires a function with an artifact-root parameter")
    first = parameters[0]
    if first.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
        raise TypeError("artifact_reader requires a concrete artifact-root parameter")
    if first.name in {"self", "cls"}:
        raise TypeError("artifact_reader does not support method receivers")
    if first.default is not inspect.Parameter.empty:
        raise TypeError("artifact_reader requires a non-optional artifact-root parameter")
    root_name = first.name

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        bound = signature.bind(*args, **kwargs)
        root = bound.arguments[root_name]
        with artifact_lease(root, exclusive=False):
            return func(*args, **kwargs)

    return wrapped


@contextlib.contextmanager
def artifact_reader_leases(roots: Sequence[str | Path | None]) -> Iterator[None]:
    """Acquire shared leases in canonical order, with all intents first."""

    ordered: dict[str, Path] = {}
    for root in roots:
        if root is not None:
            path = _canonical_root(root)
            ordered.setdefault(os.path.normcase(os.fspath(path)), path)
    leases = [
        _ArtifactLease(path, exclusive=False) for path in (ordered[key] for key in sorted(ordered))
    ]
    try:
        for lease in leases:
            lease.acquire_intent()
        for lease in leases:
            lease.acquire_body()
        yield
    finally:
        errors: list[ArtifactLeaseError] = []
        for lease in reversed(leases):
            try:
                lease.release()
            except ArtifactLeaseError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
