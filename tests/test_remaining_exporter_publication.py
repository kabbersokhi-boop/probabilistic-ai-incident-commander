from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from paic.artifacts.publication import AtomicDirectoryPublisher
from paic.detection.io import DetectionIOError, export_detection
from paic.detection.types import DetectionBuildResult
from paic.detection.validation import validate_detection_directory
from paic.evidence.io import EvidenceIOError, export_evidence
from paic.evidence.types import EvidenceBuildResult
from paic.evidence.validation import validate_evidence_directory
from paic.impact.io import ImpactIOError, export_impact
from paic.impact.types import ImpactBuildResult
from paic.impact.validation import validate_impact_directory
from paic.tui.models import WorkspaceConfig, WorkspacePaths
from paic.tui.render import Renderer
from paic.tui.workspace import inspect_workspace

FailureHook = Callable[[str], None]


def _publisher_type(hook: FailureHook) -> type[AtomicDirectoryPublisher]:
    class InjectedPublisher(AtomicDirectoryPublisher):
        def __init__(self, target: str | Path, *, overwrite: bool = False) -> None:
            super().__init__(target, overwrite=overwrite, failure_hook=hook)

    return InjectedPublisher


def _assert_debris_gone(parent: Path, name: str) -> None:
    assert not list(parent.glob(f".{name}.staging-*"))
    assert not list(parent.glob(f".{name}.backup-*"))


def _assert_tui_failure(root: Path, artifact: Path, *, stage: str, source: Path) -> None:
    paths: dict[str, dict[str, Path]]
    if stage == "detection":
        paths = {"metrics": {"analytics_dir": source, "detection_dir": artifact}}
    elif stage == "impact":
        paths = {"incident": {"dataset_dir": source, "impact_dir": artifact}}
    else:
        paths = {"incident": {"dataset_dir": source, "evidence_dir": artifact}}
    snapshot = inspect_workspace(
        WorkspaceConfig(
            workspace_id="migration-corruption",
            display_name="Migration corruption",
            root_dir=root,
            paths=WorkspacePaths.model_validate(paths),
        )
    )
    rendered = Renderer(color=False, unicode=False).overview(snapshot)
    assert snapshot.overall_status == "error"
    assert "Traceback" not in rendered
    assert "\x1b" not in rendered
    assert str(root) not in rendered


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "case",
    [
        "truncated_manifest",
        "missing_success",
        "changed_table",
        "symlinked_table",
        "source_mismatch",
    ],
)
def test_detection_corruption_matrix_fails_closed_with_safe_tui_output(
    tmp_path: Path, detection_smoke_dir: Path, analytics_smoke_dir: Path, case: str
) -> None:
    copied = tmp_path / case
    shutil.copytree(detection_smoke_dir, copied)
    manifest_path = copied / "manifest.json"
    if case == "truncated_manifest":
        manifest_path.write_text("{", encoding="utf-8")
    elif case == "missing_success":
        (copied / "_SUCCESS").unlink()
    elif case == "changed_table":
        table = next((copied / "tables").glob("*.parquet"))
        table.write_bytes(table.read_bytes() + b"tampered")
    elif case == "symlinked_table":
        table = next((copied / "tables").glob("*.parquet"))
        table.unlink()
        table.symlink_to(tmp_path / "outside.parquet")
    else:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["source_analytics_manifest_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        (copied / "_SUCCESS").write_text(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
        )
    report = validate_detection_directory(copied, analytics_dir=analytics_smoke_dir)
    assert not report.valid
    _assert_tui_failure(tmp_path, copied, stage="detection", source=analytics_smoke_dir)


def test_detection_publication_failure_and_success_paths(
    tmp_path: Path,
    detection_smoke_result: DetectionBuildResult,
    analytics_smoke_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "detection"
    export_detection(detection_smoke_result, output)
    previous = (output / "manifest.json").read_bytes()

    def before_commit(point: str) -> None:
        if point == "payload-written":
            raise RuntimeError("before commit")

    monkeypatch.setattr(
        "paic.detection.io.AtomicDirectoryPublisher", _publisher_type(before_commit)
    )
    with pytest.raises(RuntimeError, match="before commit"):
        export_detection(detection_smoke_result, output, overwrite=True)
    assert (output / "manifest.json").read_bytes() == previous
    assert validate_detection_directory(output, analytics_dir=analytics_smoke_dir).valid
    _assert_debris_gone(tmp_path, "detection")

    monkeypatch.setattr("paic.detection.io.AtomicDirectoryPublisher", AtomicDirectoryPublisher)
    export_detection(detection_smoke_result, output, overwrite=True)
    assert validate_detection_directory(output, analytics_dir=analytics_smoke_dir).valid
    _assert_debris_gone(tmp_path, "detection")

    real = tmp_path / "real-detection"
    real.mkdir()
    link = tmp_path / "detection-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(DetectionIOError, match="symbolic link"):
        export_detection(detection_smoke_result, link, overwrite=True)
    file_target = tmp_path / "detection-file"
    file_target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(DetectionIOError, match="artifact target must be a directory"):
        export_detection(detection_smoke_result, file_target, overwrite=True)

    def after_visibility(point: str) -> None:
        if point == "new-committed":
            raise RuntimeError("after visibility")

    monkeypatch.setattr(
        "paic.detection.io.AtomicDirectoryPublisher", _publisher_type(after_visibility)
    )
    with pytest.raises(DetectionIOError, match="committed but durability is uncertain"):
        export_detection(detection_smoke_result, output, overwrite=True)
    assert validate_detection_directory(output, analytics_dir=analytics_smoke_dir).valid


def test_impact_publication_failure_and_success_paths(
    tmp_path: Path,
    impact_smoke_result: ImpactBuildResult,
    impact_smoke_dataset_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "impact"
    export_impact(impact_smoke_result, output)
    previous = (output / "manifest.json").read_bytes()

    def before_commit(point: str) -> None:
        if point == "payload-written":
            raise RuntimeError("before commit")

    monkeypatch.setattr("paic.impact.io.AtomicDirectoryPublisher", _publisher_type(before_commit))
    with pytest.raises(RuntimeError, match="before commit"):
        export_impact(impact_smoke_result, output, overwrite=True)
    assert (output / "manifest.json").read_bytes() == previous
    assert validate_impact_directory(output, dataset_dir=impact_smoke_dataset_dir).valid
    _assert_debris_gone(tmp_path, "impact")

    monkeypatch.setattr("paic.impact.io.AtomicDirectoryPublisher", AtomicDirectoryPublisher)
    export_impact(impact_smoke_result, output, overwrite=True)
    assert validate_impact_directory(output, dataset_dir=impact_smoke_dataset_dir).valid
    _assert_debris_gone(tmp_path, "impact")

    real = tmp_path / "real-impact"
    real.mkdir()
    link = tmp_path / "impact-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ImpactIOError, match="symbolic link"):
        export_impact(impact_smoke_result, link, overwrite=True)
    file_target = tmp_path / "impact-file"
    file_target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ImpactIOError, match="artifact target must be a directory"):
        export_impact(impact_smoke_result, file_target, overwrite=True)

    def after_visibility(point: str) -> None:
        if point == "new-committed":
            raise RuntimeError("after visibility")

    monkeypatch.setattr(
        "paic.impact.io.AtomicDirectoryPublisher", _publisher_type(after_visibility)
    )
    with pytest.raises(ImpactIOError, match="committed but durability is uncertain"):
        export_impact(impact_smoke_result, output, overwrite=True)
    assert validate_impact_directory(output, dataset_dir=impact_smoke_dataset_dir).valid


def test_evidence_publication_failure_and_success_paths(
    tmp_path: Path,
    evidence_smoke_result: EvidenceBuildResult,
    impact_smoke_dataset_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "evidence"
    export_evidence(evidence_smoke_result, output)
    previous = (output / "manifest.json").read_bytes()

    def before_commit(point: str) -> None:
        if point == "payload-written":
            raise RuntimeError("before commit")

    monkeypatch.setattr("paic.evidence.io.AtomicDirectoryPublisher", _publisher_type(before_commit))
    with pytest.raises(RuntimeError, match="before commit"):
        export_evidence(evidence_smoke_result, output, overwrite=True)
    assert (output / "manifest.json").read_bytes() == previous
    assert validate_evidence_directory(output, dataset_dir=impact_smoke_dataset_dir).valid
    _assert_debris_gone(tmp_path, "evidence")

    monkeypatch.setattr("paic.evidence.io.AtomicDirectoryPublisher", AtomicDirectoryPublisher)
    export_evidence(evidence_smoke_result, output, overwrite=True)
    assert validate_evidence_directory(output, dataset_dir=impact_smoke_dataset_dir).valid
    _assert_debris_gone(tmp_path, "evidence")

    real = tmp_path / "real-evidence"
    real.mkdir()
    link = tmp_path / "evidence-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(EvidenceIOError, match="symbolic link"):
        export_evidence(evidence_smoke_result, link, overwrite=True)
    file_target = tmp_path / "evidence-file"
    file_target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(EvidenceIOError, match="artifact target must be a directory"):
        export_evidence(evidence_smoke_result, file_target, overwrite=True)

    def after_visibility(point: str) -> None:
        if point == "new-committed":
            raise RuntimeError("after visibility")

    monkeypatch.setattr(
        "paic.evidence.io.AtomicDirectoryPublisher", _publisher_type(after_visibility)
    )
    with pytest.raises(EvidenceIOError, match="committed but durability is uncertain"):
        export_evidence(evidence_smoke_result, output, overwrite=True)
    assert validate_evidence_directory(output, dataset_dir=impact_smoke_dataset_dir).valid
