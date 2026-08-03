"""Deterministic, public-safe export for the future read-only web product."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from paic.tui.config import TUIConfigError, load_workspace_config
from paic.tui.models import WorkspaceSnapshot
from paic.tui.workspace import inspect_workspace

SCHEMA_VERSION = "1.0"
BUNDLE_FILE = "bundle.json"
MANIFEST_FILE = "manifest.json"
CHECKSUM_FILE = "SHA256SUMS"
_SECRET_KEY = re.compile(
    r"(?:secret|password|passwd|token|api[_-]?key|private[_-]?key|credential|authorization|cookie|env)",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")


class WebReadinessError(ValueError):
    """Raised when public export inputs or outputs are unsafe."""


class PublicFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    content: Any


class WebBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = Field(pattern=r"^1\.0$")
    bundle_kind: str = Field(pattern=r"^paic-public-demo$")
    disclaimer: str
    workspace_id: str
    display_name: str
    source_bindings: dict[str, Any]
    lifecycle: dict[str, Any]
    detection: dict[str, Any]
    operations: dict[str, Any]
    investigation: dict[str, Any]
    impact: dict[str, Any]
    remediation: dict[str, Any]
    recovery: dict[str, Any]
    evaluation: dict[str, Any]
    stages: list[dict[str, Any]]
    files: list[PublicFile]


def _regular(path: Path, context: str) -> None:
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise WebReadinessError(f"cannot inspect {context}: {path}") from exc
    if not stat.S_ISREG(mode):
        raise WebReadinessError(f"{context} is not a regular file: {path}")


def _hash(path: Path) -> str:
    _regular(path, "public source")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_public(value: Any, *, context: str) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise WebReadinessError(f"non-string field in public export: {context}")
            if _SECRET_KEY.search(key):
                continue
            result[key] = _safe_public(item, context=f"{context}.{key}")
        return result
    if isinstance(value, list):
        return [_safe_public(item, context=f"{context}[]") for item in value]
    if isinstance(value, str):
        if _ABSOLUTE.match(value) or "\\" in value or "\x00" in value:
            raise WebReadinessError(f"machine-specific or unsafe path in public export: {context}")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise WebReadinessError(f"unsupported value in public export: {context}")


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise WebReadinessError(f"source path escapes workspace root: {path}") from exc


def _source_roots(config: Any) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    groups = {
        "metrics.dataset": config.paths.metrics.dataset_dir,
        "metrics.analytics": config.paths.metrics.analytics_dir,
        "metrics.detection": config.paths.metrics.detection_dir,
        "incident.dataset": config.paths.incident.dataset_dir,
        "incident.impact": config.paths.incident.impact_dir,
        "incident.evidence": config.paths.incident.evidence_dir,
        "incident.investigation": config.paths.incident.investigation_dir,
        "remediation.plan": config.paths.remediation.plan_dir,
        "remediation.before": config.paths.remediation.state_before_dir,
        "remediation.after": config.paths.remediation.state_after_dir,
        "remediation.execution": config.paths.remediation.execution_dir,
        "recovery.observations": config.paths.recovery.observations_dir,
        "recovery.analytics": config.paths.recovery.analytics_dir,
        "recovery.report": config.paths.recovery.report_dir,
        "evaluation.run": config.paths.evaluation.run_dir,
        "evaluation.visible": config.paths.evaluation.visible_dir,
    }
    for name, path in groups.items():
        if path is not None:
            paths[name] = path
    return paths


def _public_files(root: Path, source_root: Path, stage: str) -> list[PublicFile]:
    if not root.is_dir() or root.is_symlink():
        raise WebReadinessError(f"public source root is not a regular directory: {root}")
    files: list[PublicFile] = []
    for path in sorted(root.rglob("*.json")):
        if "answer" in path.name.lower():
            # Evaluation answer keys are evaluator authority, not public demo data.
            continue
        if path.is_symlink():
            raise WebReadinessError(f"public source contains a symbolic link: {path}")
        _regular(path, "public source")
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WebReadinessError(f"public JSON source is malformed: {path}") from exc
        if (
            stage.startswith("evaluation")
            and "answer" in json.dumps(content, sort_keys=True).lower()
        ):
            # Manifests and run metadata can reference hidden evaluator answers even
            # when they are not answer files themselves.
            continue
        safe = _safe_public(content, context=stage)
        files.append(
            PublicFile(
                path=f"{stage}/{_relative(source_root, path)}",
                sha256=_hash(path),
                size=path.stat().st_size,
                content=safe,
            )
        )
    return files


def _stage_payload(snapshot: WorkspaceSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "key": stage.key,
            "title": stage.title,
            "status": stage.status,
            "summary": stage.summary,
            "authoritative": stage.authoritative,
            "details": list(stage.details),
            "issues": list(stage.issues),
        }
        for stage in snapshot.stages
    ]


def _section(
    files: list[PublicFile], stages: list[dict[str, Any]], keys: set[str]
) -> dict[str, Any]:
    return {
        "available": any(item["key"] in keys and item["status"] == "healthy" for item in stages),
        "source_files": [item.path for item in files],
    }


def build_bundle(*, workspace: Path, output_dir: Path) -> None:
    try:
        config = load_workspace_config(workspace)
    except TUIConfigError as exc:
        raise WebReadinessError(str(exc)) from exc
    snapshot = inspect_workspace(config)
    if snapshot.overall_status != "healthy":
        raise WebReadinessError("all configured authoritative stages must validate before export")
    root = Path(config.root_dir)
    source_bindings: dict[str, Any] = {}
    public_files: list[PublicFile] = []
    seen_paths: set[Path] = set()
    for stage, path in _source_roots(config).items():
        source_files = _public_files(path, root, stage)
        if path not in seen_paths:
            public_files.extend(source_files)
            seen_paths.add(path)
        source_bindings[stage] = {
            "root": _relative(root, path),
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size}
                for item in source_files
            ],
        }
    stages = _stage_payload(snapshot)
    bundle = WebBundle(
        schema_version=SCHEMA_VERSION,
        bundle_kind="paic-public-demo",
        disclaimer="Synthetic demonstration data only; values are not production claims.",
        workspace_id=snapshot.workspace_id,
        display_name=snapshot.display_name,
        source_bindings=source_bindings,
        lifecycle={"source": "recovery and remediation validators", "read_only": True},
        detection={"source": "validated analytics and detection artifacts"},
        operations={"source": "validated operational evidence and lineage"},
        investigation={"source": "validated investigation replay and report artifacts"},
        impact={"source": "validated customer and financial impact artifacts"},
        remediation={"source": "validated simulated plan, approval, and execution artifacts"},
        recovery={"source": "validated recovery verification artifacts"},
        evaluation={"source": "validated evaluator artifacts; answer keys excluded"},
        stages=stages,
        files=sorted(public_files, key=lambda item: (item.path, item.sha256)),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in output_dir.iterdir()):
        raise WebReadinessError("output directory cannot contain symbolic links")
    payload = json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    (output_dir / BUNDLE_FILE).write_text(payload, encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_kind": "paic-public-demo",
        "workspace_id": snapshot.workspace_id,
        "source_file_count": len(public_files),
        "bundle_sha256": _hash(output_dir / BUNDLE_FILE),
    }
    (output_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = [f"{_hash(output_dir / name)}  {name}" for name in (BUNDLE_FILE, MANIFEST_FILE)]
    (output_dir / CHECKSUM_FILE).write_text("\n".join(rows) + "\n", encoding="utf-8")
    validate_bundle(output_dir)


def validate_bundle(bundle_dir: Path) -> WebBundle:
    if not bundle_dir.is_dir() or bundle_dir.is_symlink():
        raise WebReadinessError("bundle directory must be a regular directory")
    observed = {path.name for path in bundle_dir.iterdir()}
    expected = {BUNDLE_FILE, MANIFEST_FILE, CHECKSUM_FILE}
    if observed != expected:
        raise WebReadinessError("bundle directory is not closed-world")
    for name in expected:
        _regular(bundle_dir / name, "bundle entry")
    checksums = (bundle_dir / CHECKSUM_FILE).read_text(encoding="utf-8").splitlines()
    if len(checksums) != 2 or any(
        line != f"{_hash(bundle_dir / name)}  {name}"
        for line, name in zip(checksums, (BUNDLE_FILE, MANIFEST_FILE), strict=True)
    ):
        raise WebReadinessError("bundle checksum file is invalid")
    payload = json.loads((bundle_dir / BUNDLE_FILE).read_text(encoding="utf-8"))
    bundle = WebBundle.model_validate(payload)
    _safe_public(bundle.model_dump(mode="json"), context="bundle")
    manifest = json.loads((bundle_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    if manifest.get("bundle_sha256") != _hash(bundle_dir / BUNDLE_FILE):
        raise WebReadinessError("bundle manifest does not bind bundle bytes")
    return bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--workspace", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            build_bundle(workspace=args.workspace, output_dir=args.output_dir)
        else:
            validate_bundle(args.bundle_dir)
    except (WebReadinessError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"web-readiness error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
