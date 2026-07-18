from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.cli import main


def test_validate_command_succeeds(spec_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["validate", "--spec-dir", str(spec_dir)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Project contracts are valid" in captured.out


def test_validate_json_output_is_machine_readable(
    spec_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["validate", "--spec-dir", str(spec_dir), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["valid"] is True
    assert payload["incident_count"] == 5
    assert payload["issues"] == []


def test_summary_command_lists_incident_families(
    spec_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["summary", "--spec-dir", str(spec_dir)])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["incident_count"] == 5
    assert "payment_configuration" in payload["incident_families"]


def test_export_schemas_writes_thirty_documents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "schemas"
    exit_code = main(["export-schemas", "--output-dir", str(output_dir)])
    capsys.readouterr()
    assert exit_code == 0
    assert {path.name for path in output_dir.glob("*.json")} == {
        "project.schema.json",
        "evaluation.schema.json",
        "safety.schema.json",
        "incident.schema.json",
        "simulation-config.schema.json",
        "dataset-manifest.schema.json",
        "analytics-config.schema.json",
        "analytics-manifest.schema.json",
        "detection-config.schema.json",
        "detection-manifest.schema.json",
        "impact-config.schema.json",
        "impact-manifest.schema.json",
        "evidence-config.schema.json",
        "evidence-manifest.schema.json",
        "tool-request.schema.json",
        "tool-response.schema.json",
        "tool-error.schema.json",
        "investigation-config.schema.json",
        "investigation-request.schema.json",
        "investigation-report.schema.json",
        "investigation-manifest.schema.json",
        "investigation-evaluation-case.schema.json",
        "remediation-config.schema.json",
        "remediation-control-state.schema.json",
        "remediation-proposal.schema.json",
        "remediation-plan.schema.json",
        "remediation-approval-decision.schema.json",
        "remediation-execution-request.schema.json",
        "remediation-execution-receipt.schema.json",
        "remediation-artifact-manifest.schema.json",
        "recovery-config.schema.json",
        "recovery-observation-set.schema.json",
        "recovery-report.schema.json",
        "recovery-lifecycle-state.schema.json",
        "recovery-lifecycle-event.schema.json",
        "recovery-artifact-manifest.schema.json",
        "recovery-observation-artifact-manifest.schema.json",
        "evaluation-config.schema.json",
        "evaluation-run.schema.json",
        "evaluation-comparison.schema.json",
        "evaluation-adversarial-result.schema.json",
    }


def test_validate_load_error_supports_text_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", "--spec-dir", str(tmp_path)]) == 2
    assert "LOAD ERROR" in capsys.readouterr().err

    assert main(["validate", "--spec-dir", str(tmp_path), "--format", "json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert "load_error" in payload


def test_summary_load_error_returns_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", "--spec-dir", str(tmp_path)]) == 2
    assert "LOAD ERROR" in capsys.readouterr().err
