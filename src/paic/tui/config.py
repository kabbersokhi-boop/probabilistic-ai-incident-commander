"""Workspace configuration loading and safe path resolution."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from paic.tui.models import WorkspaceConfig, WorkspacePaths


class TUIConfigError(RuntimeError):
    pass


def _inside(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _reject_symlink_components(base: Path, path: Path, *, field: str) -> None:
    current = base
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise TUIConfigError(f"{field} traverses a symbolic link")


def _resolve_tree(root: Path, value: Any, *, field: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _resolve_tree(root, item, field=f"{field}.{key}") for key, item in value.items()
        }
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        raise TUIConfigError(f"{field} must be relative to root_dir")
    if "\x00" in str(path):
        raise TUIConfigError(f"{field} contains an invalid path character")
    _reject_symlink_components(root, path, field=field)
    candidate = (root / path).resolve(strict=False)
    if not _inside(root, candidate):
        raise TUIConfigError(f"{field} escapes root_dir")
    return candidate


def load_workspace_config(path: str | Path) -> WorkspaceConfig:
    config_path = Path(path)
    if config_path.is_symlink() or not config_path.is_file():
        raise TUIConfigError("workspace configuration must be a regular non-symlink file")
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config = WorkspaceConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise TUIConfigError(f"cannot load workspace configuration: {exc}") from exc
    if config.root_dir.is_absolute():
        raise TUIConfigError("root_dir must be relative to the workspace configuration")
    _reject_symlink_components(config_path.parent, config.root_dir, field="root_dir")
    root = (config_path.parent / config.root_dir).resolve(strict=False)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise TUIConfigError("root_dir must resolve to a regular directory")
    resolved = _resolve_tree(root, config.paths.model_dump(mode="python"), field="paths")
    return config.model_copy(
        update={"root_dir": root, "paths": WorkspacePaths.model_validate(resolved)}
    )


def _workspace_template_payload(
    *, workspace_id: str, display_name: str, root_dir: str
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "display_name": display_name,
        "root_dir": root_dir,
        "paths": {
            "metrics": {
                "dataset_dir": ".artifacts/smoke",
                "analytics_dir": ".artifacts/analytics-smoke",
                "detection_dir": ".artifacts/detection-smoke",
            },
            "incident": {
                "dataset_dir": ".artifacts/impact-source-smoke",
                "impact_dir": ".artifacts/impact-smoke",
                "evidence_dir": ".artifacts/evidence-smoke",
                "investigation_dir": ".artifacts/investigation-smoke",
                "investigation_config": "configs/investigation/smoke.yaml",
            },
            "remediation": {
                "plan_dir": ".artifacts/remediation-plan",
                "state_before_dir": ".artifacts/remediation-state",
                "state_after_dir": ".artifacts/remediation-state-after",
                "execution_dir": ".artifacts/remediation-execution",
            },
            "recovery": {
                "observations_dir": ".artifacts/obs-recovered",
                "analytics_dir": ".artifacts/recovery-analytics",
                "report_dir": ".artifacts/report-recovered",
            },
            "evaluation": {
                "run_dir": ".artifacts/evaluation-smoke",
                "visible_dir": "configs/evaluation/smoke",
                "answers_dir": "configs/evaluation/answers",
                "predictions": "configs/evaluation/smoke/predictions.json",
                "config": "configs/evaluation/smoke/evaluation.json",
            },
        },
    }


def workspace_template(*, workspace_id: str, display_name: str, root_dir: str) -> str:
    return yaml.safe_dump(
        _workspace_template_payload(
            workspace_id=workspace_id,
            display_name=display_name,
            root_dir=root_dir,
        ),
        sort_keys=False,
        allow_unicode=True,
    )


def _absolute_without_resolving(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _assert_safe_parent(target: Path) -> None:
    absolute = _absolute_without_resolving(target)
    current = Path(absolute.anchor)
    for part in absolute.parent.parts[1:]:
        current = current / part
        if (current.exists() or current.is_symlink()) and (
            current.is_symlink() or not current.is_dir()
        ):
            raise TUIConfigError(
                "workspace configuration parent traverses a symlink or non-directory"
            )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(target: Path, content: str) -> None:
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise TUIConfigError(f"workspace configuration already exists: {target}") from exc
    os.chmod(target, 0o600)
    with suppress(OSError):
        _fsync_directory(target.parent)


def _write_atomic(target: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if (target.exists() or target.is_symlink()) and (
            target.is_symlink() or not target.is_file()
        ):
            raise TUIConfigError("workspace configuration target must be a regular file")
        os.replace(temporary, target)
        with suppress(OSError):
            _fsync_directory(target.parent)
    except Exception:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def write_workspace_template(
    path: str | Path,
    *,
    workspace_id: str,
    display_name: str,
    root_dir: str,
    overwrite: bool = False,
) -> Path:
    if Path(root_dir).is_absolute():
        raise TUIConfigError("root_dir must be relative to the workspace configuration")
    try:
        WorkspaceConfig(
            workspace_id=workspace_id,
            display_name=display_name,
            root_dir=Path(root_dir),
            paths=WorkspacePaths(),
        )
    except ValidationError as exc:
        raise TUIConfigError(f"invalid workspace template values: {exc}") from exc

    target = Path(path)
    _assert_safe_parent(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TUIConfigError(f"cannot create workspace configuration parent: {exc}") from exc
    _assert_safe_parent(target)

    if target.exists() or target.is_symlink():
        if not overwrite:
            raise TUIConfigError(f"workspace configuration already exists: {target}")
        if target.is_symlink() or not target.is_file():
            raise TUIConfigError("workspace configuration target must be a regular file")

    content = workspace_template(
        workspace_id=workspace_id,
        display_name=display_name,
        root_dir=root_dir,
    )
    try:
        if overwrite:
            _write_atomic(target, content)
        else:
            _write_exclusive(target, content)
    except TUIConfigError:
        raise
    except OSError as exc:
        raise TUIConfigError(f"cannot write workspace configuration: {exc}") from exc
    return target
