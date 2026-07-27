from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_phase11_evidence.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_phase11_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(tmp_path: Path, *, count: int = 25, mode: str = "inspection") -> Path:
    root = tmp_path / "evidence"
    root.mkdir(parents=True)
    commit = "a" * 40
    workspace = "b" * 64
    configuration = "c" * 64
    snapshot = "d" * 64
    records = [
        {
            "index": index,
            "duration_seconds": 2.0,
            "snapshot_sha256": snapshot,
            "status": "healthy",
            "configured_stage_count": 9,
            "healthy_stage_count": 9,
            "authoritative_stage_count": 9,
        }
        for index in range(1, count + 1)
    ]
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "commit": commit,
                "mode": mode,
                "workspace_sha256": workspace,
                "resolved_configuration_sha256": configuration,
            }
        ),
        encoding="utf-8",
    )
    (root / "iterations.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    (root / "summary.json").write_text(
        json.dumps(
            {
                "commit": commit,
                "mode": mode,
                "workspace_sha256": workspace,
                "resolved_configuration_sha256": configuration,
                "iterations": count,
                "minimum_iterations": 25,
                "minimum_duration_seconds": 0.0,
                "cumulative_inspection_seconds": count * 2.0,
                "minimums_satisfied": True,
                "resumed": False,
                "unique_snapshot_hashes": [snapshot],
                "status_counts": {"healthy": count},
                "configured_stage_counts": [9],
                "healthy_stage_counts": [9],
                "authoritative_stage_counts": [9],
                "fd_delta": 0,
                "gc_object_delta": 10,
                "rss_delta_bytes": 1024,
                "publication_debris": [],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_valid_bundle_is_reconciled(tmp_path: Path) -> None:
    module = _module()
    summary = module.validate_bundle(
        _bundle(tmp_path), expected_commit="a" * 40, expected_mode="inspection"
    )
    assert summary["iterations"] == 25


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "mutation, message",
    [
        (lambda records: records.pop(), "iteration count"),
        (lambda records: records.__setitem__(0, {**records[0], "index": 2}), "contiguous"),
        (lambda records: records.__setitem__(0, {**records[0], "status": "error"}), "status"),
        (
            lambda records: records.__setitem__(0, {**records[0], "duration_seconds": 3.0}),
            "duration",
        ),
    ],
)
def test_jsonl_mutations_fail_closed(tmp_path: Path, mutation: object, message: str) -> None:
    module = _module()
    root = _bundle(tmp_path)
    records = [json.loads(line) for line in (root / "iterations.jsonl").read_text().splitlines()]
    mutation(records)  # type: ignore[operator]
    (root / "iterations.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(module.EvidenceValidationError, match=message):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="inspection")


def test_truncated_jsonl_and_stale_summary_fail(tmp_path: Path) -> None:
    module = _module()
    root = _bundle(tmp_path)
    with (root / "iterations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"index": 26')
    with pytest.raises(module.EvidenceValidationError, match="invalid"):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="inspection")

    root = _bundle(tmp_path / "stale")
    summary = json.loads((root / "summary.json").read_text())
    summary["iterations"] = 24
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(module.EvidenceValidationError, match="iteration count"):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="inspection")


def test_endurance_resume_is_rejected_and_resource_nan_is_invalid(tmp_path: Path) -> None:
    module = _module()
    root = _bundle(tmp_path, mode="endurance")
    summary = json.loads((root / "summary.json").read_text())
    summary["resumed"] = True
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(module.EvidenceValidationError, match="fresh"):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="endurance")

    root = _bundle(tmp_path / "nan", mode="inspection")
    summary = json.loads((root / "summary.json").read_text())
    summary["fd_delta"] = float("nan")
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(module.EvidenceValidationError, match="fd_delta"):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="inspection")
