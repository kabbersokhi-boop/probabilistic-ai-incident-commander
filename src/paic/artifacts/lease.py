"""Per-artifact shared-reader/exclusive-writer leases."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast


class ArtifactLeaseError(RuntimeError):
    """Raised when a safe artifact lease cannot be acquired."""


T = TypeVar("T")


def _lease_path(root: Path) -> Path:
    return root.absolute().parent / f".{root.name}.lease"


@contextlib.contextmanager
def artifact_lease(root: str | Path, *, exclusive: bool) -> Iterator[None]:
    """Hold a persistent per-root kernel lease for a complete logical read/write."""
    target = Path(root)
    parent = target.absolute().parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ArtifactLeaseError("artifact lease parent is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
    path = _lease_path(target)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ArtifactLeaseError("artifact coordination file must be regular")
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactLeaseError("artifact coordination file must be regular")
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    except ArtifactLeaseError:
        raise
    except OSError as exc:
        raise ArtifactLeaseError(f"cannot acquire artifact lease: {exc}") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def artifact_reader(func: Callable[..., T]) -> Callable[..., T]:
    """Protect a public loader's complete multi-file read with a shared lease."""

    @wraps(func)
    def wrapped(root: str | Path, *args: Any, **kwargs: Any) -> T:
        with artifact_lease(root, exclusive=False):
            return func(root, *args, **kwargs)

    return cast(Callable[..., T], wrapped)


@contextlib.contextmanager
def artifact_reader_leases(roots: Sequence[str | Path | None]) -> Iterator[None]:
    """Acquire shared leases in canonical absolute-path order to prevent deadlock."""
    ordered: dict[str, Path] = {}
    for root in roots:
        if root is not None:
            path = Path(root).absolute()
            ordered.setdefault(os.fspath(path), path)
    with ExitStack() as stack:
        for path in (ordered[key] for key in sorted(ordered)):
            stack.enter_context(artifact_lease(path, exclusive=False))
        yield
