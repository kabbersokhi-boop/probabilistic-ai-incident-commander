from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "phase11_authoritative_soak.py"


def _load_module() -> ModuleType:
    """Load the script as a registered module so dataclass metadata resolves."""
    spec = importlib.util.spec_from_file_location("phase11_authoritative_soak", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_combined_thresholds_require_count_and_cumulative_duration() -> None:
    module = _load_module()
    iteration = module.Iteration

    completed = [
        iteration(index=index, duration_seconds=10.0, snapshot_sha256="same", status="healthy")
        for index in range(1, 26)
    ]
    assert not module._minimums_satisfied(completed, min_iterations=25, min_duration_seconds=300.0)

    completed.extend(
        iteration(index=index, duration_seconds=10.0, snapshot_sha256="same", status="healthy")
        for index in range(26, 31)
    )
    assert module._minimums_satisfied(completed, min_iterations=25, min_duration_seconds=300.0)


def test_resume_uses_prior_iteration_durations() -> None:
    module = _load_module()
    iteration = module.Iteration
    completed = [
        iteration(index=1, duration_seconds=1200.0, snapshot_sha256="same", status="healthy"),
        iteration(index=2, duration_seconds=600.0, snapshot_sha256="same", status="healthy"),
    ]

    assert module._cumulative_duration(completed) == 1800.0
    assert module._minimums_satisfied(completed, min_iterations=2, min_duration_seconds=1800.0)
    assert not module._minimums_satisfied(completed, min_iterations=3, min_duration_seconds=1800.0)


def test_single_threshold_modes_remain_supported() -> None:
    module = _load_module()
    iteration = module.Iteration
    completed = [iteration(index=1, duration_seconds=2.0, snapshot_sha256="same", status="healthy")]

    assert module._minimums_satisfied(completed, min_iterations=1, min_duration_seconds=0.0)
    assert module._minimums_satisfied(completed, min_iterations=0, min_duration_seconds=2.0)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("iterations", "duration"),
    [(-1, 0.0), (0, -1.0), (0, float("inf")), (0, 0.0)],
)
def test_invalid_thresholds_fail_closed(iterations: int, duration: float) -> None:
    module = _load_module()
    with pytest.raises(RuntimeError):
        module._validate_thresholds(iterations, duration)
