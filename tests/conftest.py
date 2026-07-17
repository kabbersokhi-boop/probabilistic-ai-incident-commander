from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("POLARS_MAX_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import pytest

from paic.contracts.loader import ContractBundle, load_contract_bundle
from paic.simulator.config import SimulationConfig, load_simulation_config
from paic.simulator.engine import simulate
from paic.simulator.types import SimulationResult


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def spec_dir(repo_root: Path) -> Path:
    return repo_root / "specs"


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def bundle(spec_dir: Path) -> ContractBundle:
    return load_contract_bundle(spec_dir)


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def smoke_config(repo_root: Path) -> SimulationConfig:
    return load_simulation_config(repo_root / "configs" / "simulation" / "smoke.yaml")


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def smoke_result(smoke_config: SimulationConfig) -> SimulationResult:
    return simulate(smoke_config)


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def rich_result(smoke_config: SimulationConfig) -> SimulationResult:
    scale = smoke_config.scale.model_copy(update={"base_sessions_per_hour": 10.0})
    behavior = smoke_config.behavior.model_copy(
        update={"base_return_rate": 0.55, "refund_completion_rate": 0.98}
    )
    config = smoke_config.model_copy(
        update={
            "simulation_id": "commerce-rich-baseline",
            "seed": smoke_config.seed + 99,
            "duration_days": 6,
            "scale": scale,
            "behavior": behavior,
        }
    )
    return simulate(config)
