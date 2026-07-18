from __future__ import annotations

import json
from pathlib import Path

from paic.cli import main


def test_evidence_cli_build_validate_and_summary(
    tmp_path: Path,
    repo_root: Path,
    impact_smoke_dataset_dir: Path,
    capsys: object,
) -> None:
    output = tmp_path / "evidence"
    assert (
        main(
            [
                "evidence",
                "build",
                "--dataset-dir",
                str(impact_smoke_dataset_dir),
                "--config",
                str(repo_root / "configs" / "evidence" / "smoke.yaml"),
                "--output-dir",
                str(output),
                "--format",
                "json",
            ]
        )
        == 0
    )
    build_payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert build_payload["success"] is True
    assert (
        main(
            [
                "evidence",
                "validate",
                "--evidence-dir",
                str(output),
                "--dataset-dir",
                str(impact_smoke_dataset_dir),
                "--format",
                "json",
            ]
        )
        == 0
    )
    validation = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert validation["valid"] is True
    assert main(["evidence", "summary", "--evidence-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert summary["manifest"]["evidence_record_count"] > 100
