from __future__ import annotations

from paic.tui.hardening import assert_endurance_health, run_snapshot_endurance
from paic.tui.models import StageSnapshot, WorkspaceSnapshot


def _snapshot() -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        workspace_id="endurance-room",
        display_name="Endurance room",
        root_dir=".",
        overall_status="healthy",
        configured_stage_count=1,
        healthy_stage_count=1,
        stages=[
            StageSnapshot(
                key="dataset",
                title="Synthetic data",
                status="healthy",
                summary="Healthy",
                authoritative=True,
            )
        ],
    )


def test_snapshot_endurance_is_deterministic_and_leak_bounded() -> None:
    report = run_snapshot_endurance(_snapshot, iterations=1_000)
    assert report.deterministic
    assert report.iterations == 1_000
    assert len(report.unique_snapshot_hashes) == 1
    assert_endurance_health(report, max_gc_object_delta=512)


def test_snapshot_endurance_detects_nondeterminism() -> None:
    counter = 0

    def builder() -> WorkspaceSnapshot:
        nonlocal counter
        counter += 1
        snapshot = _snapshot()
        return snapshot.model_copy(update={"display_name": f"Run {counter}"})

    report = run_snapshot_endurance(builder, iterations=3)
    assert not report.deterministic
