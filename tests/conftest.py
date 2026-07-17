from __future__ import annotations

from pathlib import Path

import pytest

from paic.contracts.loader import ContractBundle, load_contract_bundle


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def spec_dir(repo_root: Path) -> Path:
    return repo_root / "specs"


@pytest.fixture(scope="session")
def bundle(spec_dir: Path) -> ContractBundle:
    return load_contract_bundle(spec_dir)
