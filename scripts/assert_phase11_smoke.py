#!/usr/bin/env python3
"""Fail closed unless the configured Phase 11 smoke workspace is authoritative."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from paic.artifacts.lease import artifact_reader_leases
from paic.recovery.artifact import load_recovery
from paic.recovery.observations import load_observations, observation_manifest_sha256
from paic.remediation.artifact import load_execution, manifest_sha256
from paic.tui.config import load_workspace_config
from paic.tui.workspace import inspect_workspace


def assert_authoritative_smoke(workspace: str | Path) -> None:
    """Assert the complete configured control room and recovery lineage are green."""
    config = load_workspace_config(workspace)
    snapshot = inspect_workspace(config)
    if (
        snapshot.overall_status != "healthy"
        or snapshot.configured_stage_count != 9
        or snapshot.healthy_stage_count != 9
        or len(snapshot.stages) != 9
        or any(not stage.authoritative for stage in snapshot.stages)
    ):
        raise RuntimeError(
            "Phase 11 smoke workspace is not nine-stage healthy and authoritative: "
            + snapshot.model_dump_json()
        )

    execution_dir = config.paths.remediation.execution_dir
    observations_dir = config.paths.recovery.observations_dir
    report_dir = config.paths.recovery.report_dir
    analytics_dir = config.paths.recovery.analytics_dir
    required = {
        "execution": execution_dir,
        "observations": observations_dir,
        "report": report_dir,
        "analytics": analytics_dir,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise RuntimeError(
            f"Phase 11 smoke workspace is missing required paths: {', '.join(missing)}"
        )

    execution_root = cast(Path, execution_dir)
    observations_root = cast(Path, observations_dir)
    report_root = cast(Path, report_dir)
    analytics_root = cast(Path, analytics_dir)
    with artifact_reader_leases((execution_root, observations_root, report_root, analytics_root)):
        execution = load_execution(execution_root)
        observations = load_observations(
            observations_root, analytics_dir=analytics_root, execution_dir=execution_root
        )
        report = load_recovery(report_root).report
        execution_manifest = manifest_sha256(execution_root)
        observation_manifest = observation_manifest_sha256(observations_root)
        if (
            observations.incident_id != execution.receipt.incident_id
            or observations.executed_at != execution.receipt.executed_at
            or observations.execution_receipt_sha256 != execution.receipt.receipt_sha256
            or observations.execution_manifest_sha256 != execution_manifest
            or report.incident_id != execution.receipt.incident_id
            or report.execution_receipt_sha256 != execution.receipt.receipt_sha256
            or report.execution_manifest_sha256 != execution_manifest
            or report.observation_manifest_sha256 != observation_manifest
        ):
            raise RuntimeError("Phase 11 smoke recovery artifacts are not bound to its execution")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    try:
        assert_authoritative_smoke(args.workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Phase 11 authoritative smoke assertion failed: {exc}") from exc
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
