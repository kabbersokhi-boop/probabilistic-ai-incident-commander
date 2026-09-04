from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_soak_evidence.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("soak_evidence_freshness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    root.mkdir()
    commit = "a" * 40
    workspace = "b" * 64
    configuration = "c" * 64
    snapshot = "d" * 64
    records = [
        {
            "index": index,
            "duration_seconds": 1.0,
            "snapshot_sha256": snapshot,
            "status": "healthy",
            "configured_stage_count": 9,
            "healthy_stage_count": 9,
            "authoritative_stage_count": 9,
        }
        for index in range(1, 26)
    ]
    metadata = {
        "commit": commit,
        "mode": "inspection",
        "workspace_sha256": workspace,
        "resolved_configuration_sha256": configuration,
    }
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (root / "iterations.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )
    (root / "summary.json").write_text(
        json.dumps(
            {
                **metadata,
                "iterations": 25,
                "minimum_iterations": 25,
                "minimum_duration_seconds": 0.0,
                "cumulative_inspection_seconds": 25.0,
                "minimums_satisfied": True,
                "resumed": False,
                "unique_snapshot_hashes": [snapshot],
                "status_counts": {"healthy": 25},
                "configured_stage_counts": [9],
                "healthy_stage_counts": [9],
                "authoritative_stage_counts": [9],
                "fd_delta": 0,
                "gc_object_delta": 0,
                "rss_delta_bytes": 0,
                "publication_debris": [],
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])  # type: ignore[untyped-decorator]
def test_resumed_requires_exact_boolean_false(tmp_path: Path, value: object) -> None:
    module = _module()
    root = _bundle(tmp_path)
    summary = json.loads((root / "summary.json").read_text())
    summary["resumed"] = value
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(module.EvidenceValidationError, match=r"explicit boolean|fresh"):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="inspection")


def test_missing_resumed_fails_closed(tmp_path: Path) -> None:
    module = _module()
    root = _bundle(tmp_path)
    summary = json.loads((root / "summary.json").read_text())
    del summary["resumed"]
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(module.EvidenceValidationError, match="explicit boolean"):
        module.validate_bundle(root, expected_commit="a" * 40, expected_mode="inspection")
