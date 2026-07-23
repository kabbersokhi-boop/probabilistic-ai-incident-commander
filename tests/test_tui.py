from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paic.artifacts.lease import ArtifactLeaseError
from paic.tui import workspace as workspace_module
from paic.tui.app import TUIApplication
from paic.tui.cli import dispatch_tui
from paic.tui.config import (
    TUIConfigError,
    load_workspace_config,
    write_workspace_template,
)
from paic.tui.models import (
    StageSnapshot,
    StageStatus,
    WorkspaceConfig,
    WorkspacePaths,
    WorkspaceSnapshot,
)
from paic.tui.render import Renderer, strip_ansi
from paic.tui.workspace import inspect_workspace


def _snapshot(status: StageStatus = "healthy") -> WorkspaceSnapshot:
    stage = StageSnapshot(
        key="dataset",
        title="Synthetic data",
        status=status,
        summary="Source data is valid.",
        authoritative=True,
        details=["Rows: 10"],
    )
    return WorkspaceSnapshot(
        workspace_id="test-room",
        display_name="Test room",
        root_dir="/tmp",
        overall_status=status,
        configured_stage_count=1,
        healthy_stage_count=1 if status == "healthy" else 0,
        stages=[stage],
    )


def test_workspace_config_resolves_only_paths_inside_root(tmp_path: Path) -> None:
    config_path = tmp_path / "workspace.yaml"
    config_path.write_text(
        """schema_version: \"1.0\"
workspace_id: test-room
display_name: Test room
root_dir: .
paths:
  metrics:
    dataset_dir: artifacts/data
  incident: {}
  remediation: {}
  recovery: {}
  evaluation: {}
""",
        encoding="utf-8",
    )
    config = load_workspace_config(config_path)
    assert config.paths.metrics.dataset_dir == (tmp_path / "artifacts/data").resolve()

    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("artifacts/data", "../outside"),
        encoding="utf-8",
    )
    with pytest.raises(TUIConfigError, match="escapes root_dir"):
        load_workspace_config(config_path)


def test_workspace_config_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.yaml"
    write_workspace_template(
        target,
        workspace_id="test-room",
        display_name="Test room",
        root_dir=".",
    )
    link = tmp_path / "workspace.yaml"
    link.symlink_to(target)
    with pytest.raises(TUIConfigError, match="non-symlink"):
        load_workspace_config(link)


def test_workspace_config_rejects_symlinked_artifact_component(tmp_path: Path) -> None:
    real = tmp_path / "real-artifacts"
    real.mkdir()
    (tmp_path / "artifacts").symlink_to(real, target_is_directory=True)
    config_path = tmp_path / "workspace.yaml"
    config_path.write_text(
        """schema_version: \"1.0\"
workspace_id: test-room
display_name: Test room
root_dir: .
paths:
  metrics:
    dataset_dir: artifacts/data
  incident: {}
  remediation: {}
  recovery: {}
  evaluation: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(TUIConfigError, match="symbolic link"):
        load_workspace_config(config_path)


def test_empty_workspace_is_safe_and_explains_missing_configuration(
    tmp_path: Path,
) -> None:
    config = WorkspaceConfig(
        workspace_id="empty-room",
        display_name="Empty room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    snapshot = inspect_workspace(config)
    assert snapshot.overall_status == "not_configured"
    assert all(stage.status == "not_configured" for stage in snapshot.stages)


def test_renderer_is_deterministic_without_color() -> None:
    rendered = Renderer(color=False, unicode=False).overview(_snapshot())
    assert "PAIC TERMINAL CONTROL ROOM" in rendered
    assert "Synthetic data" in rendered
    assert "\033[" not in rendered
    assert strip_ansi(Renderer(color=True).overview(_snapshot())) != ""


def test_interactive_app_handles_detail_refresh_help_and_quit(tmp_path: Path) -> None:
    config = WorkspaceConfig(
        workspace_id="test-room",
        display_name="Test room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    input_stream = io.StringIO("1\n\nh\n\nr\nq\n")
    output_stream = io.StringIO()
    calls = 0

    def build(_: WorkspaceConfig) -> WorkspaceSnapshot:
        nonlocal calls
        calls += 1
        return _snapshot()

    app = TUIApplication(
        config,
        input_stream=input_stream,
        output_stream=output_stream,
        snapshot_builder=build,
        color=False,
        unicode=False,
    )
    assert app.run() == 0
    assert calls == 2
    output = output_stream.getvalue()
    assert "How to read this screen" in output
    assert "What this means" in output


def test_tui_validate_returns_nonzero_for_broken_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = WorkspaceConfig(
        workspace_id="test-room",
        display_name="Test room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    snapshot = _snapshot("error")
    monkeypatch.setattr("paic.tui.cli._load_snapshot", lambda path: (config, snapshot))
    args: Any = type(
        "Args",
        (),
        {
            "tui_command": "validate",
            "workspace": tmp_path / "unused.yaml",
            "format": "json",
        },
    )()
    assert dispatch_tui(args) == 1
    assert '"overall_status": "error"' in capsys.readouterr().out


def test_configured_missing_path_is_an_error(tmp_path: Path) -> None:
    config = WorkspaceConfig(
        workspace_id="missing-room",
        display_name="Missing room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate(
            {"metrics": {"dataset_dir": tmp_path / "does-not-exist"}}
        ),
    )
    snapshot = inspect_workspace(config)
    assert snapshot.overall_status == "error"
    assert snapshot.stages[0].status == "missing"


def test_interactive_app_treats_eof_as_clean_exit(tmp_path: Path) -> None:
    config = WorkspaceConfig(
        workspace_id="test-room",
        display_name="Test room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    output = io.StringIO()
    app = TUIApplication(
        config,
        input_stream=io.StringIO(""),
        output_stream=output,
        snapshot_builder=lambda _: _snapshot(),
        color=False,
        unicode=False,
    )
    assert app.run() == 0
    assert "PAIC TERMINAL CONTROL ROOM" in output.getvalue()


def test_workspace_template_rejects_invalid_values_and_overwrite(
    tmp_path: Path,
) -> None:
    target = tmp_path / "workspace.yaml"
    with pytest.raises(TUIConfigError, match="invalid workspace template values"):
        write_workspace_template(
            target,
            workspace_id="INVALID ID",
            display_name="Test room",
            root_dir=".",
        )
    write_workspace_template(
        target,
        workspace_id="test-room",
        display_name="Test room",
        root_dir=".",
    )
    with pytest.raises(TUIConfigError, match="already exists"):
        write_workspace_template(
            target,
            workspace_id="test-room",
            display_name="Test room",
            root_dir=".",
        )


def test_renderer_detail_explains_problems() -> None:
    stage = StageSnapshot(
        key="recovery",
        title="Recovery verification",
        status="error",
        summary="Recovery evidence failed validation.",
        issues=["source binding mismatch"],
    )
    rendered = Renderer(color=False, unicode=False).detail(stage)
    assert "Problems to fix" in rendered
    assert "source binding mismatch" in rendered


def test_workspace_inspection_covers_authoritative_stage_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = {}
    for name in (
        "dataset",
        "analytics",
        "detection",
        "impact",
        "evidence",
        "investigation",
        "remediation",
        "state_before",
        "state_after",
        "execution",
        "recovery_observations",
        "recovery_analytics",
        "recovery",
        "evaluation",
        "evaluation_visible",
        "evaluation_answers",
    ):
        path = tmp_path / name
        path.mkdir()
        paths[name] = path
    for name in ("investigation_config", "evaluation_predictions", "evaluation_config"):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        paths[name] = path

    valid = SimpleNamespace(valid=True, issues=[])
    monkeypatch.setattr("paic.tui.workspace.validate_dataset_directory", lambda path: valid)
    monkeypatch.setattr(
        "paic.tui.workspace.validate_analytics_directory", lambda *args, **kwargs: valid
    )
    monkeypatch.setattr(
        "paic.tui.workspace.validate_detection_directory", lambda *args, **kwargs: valid
    )
    monkeypatch.setattr(
        "paic.tui.workspace.validate_impact_directory", lambda *args, **kwargs: valid
    )
    monkeypatch.setattr(
        "paic.tui.workspace.validate_evidence_directory", lambda *args, **kwargs: valid
    )
    monkeypatch.setattr(
        "paic.tui.workspace.load_evidence",
        lambda path: SimpleNamespace(
            manifest=SimpleNamespace(
                source_dataset_manifest_sha256="dataset",
                source_analytics_manifest_sha256="analytics",
                source_detection_manifest_sha256="detection",
                source_impact_manifest_sha256="impact",
            )
        ),
    )
    monkeypatch.setattr("paic.tui.workspace.validate_investigation", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "paic.tui.workspace.load_investigation",
        lambda path: SimpleNamespace(
            report=SimpleNamespace(
                incident_id="inc-001",
                status="concluded",
                confidence=0.9,
                selected_hypothesis_id="h-1",
                tool_trace=[1],
            )
        ),
    )
    monkeypatch.setattr("paic.tui.workspace.replay_investigation", lambda *args, **kwargs: None)
    plan = SimpleNamespace(
        remediation_id="rem-001", status="approved", risk_level="low", actions=[1]
    )
    monkeypatch.setattr("paic.tui.workspace.validate_plan", lambda *args, **kwargs: [])
    monkeypatch.setattr("paic.tui.workspace.load_plan", lambda path: SimpleNamespace(plan=plan))
    monkeypatch.setattr("paic.tui.workspace.validate_execution", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "paic.tui.workspace.load_execution",
        lambda path: SimpleNamespace(
            receipt=SimpleNamespace(status="executed", action_receipts=[1])
        ),
    )
    monkeypatch.setattr("paic.tui.workspace.validate_recovery", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "paic.tui.workspace.load_recovery",
        lambda path: SimpleNamespace(
            report=SimpleNamespace(
                decision="recovered", metric_evaluations=[1], recovery_id="rec-001"
            )
        ),
    )
    evaluation_run = SimpleNamespace(
        config=SimpleNamespace(run_id="eval-001"),
        results=[1],
        aggregate=SimpleNamespace(
            model_dump=lambda mode: {
                "top_1_accuracy": 1.0,
                "top_3_accuracy": 1.0,
                "brier_score": 0.1,
                "safety_pass_rate": 1.0,
            }
        ),
    )
    monkeypatch.setattr(
        "paic.tui.workspace.replay_evaluation", lambda *args, **kwargs: evaluation_run
    )

    config = WorkspaceConfig(
        workspace_id="full-room",
        display_name="Full room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate(
            {
                "metrics": {
                    "dataset_dir": paths["dataset"],
                    "analytics_dir": paths["analytics"],
                    "detection_dir": paths["detection"],
                },
                "incident": {
                    "dataset_dir": paths["dataset"],
                    "analytics_dir": paths["analytics"],
                    "detection_dir": paths["detection"],
                    "impact_dir": paths["impact"],
                    "evidence_dir": paths["evidence"],
                    "investigation_dir": paths["investigation"],
                    "investigation_config": paths["investigation_config"],
                },
                "remediation": {
                    "plan_dir": paths["remediation"],
                    "state_before_dir": paths["state_before"],
                    "state_after_dir": paths["state_after"],
                    "execution_dir": paths["execution"],
                },
                "recovery": {
                    "observations_dir": paths["recovery_observations"],
                    "analytics_dir": paths["recovery_analytics"],
                    "report_dir": paths["recovery"],
                },
                "evaluation": {
                    "run_dir": paths["evaluation"],
                    "visible_dir": paths["evaluation_visible"],
                    "answers_dir": paths["evaluation_answers"],
                    "predictions": paths["evaluation_predictions"],
                    "config": paths["evaluation_config"],
                },
            }
        ),
    )
    snapshot = inspect_workspace(config)
    assert snapshot.overall_status == "healthy"
    assert snapshot.configured_stage_count == 9
    assert snapshot.healthy_stage_count == 9


def test_workspace_safety_helpers_report_unsafe_and_failed_inspection(tmp_path: Path) -> None:
    regular_file = tmp_path / "artifact"
    regular_file.write_text("x", encoding="utf-8")
    blocked = workspace_module._path_ready("dataset", regular_file)
    assert blocked is not None and blocked.status == "error"
    issue = SimpleNamespace(code="bad", table="events", message="invalid")
    assert workspace_module._issue_text(issue) == "bad.events: invalid"
    message_only = SimpleNamespace(code=None, table=None, message="broken")
    assert workspace_module._issue_text(message_only) == "broken"
    assert workspace_module._issue_text(SimpleNamespace()) == "namespace()"
    failed = workspace_module._safe_stage(
        "dataset", tmp_path, lambda path: (_ for _ in ()).throw(ValueError("boom"))
    )
    assert failed.status == "error"
    assert "boom" in failed.issues


def test_recovery_stage_exposes_authoritative_binding_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recovery validation failures remain visible rather than becoming generic TUI errors."""
    observations = tmp_path / "observations"
    analytics = tmp_path / "analytics"
    execution = tmp_path / "execution"
    report = tmp_path / "report"
    for path in (observations, analytics, execution, report):
        path.mkdir()
    monkeypatch.setattr(
        "paic.tui.workspace.validate_recovery",
        lambda *_, **__: ["observation artifact is bound to another execution"],
    )
    monkeypatch.setattr(
        "paic.tui.workspace.load_recovery",
        lambda _: SimpleNamespace(
            report=SimpleNamespace(
                decision="recovered", metric_evaluations=[1], recovery_id="recovery-1"
            )
        ),
    )
    config = WorkspaceConfig(
        workspace_id="recovery-room",
        display_name="Recovery room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate(
            {
                "remediation": {"execution_dir": execution},
                "recovery": {
                    "observations_dir": observations,
                    "analytics_dir": analytics,
                    "report_dir": report,
                },
            }
        ),
    )

    stage = next(item for item in inspect_workspace(config).stages if item.key == "recovery")
    assert stage.status == "error"
    assert not stage.authoritative
    assert stage.summary == "Recovery evidence failed validation."
    assert stage.issues == ["observation artifact is bound to another execution"]


def test_workspace_lease_failure_is_a_controlled_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    config = WorkspaceConfig(
        workspace_id="lease-room",
        display_name="Lease room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate({"metrics": {"dataset_dir": artifact}}),
    )
    monkeypatch.setattr(
        workspace_module,
        "artifact_reader_leases",
        lambda _: (_ for _ in ()).throw(
            ArtifactLeaseError("artifact coordination file must be regular")
        ),
    )
    snapshot = inspect_workspace(config)
    assert snapshot.overall_status == "error"
    assert snapshot.healthy_stage_count == 0
    assert snapshot.stages[0].status == "error"
    assert "coordination file" in snapshot.stages[0].issues[0]


@pytest.mark.parametrize("command", ["run", "snapshot", "validate"])  # type: ignore[untyped-decorator]
def test_cli_lease_failure_has_no_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setattr(
        "paic.tui.cli._load_snapshot",
        lambda _: (_ for _ in ()).throw(ArtifactLeaseError("flock unavailable")),
    )
    args: Any = type(
        "Args",
        (),
        {
            "tui_command": command,
            "workspace": tmp_path / "unused.yaml",
            "format": "json",
            "no_color": True,
            "ascii": True,
        },
    )()
    assert dispatch_tui(args) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert '"error": "flock unavailable"' in captured.err


def test_tui_snapshot_json_returns_success_for_healthy_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = WorkspaceConfig(
        workspace_id="test-room",
        display_name="Test room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    snapshot = _snapshot()
    monkeypatch.setattr("paic.tui.cli._load_snapshot", lambda path: (config, snapshot))
    args: Any = type(
        "Args",
        (),
        {
            "tui_command": "snapshot",
            "workspace": tmp_path / "unused.yaml",
            "format": "json",
            "no_color": True,
            "ascii": True,
        },
    )()
    assert dispatch_tui(args) == 0
    assert (
        '"overall_status": "healthy"' in capsys.readouterr().out
    )  # Phase 11 final hardening regressions


def _assert_unbound_stage_is_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    stage_key: str,
    paths: dict[str, object],
    validator_name: str,
) -> None:
    artifact = tmp_path / stage_key
    artifact.mkdir()
    resolved = WorkspacePaths.model_validate(paths)
    section = resolved.metrics if stage_key != "impact" else resolved.incident
    field = f"{stage_key}_dir"
    resolved = resolved.model_copy(
        update={
            "metrics" if stage_key != "impact" else "incident": section.model_copy(
                update={field: artifact}
            )
        }
    )
    report = type("Report", (), {"valid": True, "issues": []})()
    monkeypatch.setattr(f"paic.tui.workspace.{validator_name}", lambda *a, **k: report)
    config = WorkspaceConfig(
        workspace_id="authority-room",
        display_name="Authority room",
        root_dir=tmp_path,
        paths=resolved,
    )
    stage = next(item for item in inspect_workspace(config).stages if item.key == stage_key)
    assert stage.status == "warning"
    assert not stage.authoritative


def test_analytics_without_dataset_is_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _assert_unbound_stage_is_warning(
        monkeypatch,
        tmp_path,
        stage_key="analytics",
        paths={"metrics": {"analytics_dir": "analytics"}},
        validator_name="validate_analytics_directory",
    )


def test_detection_without_analytics_is_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _assert_unbound_stage_is_warning(
        monkeypatch,
        tmp_path,
        stage_key="detection",
        paths={"metrics": {"detection_dir": "detection"}},
        validator_name="validate_detection_directory",
    )


def test_impact_without_dataset_is_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _assert_unbound_stage_is_warning(
        monkeypatch,
        tmp_path,
        stage_key="impact",
        paths={"incident": {"impact_dir": "impact"}},
        validator_name="validate_impact_directory",
    )


def test_evidence_requires_the_exact_declared_source_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    evidence = tmp_path / "evidence"
    dataset = tmp_path / "dataset"
    evidence.mkdir()
    dataset.mkdir()
    report = type("Report", (), {"valid": True, "issues": []})()
    manifest = SimpleNamespace(
        source_dataset_manifest_sha256="a" * 64,
        source_analytics_manifest_sha256=None,
        source_detection_manifest_sha256=None,
        source_impact_manifest_sha256=None,
    )
    monkeypatch.setattr("paic.tui.workspace.validate_evidence_directory", lambda *a, **k: report)
    monkeypatch.setattr(
        "paic.tui.workspace.load_evidence",
        lambda *a, **k: SimpleNamespace(manifest=manifest),
    )
    without_source = WorkspaceConfig(
        workspace_id="evidence-room",
        display_name="Evidence room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate({"incident": {"evidence_dir": evidence}}),
    )
    stage = next(
        item for item in inspect_workspace(without_source).stages if item.key == "evidence"
    )
    assert stage.status == "warning"
    assert not stage.authoritative

    with_source = without_source.model_copy(
        update={
            "paths": without_source.paths.model_copy(
                update={
                    "incident": without_source.paths.incident.model_copy(
                        update={"dataset_dir": dataset}
                    )
                }
            )
        }
    )
    stage = next(item for item in inspect_workspace(with_source).stages if item.key == "evidence")
    assert stage.status == "healthy"
    assert stage.authoritative


def _remediation_workspace(
    tmp_path: Path, *, include_after: bool, missing_after: bool = False
) -> WorkspaceConfig:
    investigation = tmp_path / "investigation"
    dataset = tmp_path / "dataset"
    plan = tmp_path / "plan"
    before = tmp_path / "before"
    execution = tmp_path / "execution"
    for path in (investigation, dataset, plan, before, execution):
        path.mkdir()
    config_path = tmp_path / "investigation.yaml"
    config_path.write_text("schema_version: '1.0'\n", encoding="utf-8")
    after: Path | None = None
    if include_after:
        after = tmp_path / "after"
        if not missing_after:
            after.mkdir()
    return WorkspaceConfig(
        workspace_id="remediation-room",
        display_name="Remediation room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate(
            {
                "incident": {
                    "investigation_dir": investigation,
                    "investigation_config": config_path,
                    "dataset_dir": dataset,
                },
                "remediation": {
                    "plan_dir": plan,
                    "state_before_dir": before,
                    "state_after_dir": after,
                    "execution_dir": execution,
                },
            }
        ),
    )


def _stub_remediation(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    plan = SimpleNamespace(
        remediation_id="rem-1", status="awaiting_approval", risk_level="low", actions=[]
    )
    receipt = SimpleNamespace(status="executed", action_receipts=[])
    monkeypatch.setattr("paic.tui.workspace.validate_plan", lambda *a, **k: [])
    monkeypatch.setattr("paic.tui.workspace.load_plan", lambda *a, **k: SimpleNamespace(plan=plan))
    monkeypatch.setattr("paic.tui.workspace.validate_execution", lambda *a, **k: [])
    monkeypatch.setattr(
        "paic.tui.workspace.load_execution",
        lambda *a, **k: SimpleNamespace(receipt=receipt),
    )


def test_execution_without_after_state_is_not_authoritative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_remediation(monkeypatch)
    config = _remediation_workspace(tmp_path, include_after=False)
    stage = next(item for item in inspect_workspace(config).stages if item.key == "remediation")
    assert stage.status == "warning"
    assert not stage.authoritative
    assert "before/after" in stage.summary


def test_complete_execution_provenance_is_authoritative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_remediation(monkeypatch)
    config = _remediation_workspace(tmp_path, include_after=True)
    stage = next(item for item in inspect_workspace(config).stages if item.key == "remediation")
    assert stage.status == "healthy"
    assert stage.authoritative


def test_configured_missing_after_state_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_remediation(monkeypatch)
    config = _remediation_workspace(tmp_path, include_after=True, missing_after=True)
    stage = next(item for item in inspect_workspace(config).stages if item.key == "remediation")
    assert stage.status == "error"
    assert not stage.authoritative
    assert any("Missing path" in issue for issue in stage.issues)


def test_terminal_renderer_neutralizes_control_sequences() -> None:
    import unicodedata

    from paic.tui.render import sanitize_terminal_text

    malicious = "safe\x1b]52;c;ZXhmaWx0cmF0ZQ==\x07\x1b[2J\a\rreplace\nnext\u202eend"
    clean = sanitize_terminal_text(malicious)
    assert "\n" not in clean
    assert "\r" not in clean
    assert "\x1b" not in clean
    assert "\x07" not in clean
    assert "\u202e" not in clean
    assert not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in clean)

    stage = StageSnapshot(
        key="dataset",
        title=malicious,
        status="error",
        summary=malicious,
        path=malicious,
        details=[malicious],
        issues=[malicious],
    )
    snapshot = WorkspaceSnapshot(
        workspace_id="safe-room",
        display_name=malicious,
        root_dir=".",
        overall_status="error",
        configured_stage_count=1,
        healthy_stage_count=0,
        stages=[stage],
    )
    rendered = Renderer(color=False, unicode=False).overview(snapshot)
    rendered += Renderer(color=False, unicode=False).detail(stage)
    for control in ("\x1b", "\x07", "\r", "\u202e"):
        assert control not in rendered


def test_workspace_template_safe_dump_and_atomic_overwrite(tmp_path: Path) -> None:
    import yaml

    target = tmp_path / "nested" / "workspace.yaml"
    display_name = 'Control: "Room"\nSecond line'
    root_dir = "odd path/#hash"
    write_workspace_template(
        target,
        workspace_id="safe-room",
        display_name=display_name,
        root_dir=root_dir,
    )
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded["display_name"] == display_name
    assert loaded["root_dir"] == root_dir

    write_workspace_template(
        target,
        workspace_id="safe-room",
        display_name="Replacement room",
        root_dir=".",
        overwrite=True,
    )
    replaced = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert replaced["display_name"] == "Replacement room"
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_workspace_template_rejects_symlink_parent_and_nonregular_target(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(TUIConfigError, match="parent traverses"):
        write_workspace_template(
            linked / "workspace.yaml",
            workspace_id="safe-room",
            display_name="Safe room",
            root_dir=".",
        )

    directory_target = tmp_path / "directory-target"
    directory_target.mkdir()
    with pytest.raises(TUIConfigError, match="regular file"):
        write_workspace_template(
            directory_target,
            workspace_id="safe-room",
            display_name="Safe room",
            root_dir=".",
            overwrite=True,
        )


def test_snapshot_paths_are_workspace_relative_and_errors_are_redacted(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "private" / "missing"
    config = WorkspaceConfig(
        workspace_id="redaction-room",
        display_name="Redaction room",
        root_dir=tmp_path,
        paths=WorkspacePaths.model_validate({"metrics": {"dataset_dir": missing}}),
    )
    snapshot = inspect_workspace(config)
    dataset = next(item for item in snapshot.stages if item.key == "dataset")
    serialized = snapshot.model_dump_json()
    assert snapshot.root_dir == "."
    assert dataset.path == "private/missing"
    assert str(tmp_path) not in serialized
