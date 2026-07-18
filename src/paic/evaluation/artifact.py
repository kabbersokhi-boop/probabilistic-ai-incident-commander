"""Closed-world evaluation artifacts and semantic replay."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from pydantic import Field

from paic import __version__
from paic.evaluation.models import EvaluationRun, StrictModel


class EvaluationFile(StrictModel):
    relative_path: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifest(StrictModel):
    schema_version: str = "1.0"
    artifact_type: str = "evaluation-run"
    run_id: str
    package_version: str = __version__
    benchmark_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_key_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[EvaluationFile]


class EvaluationArtifactError(RuntimeError):
    pass


_PAYLOADS = {"evaluation.config.resolved.json", "case-results.json", "aggregate-metrics.json"}
_ALL_FILES = _PAYLOADS | {"manifest.json", "_SUCCESS"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_layout(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise EvaluationArtifactError("evaluation root must be a regular directory")
    entries = list(root.iterdir())
    if {entry.name for entry in entries} != _ALL_FILES:
        raise EvaluationArtifactError("evaluation artifact has missing or undeclared files")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise EvaluationArtifactError("evaluation artifact contains unsafe paths")


def export_evaluation(
    run: EvaluationRun, output_dir: str | Path, *, overwrite: bool = False
) -> EvaluationManifest:
    target = Path(output_dir)
    if target.exists():
        if not overwrite:
            raise EvaluationArtifactError(f"output already exists: {target}")
        shutil.rmtree(target)
    staging = target.with_name(f".{target.name}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    payloads = {
        "evaluation.config.resolved.json": run.config.model_dump_json(indent=2) + "\n",
        "case-results.json": json.dumps(
            [item.model_dump(mode="json") for item in run.results], indent=2, sort_keys=True
        )
        + "\n",
        "aggregate-metrics.json": run.aggregate.model_dump_json(indent=2) + "\n",
    }
    for name, content in payloads.items():
        (staging / name).write_text(content, encoding="utf-8")
    files = [
        EvaluationFile(
            relative_path=name,
            byte_size=(staging / name).stat().st_size,
            sha256=_sha(staging / name),
        )
        for name in sorted(_PAYLOADS)
    ]
    manifest = EvaluationManifest(
        run_id=run.config.run_id,
        package_version=__version__,
        benchmark_manifest_sha256=run.benchmark_manifest_sha256,
        answer_key_manifest_sha256=run.answer_key_manifest_sha256,
        files=files,
    )
    (staging / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (staging / "_SUCCESS").write_text(_sha(staging / "manifest.json") + "\n", encoding="utf-8")
    staging.replace(target)
    return manifest


def load_evaluation(root: str | Path) -> EvaluationRun:
    target = Path(root)
    _check_layout(target)
    try:
        manifest = EvaluationManifest.model_validate_json((target / "manifest.json").read_text())
        config = json.loads((target / "evaluation.config.resolved.json").read_text())
        results = json.loads((target / "case-results.json").read_text())
        aggregate = json.loads((target / "aggregate-metrics.json").read_text())
        run = EvaluationRun.model_validate(
            {
                "config": config,
                "benchmark_manifest_sha256": manifest.benchmark_manifest_sha256,
                "answer_key_manifest_sha256": manifest.answer_key_manifest_sha256,
                "results": results,
                "aggregate": aggregate,
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise EvaluationArtifactError(f"cannot load evaluation artifact: {exc}") from exc
    if run.config.run_id != manifest.run_id or manifest.package_version != __version__:
        raise EvaluationArtifactError("evaluation identity or package binding mismatch")
    for item in manifest.files:
        path = target / item.relative_path
        if not path.is_file() or path.stat().st_size != item.byte_size or _sha(path) != item.sha256:
            raise EvaluationArtifactError(f"evaluation payload hash mismatch: {item.relative_path}")
    if _sha(target / "manifest.json") != (target / "_SUCCESS").read_text().strip():
        raise EvaluationArtifactError("evaluation success marker mismatch")
    return run


def replay_evaluation(root: str | Path) -> EvaluationRun:
    """Re-read the immutable run; scoring is deterministic and provider-free."""

    return load_evaluation(root)
