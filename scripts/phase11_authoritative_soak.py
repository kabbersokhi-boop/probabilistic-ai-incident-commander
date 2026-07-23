#!/usr/bin/env python3
"""Resumable, source-authoritative Phase 11 workspace-inspection soak."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from paic.tui.config import load_workspace_config
from paic.tui.workspace import inspect_workspace


@dataclass(frozen=True)
class Iteration:
    index: int
    duration_seconds: float
    snapshot_sha256: str
    status: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration_sha256(config: Any) -> str:
    """Return a stable hash of the resolved workspace configuration."""
    return hashlib.sha256(
        json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _fd_count() -> int | None:
    root = Path("/proc/self/fd")
    try:
        return len(list(root.iterdir())) if root.is_dir() else None
    except OSError:
        return None


def _rss_bytes() -> int | None:
    try:
        # Linux reports KiB; macOS reports bytes. This run is Linux-oriented.
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    except (AttributeError, OSError):
        return None


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_iteration(path: Path, item: Iteration) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(item), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_iterations(path: Path) -> list[Iteration]:
    if not path.exists():
        return []
    try:
        return [
            Iteration(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot resume soak results: {exc}") from exc


def _cumulative_duration(completed: Sequence[Iteration]) -> float:
    return sum(item.duration_seconds for item in completed)


def _validate_thresholds(min_iterations: int, min_duration_seconds: float) -> None:
    if min_iterations < 0:
        raise RuntimeError("iteration threshold cannot be negative")
    if not math.isfinite(min_duration_seconds) or min_duration_seconds < 0:
        raise RuntimeError("duration threshold must be a finite non-negative number")
    if min_iterations == 0 and min_duration_seconds == 0:
        raise RuntimeError("specify a positive iteration count or duration")


def _minimums_satisfied(
    completed: Sequence[Iteration], *, min_iterations: int, min_duration_seconds: float
) -> bool:
    iteration_minimum_met = min_iterations == 0 or len(completed) >= min_iterations
    duration_minimum_met = (
        min_duration_seconds == 0
        or _cumulative_duration(completed) >= min_duration_seconds
    )
    return iteration_minimum_met and duration_minimum_met


def _publication_debris(root: Path, results: Path) -> tuple[list[str], list[str]]:
    """Report transactional debris without misclassifying artifact control locks."""
    ignored = results.resolve()
    publication_findings: list[str] = []
    control_locks: list[str] = []
    for path in root.rglob(".*"):
        if ignored == path or ignored in path.parents:
            continue
        relative = str(path.relative_to(root))
        if ".staging-" in path.name or ".backup-" in path.name:
            publication_findings.append(relative)
        elif path.name.endswith(".lock"):
            try:
                # AtomicDirectoryPublisher writes exactly a decimal process id.
                # Empty/content-bearing locks are artifact-level control files and
                # remain useful diagnostic context, but are not publication debris.
                is_publisher_lock = (
                    path.is_file() and path.read_text(encoding="utf-8").strip().isdigit()
                )
            except OSError:
                is_publisher_lock = True
            if is_publisher_lock:
                publication_findings.append(relative)
            else:
                control_locks.append(relative)
    return sorted(publication_findings), sorted(control_locks)


def run(args: argparse.Namespace) -> int:
    _validate_thresholds(args.iterations, args.duration_seconds)
    workspace = Path(args.workspace)
    config = load_workspace_config(workspace)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / "metadata.json"
    iteration_path = output / "iterations.jsonl"
    commit = _commit()
    metadata = {
        "commit": commit,
        "workspace_sha256": _sha256(workspace.resolve()),
        "resolved_configuration_sha256": _configuration_sha256(config),
    }
    if metadata_path.exists():
        prior = json.loads(metadata_path.read_text(encoding="utf-8"))
        if prior != metadata:
            raise RuntimeError(
                "existing soak results use a different source commit or workspace config"
            )
    else:
        _atomic_json(metadata_path, metadata)

    completed = _load_iterations(iteration_path)
    for _ in range(args.warmup):
        inspect_workspace(config)
    gc.collect()
    baseline_fd = _fd_count()
    baseline_rss = _rss_bytes()
    baseline_objects = len(gc.get_objects())
    tracemalloc.start()
    baseline_trace, _ = tracemalloc.get_traced_memory()
    started = time.perf_counter()
    failed = any(item.status in {"error", "missing"} for item in completed)
    while not _minimums_satisfied(
        completed,
        min_iterations=args.iterations,
        min_duration_seconds=args.duration_seconds,
    ):
        begun = time.perf_counter()
        try:
            snapshot = inspect_workspace(config)
        except Exception as exc:
            item = Iteration(
                index=len(completed) + 1,
                duration_seconds=time.perf_counter() - begun,
                snapshot_sha256="",
                status="error",
            )
            _append_iteration(iteration_path, item)
            completed.append(item)
            _atomic_json(metadata_path, metadata)
            raise RuntimeError(
                f"workspace inspection failed at iteration {item.index}: {exc}"
            ) from exc
        item = Iteration(
            index=len(completed) + 1,
            duration_seconds=time.perf_counter() - begun,
            snapshot_sha256=hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest(),
            status=snapshot.overall_status,
        )
        _append_iteration(iteration_path, item)
        completed.append(item)
        failed |= item.status in {"error", "missing"}
        _atomic_json(metadata_path, metadata)

    run_elapsed_seconds = time.perf_counter() - started
    cumulative_inspection_seconds = _cumulative_duration(completed)
    minimums_satisfied = _minimums_satisfied(
        completed,
        min_iterations=args.iterations,
        min_duration_seconds=args.duration_seconds,
    )
    gc.collect()
    final_fd = _fd_count()
    final_rss = _rss_bytes()
    final_objects = len(gc.get_objects())
    final_trace, peak_trace = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    hashes = sorted({item.snapshot_sha256 for item in completed})
    counts: dict[str, int] = {}
    for item in completed:
        counts[item.status] = counts.get(item.status, 0) + 1
    publication_debris, control_locks = _publication_debris(config.root_dir, output)
    report = {
        **metadata,
        "warmup_iterations": args.warmup,
        "minimum_iterations": args.iterations,
        "minimum_duration_seconds": args.duration_seconds,
        "iterations": len(completed),
        "elapsed_seconds": cumulative_inspection_seconds,
        "cumulative_inspection_seconds": cumulative_inspection_seconds,
        "run_elapsed_seconds": run_elapsed_seconds,
        "minimums_satisfied": minimums_satisfied,
        "unique_snapshot_hashes": hashes,
        "status_counts": counts,
        "fd_delta": None if baseline_fd is None or final_fd is None else final_fd - baseline_fd,
        "rss_delta_bytes": None
        if baseline_rss is None or final_rss is None
        else final_rss - baseline_rss,
        "gc_object_delta": final_objects - baseline_objects,
        "tracemalloc_delta_bytes": final_trace - baseline_trace,
        "tracemalloc_peak_bytes": peak_trace,
        "publication_debris": publication_debris,
        "control_lock_paths": control_locks,
    }
    _atomic_json(output / "summary.json", report)
    threshold_failure = (
        not minimums_satisfied
        or (report["fd_delta"] is not None and report["fd_delta"] > args.max_fd_delta)
        or report["gc_object_delta"] > args.max_gc_delta
        or (
            report["rss_delta_bytes"] is not None
            and report["rss_delta_bytes"] > args.max_rss_delta
        )
    )
    return 1 if failed or len(hashes) > 1 or publication_debris or threshold_failure else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="configs/tui/smoke.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--max-fd-delta", type=int, default=0)
    parser.add_argument("--max-gc-delta", type=int, default=2048)
    parser.add_argument("--max-rss-delta", type=int, default=64 * 1024 * 1024)
    try:
        return run(parser.parse_args())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"phase11 authoritative soak failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
