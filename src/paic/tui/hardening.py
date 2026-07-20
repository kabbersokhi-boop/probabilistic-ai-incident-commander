"""Deterministic corruption and endurance helpers for Phase 11 certification."""

from __future__ import annotations

import gc
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from paic.tui.models import WorkspaceSnapshot

SnapshotBuilder = Callable[[], WorkspaceSnapshot]


@dataclass(frozen=True)
class EnduranceReport:
    iterations: int
    elapsed_seconds: float
    unique_snapshot_hashes: tuple[str, ...]
    file_descriptor_delta: int | None
    gc_object_delta: int

    @property
    def deterministic(self) -> bool:
        return len(self.unique_snapshot_hashes) == 1

    def as_json(self) -> str:
        return (
            json.dumps(
                {
                    "iterations": self.iterations,
                    "elapsed_seconds": round(self.elapsed_seconds, 6),
                    "unique_snapshot_hashes": list(self.unique_snapshot_hashes),
                    "deterministic": self.deterministic,
                    "file_descriptor_delta": self.file_descriptor_delta,
                    "gc_object_delta": self.gc_object_delta,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


def _fd_count() -> int | None:
    proc = Path("/proc/self/fd")
    if not proc.is_dir():
        return None
    try:
        return len(list(proc.iterdir()))
    except OSError:
        return None


def run_snapshot_endurance(builder: SnapshotBuilder, *, iterations: int) -> EnduranceReport:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    gc.collect()
    objects_before = len(gc.get_objects())
    fds_before = _fd_count()
    hashes: set[str] = set()
    started = time.perf_counter()
    for _ in range(iterations):
        payload = builder().model_dump_json().encode()
        hashes.add(hashlib.sha256(payload).hexdigest())
    elapsed = time.perf_counter() - started
    gc.collect()
    objects_after = len(gc.get_objects())
    fds_after = _fd_count()
    return EnduranceReport(
        iterations=iterations,
        elapsed_seconds=elapsed,
        unique_snapshot_hashes=tuple(sorted(hashes)),
        file_descriptor_delta=(
            None if fds_before is None or fds_after is None else fds_after - fds_before
        ),
        gc_object_delta=objects_after - objects_before,
    )


def assert_endurance_health(
    report: EnduranceReport,
    *,
    max_fd_delta: int = 0,
    max_gc_object_delta: int = 256,
) -> None:
    if not report.deterministic:
        raise RuntimeError("snapshot output changed during the endurance run")
    if report.file_descriptor_delta is not None and report.file_descriptor_delta > max_fd_delta:
        raise RuntimeError("file descriptor growth exceeded the configured limit")
    if report.gc_object_delta > max_gc_object_delta:
        raise RuntimeError("garbage-collected object growth exceeded the configured limit")
