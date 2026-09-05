from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from paic.analytics.validation import validate_analytics_directory
from paic.simulator.io import load_manifest as load_dataset_manifest
from paic.simulator.validation import validate_dataset_directory
from paic.tui.models import WorkspaceConfig, WorkspacePaths, WorkspaceSnapshot
from paic.tui.render import Renderer
from paic.tui.workspace import inspect_workspace


def _tui_snapshot(path: Path, *, stage: str, root: Path) -> WorkspaceSnapshot:
    paths = {"metrics": {"dataset_dir": path}}
    if stage == "analytics":
        paths = {"metrics": {"dataset_dir": root / "dataset", "analytics_dir": path}}
    config = WorkspaceConfig(
        workspace_id="corruption-matrix",
        display_name="Corruption matrix",
        root_dir=root,
        paths=WorkspacePaths.model_validate(paths),
    )
    snapshot = inspect_workspace(config)
    rendered = Renderer(color=False, unicode=False).overview(snapshot)
    assert "\x1b" not in rendered
    assert "Traceback" not in rendered
    return snapshot


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "case",
    ["truncated_manifest", "missing_success", "changed_table", "symlinked_table"],
)
def test_dataset_corruption_matrix_fails_closed(
    tmp_path: Path, smoke_dataset_dir: Path, case: str
) -> None:
    copied = tmp_path / case
    shutil.copytree(smoke_dataset_dir, copied)
    if case == "truncated_manifest":
        (copied / "manifest.json").write_text("{", encoding="utf-8")
    elif case == "missing_success":
        (copied / "_SUCCESS").unlink()
    elif case == "changed_table":
        manifest = load_dataset_manifest(copied)
        table = copied / manifest.tables[0].relative_path
        table.write_bytes(table.read_bytes() + b"tampered")
    else:
        manifest = load_dataset_manifest(copied)
        table = copied / manifest.tables[0].relative_path
        table.unlink()
        table.symlink_to(tmp_path / "outside.parquet")

    report = validate_dataset_directory(copied)
    assert not report.valid
    assert _tui_snapshot(copied, stage="dataset", root=tmp_path).overall_status == "error"


def test_analytics_source_manifest_mismatch_fails_closed(
    tmp_path: Path, analytics_smoke_dir: Path, smoke_dataset_dir: Path
) -> None:
    copied = tmp_path / "analytics-mismatch"
    shutil.copytree(analytics_smoke_dir, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (copied / "_SUCCESS").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
    )
    report = validate_analytics_directory(copied, dataset_dir=smoke_dataset_dir)
    assert not report.valid
    assert _tui_snapshot(copied, stage="analytics", root=tmp_path).overall_status == "error"
