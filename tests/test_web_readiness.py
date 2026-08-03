import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import paic.web_readiness as web
from paic.tui.config import TUIConfigError
from paic.web_readiness import WebReadinessError, validate_bundle


def _fake_config(root: Path) -> SimpleNamespace:
    source = root / "source"
    group = SimpleNamespace(
        dataset_dir=source,
        analytics_dir=source,
        detection_dir=source,
        impact_dir=source,
        evidence_dir=source,
        investigation_dir=source,
        plan_dir=source,
        state_before_dir=source,
        state_after_dir=source,
        execution_dir=source,
        observations_dir=source,
        report_dir=source,
        run_dir=source,
        visible_dir=source,
    )
    return SimpleNamespace(
        root_dir=root,
        paths=SimpleNamespace(
            metrics=group,
            incident=group,
            remediation=group,
            recovery=group,
            evaluation=group,
        ),
    )


def test_build_bundle_uses_validated_workspace_and_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _fake_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.json").write_text(
        json.dumps({"incident_id": "demo", "api_key": "removed", "values": [1, 2]}) + "\n"
    )
    snapshot = SimpleNamespace(
        overall_status="healthy",
        workspace_id="demo",
        display_name="Synthetic demo",
        stages=[],
    )
    monkeypatch.setattr(web, "load_workspace_config", lambda _: config)
    monkeypatch.setattr(web, "inspect_workspace", lambda _: snapshot)
    output = tmp_path / "bundle"
    web.build_bundle(workspace=tmp_path / "workspace.yaml", output_dir=output)
    first = (output / "bundle.json").read_bytes()
    web.build_bundle(workspace=tmp_path / "workspace.yaml", output_dir=output)
    assert first == (output / "bundle.json").read_bytes()
    assert validate_bundle(output).files[0].content["incident_id"] == "demo"


def test_public_export_excludes_evaluator_answer_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "benchmark.answers.json").write_text('{"root_cause_id": "hidden"}\n')
    (source / "manifest.json").write_text('{"files": ["benchmark.answers.json"]}\n')
    (source / "aggregate-metrics.json").write_text('{"top1_accuracy": 1.0}\n')
    files = web._public_files(source, tmp_path, "evaluation.run")
    assert [item.path for item in files] == ["evaluation.run/source/aggregate-metrics.json"]


def test_public_sanitizer_rejects_paths_and_unsupported_values() -> None:
    assert web._safe_public({"password": "removed", "ok": True}, context="test") == {"ok": True}
    assert web._safe_public({"nested": [None, 1, 1.5, False]}, context="test") == {
        "nested": [None, 1, 1.5, False]
    }
    with pytest.raises(WebReadinessError, match="non-string"):
        web._safe_public({1: "bad"}, context="test")
    with pytest.raises(WebReadinessError, match="machine-specific"):
        web._safe_public({"path": "/private/data"}, context="test")
    with pytest.raises(WebReadinessError, match="machine-specific"):
        web._safe_public({"path": "C:\\private\\data"}, context="test")
    with pytest.raises(WebReadinessError, match="machine-specific"):
        web._safe_public({"path": "bad\x00path"}, context="test")
    with pytest.raises(WebReadinessError, match="unsupported"):
        web._safe_public({"value": object()}, context="test")


def test_build_bundle_rejects_unhealthy_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "load_workspace_config", lambda _: _fake_config(tmp_path))
    monkeypatch.setattr(
        web,
        "inspect_workspace",
        lambda _: SimpleNamespace(
            overall_status="failed", workspace_id="x", display_name="x", stages=[]
        ),
    )
    with pytest.raises(WebReadinessError, match="validate"):
        web.build_bundle(workspace=tmp_path / "workspace.yaml", output_dir=tmp_path / "bundle")


def test_public_files_reject_missing_and_malformed_sources(tmp_path: Path) -> None:
    with pytest.raises(WebReadinessError, match="regular directory"):
        web._public_files(tmp_path / "missing", tmp_path, "stage")
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(WebReadinessError, match="malformed"):
        web._public_files(source, tmp_path, "stage")


def test_public_files_rejects_symlinks_and_relative_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    (source / "linked.json").symlink_to(target)
    with pytest.raises(WebReadinessError, match="symbolic link"):
        web._public_files(source, tmp_path, "stage")
    with pytest.raises(WebReadinessError, match="escapes"):
        web._relative(tmp_path, tmp_path.parent / "outside.json")
    with pytest.raises(WebReadinessError, match="cannot inspect"):
        web._regular(tmp_path / "missing", "test")


def test_source_roots_skip_optional_paths_and_config_errors_are_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _fake_config(tmp_path)
    config.paths.evaluation.visible_dir = None
    assert "evaluation.visible" not in web._source_roots(config)
    monkeypatch.setattr(
        web, "load_workspace_config", lambda _: (_ for _ in ()).throw(TUIConfigError("bad"))
    )
    with pytest.raises(WebReadinessError, match="bad"):
        web.build_bundle(workspace=tmp_path / "workspace.yaml", output_dir=tmp_path / "bundle")


def test_build_bundle_rejects_symlink_in_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _fake_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.json").write_text("{}\n", encoding="utf-8")
    snapshot = SimpleNamespace(
        overall_status="healthy", workspace_id="demo", display_name="Demo", stages=[]
    )
    monkeypatch.setattr(web, "load_workspace_config", lambda _: config)
    monkeypatch.setattr(web, "inspect_workspace", lambda _: snapshot)
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "link").symlink_to(source)
    with pytest.raises(WebReadinessError, match="symbolic links"):
        web.build_bundle(workspace=tmp_path / "workspace.yaml", output_dir=output)


def test_web_cli_reports_success_and_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = {
        "schema_version": "1.0",
        "bundle_kind": "paic-public-demo",
        "disclaimer": "Synthetic demonstration data only; values are not production claims.",
        "workspace_id": "demo",
        "display_name": "Demo",
        "source_bindings": {},
        "lifecycle": {},
        "detection": {},
        "operations": {},
        "investigation": {},
        "impact": {},
        "remediation": {},
        "recovery": {},
        "evaluation": {},
        "stages": [],
        "files": [],
    }
    (bundle / "bundle.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
    digest = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps({"bundle_sha256": digest}) + "\n")
    manifest_hash = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(f"{digest}  bundle.json\n{manifest_hash}  manifest.json\n")
    assert web.main(["validate", "--bundle-dir", str(bundle)]) == 0
    assert web.main(["validate", "--bundle-dir", str(tmp_path / "missing")]) == 1


def test_web_bundle_rejects_tampering_and_unexpected_files(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = {
        "schema_version": "1.0",
        "bundle_kind": "paic-public-demo",
        "disclaimer": "Synthetic demonstration data only; values are not production claims.",
        "workspace_id": "demo",
        "display_name": "Demo",
        "source_bindings": {},
        "lifecycle": {},
        "detection": {},
        "operations": {},
        "investigation": {},
        "impact": {},
        "remediation": {},
        "recovery": {},
        "evaluation": {},
        "stages": [],
        "files": [],
    }
    (bundle / "bundle.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
    digest = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    manifest = {"bundle_sha256": digest}
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    manifest_hash = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(f"{digest}  bundle.json\n{manifest_hash}  manifest.json\n")
    assert validate_bundle(bundle).workspace_id == "demo"
    (bundle / "bundle.json").write_text(
        (bundle / "bundle.json").read_text().replace("Demo", "Changed")
    )
    with pytest.raises(WebReadinessError):
        validate_bundle(bundle)


def test_web_bundle_rejects_bad_checksum_and_manifest_binding(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = {
        "schema_version": "1.0",
        "bundle_kind": "paic-public-demo",
        "disclaimer": "Synthetic demonstration data only; values are not production claims.",
        "workspace_id": "demo",
        "display_name": "Demo",
        "source_bindings": {},
        "lifecycle": {},
        "detection": {},
        "operations": {},
        "investigation": {},
        "impact": {},
        "remediation": {},
        "recovery": {},
        "evaluation": {},
        "stages": [],
        "files": [],
    }
    (bundle / "bundle.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
    (bundle / "manifest.json").write_text(json.dumps({"bundle_sha256": "0" * 64}) + "\n")
    (bundle / "SHA256SUMS").write_text("0  bundle.json\n0  manifest.json\n")
    with pytest.raises(WebReadinessError, match="checksum"):
        validate_bundle(bundle)
    bundle_json_hash = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    manifest_hash = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(
        f"{bundle_json_hash}  bundle.json\n{manifest_hash}  manifest.json\n"
    )
    with pytest.raises(WebReadinessError, match="bind"):
        validate_bundle(bundle)


def test_public_bundle_schema_excludes_environment_and_credentials() -> None:
    schema = json.loads(Path("schemas/web-readiness-bundle.schema.json").read_text())
    serialized = json.dumps(schema, sort_keys=True).lower()
    assert "api_key" not in serialized
    assert "environment" not in serialized
