#!/usr/bin/env python3
"""Validate a Phase 11 evidence bundle independently of its producer."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


class EvidenceValidationError(RuntimeError):
    """Raised when evidence is incomplete or internally inconsistent."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceValidationError(f"cannot read {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"{path.name} must contain an object")
    return value


def _digest(value: Any, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise EvidenceValidationError(f"{name} must be a lowercase SHA-256 digest")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EvidenceValidationError(f"{name} is missing or invalid")
    return float(value)


def validate_bundle(
    output_dir: str | Path,
    *,
    expected_commit: str,
    expected_mode: str,
    min_iterations: int = 25,
    min_duration_seconds: float = 0.0,
    max_fd_delta: int | None = 0,
    max_gc_delta: int | None = 2048,
    max_rss_delta: int | None = 64 * 1024 * 1024,
    reject_resumed_endurance: bool = True,
) -> dict[str, Any]:
    root = Path(output_dir)
    metadata = _load(root / "metadata.json")
    summary = _load(root / "summary.json")
    if metadata.get("commit") != expected_commit or summary.get("commit") != expected_commit:
        raise EvidenceValidationError("commit provenance does not match the expected head")
    if metadata.get("mode") != expected_mode or summary.get("mode") != expected_mode:
        raise EvidenceValidationError("mode provenance does not match the requested mode")
    for key in ("workspace_sha256", "resolved_configuration_sha256"):
        _digest(metadata.get(key), f"metadata.{key}")
        if summary.get(key) != metadata.get(key):
            raise EvidenceValidationError(f"summary.{key} does not match metadata")

    if "resumed" not in summary or type(summary["resumed"]) is not bool:
        raise EvidenceValidationError("summary.resumed must be an explicit boolean")
    if reject_resumed_endurance and summary["resumed"] is not False:
        raise EvidenceValidationError("release evidence must be a fresh run")

    try:
        lines = (root / "iterations.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvidenceValidationError("cannot read iterations.jsonl") from exc
    if not lines or any(not line.strip() for line in lines):
        raise EvidenceValidationError("iterations.jsonl is empty or contains a blank record")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise EvidenceValidationError(f"invalid iterations.jsonl record {index}") from exc
        if not isinstance(value, dict):
            raise EvidenceValidationError(f"iterations.jsonl record {index} is not an object")
        records.append(value)

    if summary.get("iterations") != len(records) or len(records) < min_iterations:
        raise EvidenceValidationError("summary iteration count does not match the complete JSONL")
    if [item.get("index") for item in records] != list(range(1, len(records) + 1)):
        raise EvidenceValidationError("iteration indexes are not contiguous")
    if any(item.get("status") != "healthy" for item in records):
        raise EvidenceValidationError("iteration status is not healthy")
    hashes = {item.get("snapshot_sha256") for item in records}
    if len(hashes) != 1 or not isinstance(next(iter(hashes)), str):
        raise EvidenceValidationError("iteration snapshot hashes are not deterministic")
    snapshot = next(iter(hashes))
    _digest(snapshot, "iteration snapshot_sha256")
    if summary.get("unique_snapshot_hashes") != [snapshot]:
        raise EvidenceValidationError("summary snapshot hashes do not match JSONL")

    durations: list[float] = []
    for item in records:
        duration = _number(item.get("duration_seconds"), "iteration duration")
        if duration < 0:
            raise EvidenceValidationError("iteration duration is invalid")
        durations.append(duration)
        for field in ("configured_stage_count", "healthy_stage_count", "authoritative_stage_count"):
            if item.get(field) != 9:
                raise EvidenceValidationError(f"iteration {field} is not 9")
    if Counter(item["status"] for item in records) != summary.get("status_counts"):
        raise EvidenceValidationError("summary status counts do not match JSONL")
    for field, key in (
        ("configured_stage_count", "configured_stage_counts"),
        ("healthy_stage_count", "healthy_stage_counts"),
        ("authoritative_stage_count", "authoritative_stage_counts"),
    ):
        if summary.get(key) != [9] or sorted({item[field] for item in records}) != [9]:
            raise EvidenceValidationError(f"summary {key} does not match JSONL")

    cumulative = sum(durations)
    reported = _number(summary.get("cumulative_inspection_seconds"), "cumulative duration")
    if not math.isclose(cumulative, reported, rel_tol=1e-9, abs_tol=1e-6):
        raise EvidenceValidationError("cumulative duration does not match JSONL")
    if summary.get("minimum_iterations") != min_iterations:
        raise EvidenceValidationError("minimum iteration threshold does not match request")
    if summary.get("minimum_duration_seconds") != float(min_duration_seconds):
        raise EvidenceValidationError("minimum duration threshold does not match request")
    minimums = len(records) >= min_iterations and cumulative >= min_duration_seconds
    if summary.get("minimums_satisfied") is not minimums or not minimums:
        raise EvidenceValidationError("minimum thresholds are not satisfied")
    if summary.get("publication_debris") != []:
        raise EvidenceValidationError("publication debris is present")

    for key, limit in (
        ("fd_delta", max_fd_delta),
        ("gc_object_delta", max_gc_delta),
        ("rss_delta_bytes", max_rss_delta),
    ):
        if limit is not None and _number(summary.get(key), key) > limit:
            raise EvidenceValidationError(f"{key} exceeds its configured limit")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--mode", choices=("inspection", "endurance"), required=True)
    parser.add_argument("--min-iterations", type=int, default=25)
    parser.add_argument("--min-duration-seconds", type=float, default=0.0)
    parser.add_argument("--max-fd-delta", type=int, default=0)
    parser.add_argument("--max-gc-delta", type=int, default=2048)
    parser.add_argument("--max-rss-delta", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args()
    try:
        summary = validate_bundle(
            args.output_dir,
            expected_commit=args.expected_commit,
            expected_mode=args.mode,
            min_iterations=args.min_iterations,
            min_duration_seconds=args.min_duration_seconds,
            max_fd_delta=args.max_fd_delta,
            max_gc_delta=args.max_gc_delta,
            max_rss_delta=args.max_rss_delta,
        )
    except EvidenceValidationError as exc:
        print(f"phase11 evidence validation failed: {exc}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
