from __future__ import annotations

import json
from pathlib import Path

from paic.cli import main


def test_impact_cli_build_validate_and_summary(
    tmp_path: Path,
    impact_smoke_dataset_dir: Path,
    repo_root: Path,
    capsys: object,
) -> None:
    output = tmp_path / "impact"
    assert (
        main(
            [
                "impact",
                "build",
                "--dataset-dir",
                str(impact_smoke_dataset_dir),
                "--config",
                str(repo_root / "configs" / "impact" / "smoke.yaml"),
                "--output-dir",
                str(output),
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["success"] is True
    assert (
        main(
            [
                "impact",
                "validate",
                "--impact-dir",
                str(output),
                "--dataset-dir",
                str(impact_smoke_dataset_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    assert main(["impact", "summary", "--impact-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert summary["exposed_customers"] > 0
