"""Load the machine-readable project contracts from disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from paic.contracts.models import (
    EvaluationContract,
    IncidentSpec,
    ProjectContract,
    SafetyContract,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class ContractLoadError(RuntimeError):
    """Raised when a contract file cannot be parsed or validated."""


@dataclass(frozen=True)
class ContractBundle:
    project: ProjectContract
    evaluation: EvaluationContract
    safety: SafetyContract
    incidents: tuple[IncidentSpec, ...]
    spec_dir: Path


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except OSError as exc:  # pragma: no cover - environment dependent
        raise ContractLoadError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContractLoadError(f"invalid YAML in {path}: {exc}") from exc


def _load_model(path: Path, model_type: type[ModelT]) -> ModelT:
    raw = _load_yaml(path)
    try:
        return model_type.model_validate(raw)
    except Exception as exc:
        raise ContractLoadError(f"invalid contract {path}: {exc}") from exc


def load_contract_bundle(spec_dir: str | Path) -> ContractBundle:
    """Load all Phase 0 contracts from ``spec_dir``."""

    root = Path(spec_dir).resolve()
    required_files = {
        "project": root / "project.yaml",
        "evaluation": root / "evaluation.yaml",
        "safety": root / "safety.yaml",
    }
    missing = [str(path) for path in required_files.values() if not path.is_file()]
    if missing:
        raise ContractLoadError(f"missing required contract files: {', '.join(missing)}")

    incident_dir = root / "incidents"
    incident_paths = sorted(incident_dir.glob("*.yaml")) if incident_dir.is_dir() else []
    if not incident_paths:
        raise ContractLoadError(f"no incident contracts found in {incident_dir}")

    return ContractBundle(
        project=_load_model(required_files["project"], ProjectContract),
        evaluation=_load_model(required_files["evaluation"], EvaluationContract),
        safety=_load_model(required_files["safety"], SafetyContract),
        incidents=tuple(_load_model(path, IncidentSpec) for path in incident_paths),
        spec_dir=root,
    )
