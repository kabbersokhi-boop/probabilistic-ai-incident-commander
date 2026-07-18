from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from paic.recovery import cli as recovery_cli
from test_recovery_unit import config, observations, sha


def test_recovery_cli_evaluate_validate_summary_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bound_observations = observations()
    execution = SimpleNamespace(
        receipt=SimpleNamespace(
            receipt_sha256=sha("receipt"),
            incident_id="incident-smoke",
            executed_at=bound_observations.executed_at,
        )
    )
    monkeypatch.setattr(recovery_cli, "load_execution", lambda _: execution)
    monkeypatch.setattr(recovery_cli, "manifest_sha256", lambda _: sha("execution-manifest"))
    monkeypatch.setattr(recovery_cli, "observation_manifest_sha256", lambda _: sha("observations"))
    monkeypatch.setattr(recovery_cli, "load_observations", lambda *_, **__: bound_observations)
    monkeypatch.setattr(recovery_cli, "validate_recovery", lambda *_, **__: [])
    config_path = tmp_path / "config.json"
    config_path.write_text(config().model_dump_json(), encoding="utf-8")
    artifact = tmp_path / "recovery"
    state_store = tmp_path / "state"

    assert (
        recovery_cli.dispatch_recovery(
            argparse.Namespace(
                recovery_command="evaluate",
                config=config_path,
                observations_dir=tmp_path / "observations",
                analytics_dir=tmp_path / "analytics",
                execution_dir=tmp_path / "execution",
                output_dir=artifact,
                overwrite=False,
            )
        )
        == 0
    )
    assert '"decision": "recovered"' in capsys.readouterr().out

    assert (
        recovery_cli.dispatch_recovery(
            argparse.Namespace(
                recovery_command="validate",
                recovery_dir=artifact,
                observations_dir=tmp_path / "observations",
                analytics_dir=tmp_path / "analytics",
                execution_dir=tmp_path / "execution",
            )
        )
        == 0
    )
    assert '"valid": true' in capsys.readouterr().out

    assert (
        recovery_cli.dispatch_recovery(
            argparse.Namespace(recovery_command="summary", recovery_dir=artifact)
        )
        == 0
    )
    assert '"decision": "recovered"' in capsys.readouterr().out

    assert (
        recovery_cli.dispatch_recovery(
            argparse.Namespace(
                recovery_command="state",
                recovery_state_command="apply",
                recovery_dir=artifact,
                state_store=state_store,
            )
        )
        == 0
    )
    assert '"generation": 1' in capsys.readouterr().out

    assert (
        recovery_cli.dispatch_recovery(
            argparse.Namespace(
                recovery_command="state",
                recovery_state_command="validate",
                state_store=state_store,
            )
        )
        == 0
    )
    assert '"valid": true' in capsys.readouterr().out

    assert (
        recovery_cli.dispatch_recovery(
            argparse.Namespace(
                recovery_command="state",
                recovery_state_command="show",
                state_store=state_store,
            )
        )
        == 0
    )
    assert '"status": "recovered"' in capsys.readouterr().out
