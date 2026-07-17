from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.cli import main


def test_simulate_validate_and_summarize_cli(
    repo_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "smoke"
    config = repo_root / "configs" / "simulation" / "smoke.yaml"
    assert (
        main(
            [
                "simulate",
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
    generated = json.loads(capsys.readouterr().out)
    assert generated["success"] is True
    assert generated["profile"]["business"]["orders"] > 0

    assert main(["dataset", "validate", "--dataset-dir", str(output)]) == 0
    assert "Dataset validation: passed" in capsys.readouterr().out

    assert main(["dataset", "summary", "--dataset-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["manifest"]["simulation_id"] == "commerce-smoke-baseline"


def test_simulate_cli_reports_configuration_and_output_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "dataset"
    assert (
        main(
            [
                "simulate",
                "--config",
                str(tmp_path / "missing.yaml"),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert "SIMULATION ERROR" in capsys.readouterr().err

    assert main(["dataset", "summary", "--dataset-dir", str(output)]) == 2
    assert "DATASET ERROR" in capsys.readouterr().err


def test_dataset_validate_json_reports_missing_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "dataset",
                "validate",
                "--dataset-dir",
                str(tmp_path / "missing"),
                "--format",
                "json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["issues"][0]["code"] == "dataset.load"
