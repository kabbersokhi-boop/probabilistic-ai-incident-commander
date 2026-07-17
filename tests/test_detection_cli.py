from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.cli import main


def test_detection_build_validate_and_summary_cli(
    repo_root: Path,
    analytics_smoke_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "detection"
    config = repo_root / "configs" / "detection" / "smoke.yaml"
    assert (
        main(
            [
                "detection",
                "build",
                "--analytics-dir",
                str(analytics_smoke_dir),
                "--config",
                str(config),
                "--output-dir",
                str(output),
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["manifest"]["detection_id"] == "commerce-detection-smoke"
    assert payload["manifest"]["observation_count"] == 2
    assert payload["summary"]["quality"]["failed"] == 0

    assert (
        main(
            [
                "detection",
                "validate",
                "--detection-dir",
                str(output),
                "--analytics-dir",
                str(analytics_smoke_dir),
            ]
        )
        == 0
    )
    assert "Detection validation: passed" in capsys.readouterr().out

    assert main(["detection", "summary", "--detection-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["manifest"]["detection_id"] == "commerce-detection-smoke"
    assert summary["anomaly_observations"] == 0


def test_detection_cli_reports_build_validation_and_load_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "detection",
                "build",
                "--analytics-dir",
                str(tmp_path / "missing-analytics"),
                "--config",
                str(tmp_path / "missing-config.yaml"),
                "--output-dir",
                str(tmp_path / "detection"),
            ]
        )
        == 2
    )
    assert "DETECTION ERROR" in capsys.readouterr().err

    assert (
        main(
            [
                "detection",
                "validate",
                "--detection-dir",
                str(tmp_path / "missing"),
                "--format",
                "json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["issues"][0]["code"] == "detection.load"

    assert main(["detection", "summary", "--detection-dir", str(tmp_path / "missing")]) == 2
    assert "DETECTION ERROR" in capsys.readouterr().err
