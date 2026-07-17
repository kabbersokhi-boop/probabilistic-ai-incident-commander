from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.cli import main


def test_analytics_build_validate_and_summary_cli(
    repo_root: Path,
    smoke_dataset_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "analytics"
    config = repo_root / "configs" / "analytics" / "smoke.yaml"
    assert (
        main(
            [
                "analytics",
                "build",
                "--dataset-dir",
                str(smoke_dataset_dir),
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
    assert payload["manifest"]["metric_count"] == 43
    assert payload["summary"]["quality"]["failed"] == 0

    assert (
        main(
            [
                "analytics",
                "validate",
                "--analytics-dir",
                str(output),
                "--dataset-dir",
                str(smoke_dataset_dir),
            ]
        )
        == 0
    )
    assert "Analytics validation: passed" in capsys.readouterr().out

    assert main(["analytics", "summary", "--analytics-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["manifest"]["analytics_id"] == "commerce-analytics-smoke"
    assert summary["table_rows"]["metric_observations"] > 0


def test_analytics_cli_reports_build_and_load_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "analytics",
                "build",
                "--dataset-dir",
                str(tmp_path / "missing-source"),
                "--config",
                str(tmp_path / "missing-config.yaml"),
                "--output-dir",
                str(tmp_path / "analytics"),
            ]
        )
        == 2
    )
    assert "ANALYTICS ERROR" in capsys.readouterr().err

    assert (
        main(
            [
                "analytics",
                "validate",
                "--analytics-dir",
                str(tmp_path / "missing"),
                "--format",
                "json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["issues"][0]["code"] == "analytics.load"

    assert main(["analytics", "summary", "--analytics-dir", str(tmp_path / "missing")]) == 2
    assert "ANALYTICS ERROR" in capsys.readouterr().err
