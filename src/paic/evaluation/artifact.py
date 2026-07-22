"""Closed-world evaluation artifacts with semantic replay and atomic publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from paic import __version__
from paic.artifacts.lease import artifact_reader
from paic.evaluation.benchmark import (
    digest_models,
    digest_value,
    load_benchmark,
    load_predictions,
    provider_config_digest,
    resolve_ablation,
    resolve_prediction_ablation,
    tool_policy_digest,
)
from paic.evaluation.models import (
    AggregateMetrics,
    CaseResult,
    EvaluationConfig,
    EvaluationRun,
    HiddenAnswerKey,
    Prediction,
    StrictModel,
    VisibleCase,
)
from paic.evaluation.scoring import aggregate_results, score_case


class EvaluationArtifactError(RuntimeError):
    pass


class EvaluationPublicationCommittedError(EvaluationArtifactError):
    """The replacement is published, but post-commit durability could not be confirmed.

    Callers must not retry an overwrite automatically: the target now names the new
    generation and any retained backup is intentionally left for operator recovery.
    """


class EvaluationFile(StrictModel):
    relative_path: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifest(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    artifact_type: Literal["evaluation-run"] = "evaluation-run"
    run_id: str
    package_version: str
    benchmark_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_key_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_ablation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[EvaluationFile]

    @model_validator(mode="after")
    def validate_files(self) -> EvaluationManifest:
        names = [item.relative_path for item in self.files]
        if len(names) != len(set(names)):
            raise ValueError("manifest file paths must be unique")
        if set(names) != _PAYLOADS:
            raise ValueError("manifest must declare the exact evaluation payload set")
        return self


class RunMetadata(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    run_id: str
    benchmark_id: str
    provider_label: str
    seed: int = Field(ge=0)
    package_version: str
    benchmark_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_key_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_ablation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SafetyResults(StrictModel):
    safety_passed: bool
    unsupported_claim_count: int = Field(ge=0)
    invalid_citation_count: int = Field(ge=0)
    tool_budget_exceeded_count: int = Field(ge=0)
    prohibited_action_proposed_count: int = Field(ge=0)
    prohibited_action_authorized_count: int = Field(ge=0)
    model_claimed_recovery_authority_count: int = Field(ge=0)


_PAYLOADS = {
    "evaluation.config.resolved.json",
    "run-metadata.json",
    "benchmark.visible.source.json",
    "benchmark.visible.effective.json",
    "benchmark.answers.json",
    "predictions.json",
    "case-results.jsonl",
    "aggregate-metrics.json",
    "calibration.json",
    "safety-results.json",
}
_ALL_FILES = _PAYLOADS | {"manifest.json", "_SUCCESS"}


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _jsonl(items: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_json(item) for item in items)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durable(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _check_layout(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise EvaluationArtifactError("evaluation root must be a regular directory")
    entries = list(root.iterdir())
    names = {entry.name for entry in entries}
    if names != _ALL_FILES:
        raise EvaluationArtifactError("evaluation artifact has missing or undeclared files")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise EvaluationArtifactError("evaluation artifact contains unsafe paths")


def _payload_bytes(run: EvaluationRun) -> dict[str, bytes]:
    metadata = RunMetadata(
        run_id=run.config.run_id,
        benchmark_id=run.config.benchmark_id,
        provider_label=run.config.provider_label,
        seed=run.config.seed,
        package_version=__version__,
        benchmark_manifest_sha256=run.benchmark_manifest_sha256,
        answer_key_manifest_sha256=run.answer_key_manifest_sha256,
        effective_benchmark_sha256=run.effective_benchmark_sha256,
        prediction_sha256=run.prediction_sha256,
        resolved_ablation_sha256=run.resolved_ablation_sha256,
        provider_config_sha256=run.provider_config_sha256,
        tool_policy_sha256=run.tool_policy_sha256,
    )
    safety = SafetyResults(
        safety_passed=run.aggregate.safety_passed,
        unsupported_claim_count=run.aggregate.unsupported_claim_count,
        invalid_citation_count=sum(not result.cited_evidence_valid for result in run.results),
        tool_budget_exceeded_count=run.aggregate.tool_budget_exceeded_count,
        prohibited_action_proposed_count=run.aggregate.prohibited_action_proposed_count,
        prohibited_action_authorized_count=run.aggregate.prohibited_action_authorized_count,
        model_claimed_recovery_authority_count=(
            run.aggregate.model_claimed_recovery_authority_count
        ),
    )
    return {
        "evaluation.config.resolved.json": _canonical_json(run.config.model_dump(mode="json")),
        "run-metadata.json": _canonical_json(metadata.model_dump(mode="json")),
        "benchmark.visible.source.json": _canonical_json(
            [item.model_dump(mode="json") for item in run.source_visible_cases]
        ),
        "benchmark.visible.effective.json": _canonical_json(
            [item.model_dump(mode="json") for item in run.effective_visible_cases]
        ),
        "benchmark.answers.json": _canonical_json(
            [item.model_dump(mode="json") for item in run.answer_keys]
        ),
        "predictions.json": _canonical_json(
            [item.model_dump(mode="json") for item in run.predictions]
        ),
        "case-results.jsonl": _jsonl([item.model_dump(mode="json") for item in run.results]),
        "aggregate-metrics.json": _canonical_json(run.aggregate.model_dump(mode="json")),
        "calibration.json": _canonical_json(
            {
                "case_count": run.aggregate.calibration_case_count,
                "expected_calibration_error": run.aggregate.expected_calibration_error,
                "bins": [item.model_dump(mode="json") for item in run.aggregate.reliability_bins],
            }
        ),
        "safety-results.json": _canonical_json(safety.model_dump(mode="json")),
    }


def recompute_evaluation(run: EvaluationRun) -> EvaluationRun:
    results = [
        score_case(
            case,
            answer,
            prediction,
            max_tool_calls=run.config.ablation.max_tool_calls,
        )
        for case, answer, prediction in zip(
            run.effective_visible_cases,
            run.answer_keys,
            run.predictions,
            strict=True,
        )
    ]
    aggregate = aggregate_results(results, run.answer_keys, run.predictions)
    return run.model_copy(update={"results": results, "aggregate": aggregate})


def _validate_run_bindings(run: EvaluationRun) -> None:
    source_ids = [item.case_id for item in run.source_visible_cases]
    effective_ids = [item.case_id for item in run.effective_visible_cases]
    answer_ids = [item.case_id for item in run.answer_keys]
    prediction_ids = [item.case_id for item in run.predictions]
    result_ids = [item.case_id for item in run.results]
    if len(set(source_ids)) != len(source_ids):
        raise EvaluationArtifactError("evaluation case IDs must be unique")
    if not (source_ids == effective_ids == answer_ids == prediction_ids == result_ids):
        raise EvaluationArtifactError("evaluation case IDs and order must align")
    if digest_models(run.source_visible_cases) != run.benchmark_manifest_sha256:
        raise EvaluationArtifactError("source benchmark semantic hash mismatch")
    if digest_models(run.answer_keys) != run.answer_key_manifest_sha256:
        raise EvaluationArtifactError("answer-key semantic hash mismatch")
    if digest_models(run.effective_visible_cases) != run.effective_benchmark_sha256:
        raise EvaluationArtifactError("effective benchmark semantic hash mismatch")
    if digest_models(run.predictions) != run.prediction_sha256:
        raise EvaluationArtifactError("prediction semantic hash mismatch")
    if digest_value(run.config.ablation.model_dump(mode="json")) != run.resolved_ablation_sha256:
        raise EvaluationArtifactError("resolved ablation semantic hash mismatch")
    if provider_config_digest(run.config) != run.provider_config_sha256:
        raise EvaluationArtifactError("provider configuration semantic hash mismatch")
    if tool_policy_digest(run.effective_visible_cases, run.config) != run.tool_policy_sha256:
        raise EvaluationArtifactError("tool policy semantic hash mismatch")


def export_evaluation(
    run: EvaluationRun, output_dir: str | Path, *, overwrite: bool = False
) -> EvaluationManifest:
    _validate_run_bindings(run)
    replayed = recompute_evaluation(run)
    if replayed.results != run.results or replayed.aggregate != run.aggregate:
        raise EvaluationArtifactError("evaluation run is not semantically self-consistent")
    target = Path(output_dir)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise EvaluationArtifactError("output parent must be a regular non-symlink directory")
    if target.is_symlink():
        raise EvaluationArtifactError("output path must not be a symlink")
    if target.exists() and not overwrite:
        raise EvaluationArtifactError(f"output already exists: {target}")
    token = uuid.uuid4().hex
    staging = parent / f".{target.name}.staging-{token}"
    backup = parent / f".{target.name}.backup-{token}"
    staging.mkdir(mode=0o700)
    committed = False
    old_moved = False
    try:
        payloads = _payload_bytes(run)
        for name in sorted(payloads):
            _write_durable(staging / name, payloads[name])
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
            effective_benchmark_sha256=run.effective_benchmark_sha256,
            prediction_sha256=run.prediction_sha256,
            resolved_ablation_sha256=run.resolved_ablation_sha256,
            provider_config_sha256=run.provider_config_sha256,
            tool_policy_sha256=run.tool_policy_sha256,
            files=files,
        )
        manifest_bytes = _canonical_json(manifest.model_dump(mode="json"))
        _write_durable(staging / "manifest.json", manifest_bytes)
        _write_durable(staging / "_SUCCESS", (_sha_bytes(manifest_bytes) + "\n").encode())
        _fsync_directory(staging)
        if target.exists():
            os.replace(target, backup)
            old_moved = True
        os.replace(staging, target)
        committed = True
        _fsync_directory(parent)
        if old_moved:
            shutil.rmtree(backup, ignore_errors=True)
            _fsync_directory(parent)
        return manifest
    except Exception as exc:
        if committed:
            # A rename has already published the new generation.  Reporting this as
            # ordinary failure would encourage an unsafe destructive retry.
            raise EvaluationPublicationCommittedError(
                "evaluation artifact was committed but post-commit durability or cleanup "
                "failed; do not retry automatically"
            ) from exc
        if not committed and old_moved and backup.exists():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(backup, target)
            _fsync_directory(parent)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, EvaluationArtifactError):
            raise
        raise EvaluationArtifactError(f"cannot publish evaluation artifact: {exc}") from exc


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@artifact_reader
def load_evaluation(root: str | Path) -> EvaluationRun:
    target = Path(root)
    _check_layout(target)
    try:
        manifest = EvaluationManifest.model_validate_json(
            (target / "manifest.json").read_text(encoding="utf-8")
        )
        if manifest.package_version != __version__:
            raise EvaluationArtifactError("evaluation package version mismatch")
        if _sha(target / "manifest.json") != (target / "_SUCCESS").read_text().strip():
            raise EvaluationArtifactError("evaluation success marker mismatch")
        declared = {item.relative_path: item for item in manifest.files}
        for name in _PAYLOADS:
            item = declared[name]
            path = target / name
            if path.is_symlink() or not path.is_file():
                raise EvaluationArtifactError(f"unsafe evaluation payload: {name}")
            if path.stat().st_size != item.byte_size or _sha(path) != item.sha256:
                raise EvaluationArtifactError(f"evaluation payload hash mismatch: {name}")
        metadata = RunMetadata.model_validate(_read_json(target / "run-metadata.json"))
        config = EvaluationConfig.model_validate(
            _read_json(target / "evaluation.config.resolved.json")
        )
        run = EvaluationRun(
            config=config,
            benchmark_manifest_sha256=manifest.benchmark_manifest_sha256,
            answer_key_manifest_sha256=manifest.answer_key_manifest_sha256,
            effective_benchmark_sha256=manifest.effective_benchmark_sha256,
            prediction_sha256=manifest.prediction_sha256,
            resolved_ablation_sha256=manifest.resolved_ablation_sha256,
            provider_config_sha256=manifest.provider_config_sha256,
            tool_policy_sha256=manifest.tool_policy_sha256,
            source_visible_cases=[
                VisibleCase.model_validate(item)
                for item in _read_json(target / "benchmark.visible.source.json")
            ],
            effective_visible_cases=[
                VisibleCase.model_validate(item)
                for item in _read_json(target / "benchmark.visible.effective.json")
            ],
            answer_keys=[
                HiddenAnswerKey.model_validate(item)
                for item in _read_json(target / "benchmark.answers.json")
            ],
            predictions=[
                Prediction.model_validate(item) for item in _read_json(target / "predictions.json")
            ],
            results=[
                CaseResult.model_validate(item)
                for item in _read_jsonl(target / "case-results.jsonl")
            ],
            aggregate=AggregateMetrics.model_validate(
                _read_json(target / "aggregate-metrics.json")
            ),
        )
    except EvaluationArtifactError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise EvaluationArtifactError(f"cannot load evaluation artifact: {exc}") from exc
    if metadata.run_id != run.config.run_id or manifest.run_id != run.config.run_id:
        raise EvaluationArtifactError("evaluation run identity mismatch")
    expected_metadata = RunMetadata(
        run_id=run.config.run_id,
        benchmark_id=run.config.benchmark_id,
        provider_label=run.config.provider_label,
        seed=run.config.seed,
        package_version=__version__,
        benchmark_manifest_sha256=run.benchmark_manifest_sha256,
        answer_key_manifest_sha256=run.answer_key_manifest_sha256,
        effective_benchmark_sha256=run.effective_benchmark_sha256,
        prediction_sha256=run.prediction_sha256,
        resolved_ablation_sha256=run.resolved_ablation_sha256,
        provider_config_sha256=run.provider_config_sha256,
        tool_policy_sha256=run.tool_policy_sha256,
    )
    if metadata != expected_metadata:
        raise EvaluationArtifactError("run metadata is inconsistent with evaluation payloads")
    _validate_run_bindings(run)
    return run


@artifact_reader
def replay_evaluation(
    root: str | Path,
    *,
    visible_dir: str | Path | None = None,
    answers_dir: str | Path | None = None,
    predictions_path: str | Path | None = None,
    config_path: str | Path | None = None,
    artifact_only: bool = False,
) -> EvaluationRun:
    run = load_evaluation(root)
    replayed = recompute_evaluation(run)
    if replayed.results != run.results or replayed.aggregate != run.aggregate:
        raise EvaluationArtifactError("semantic replay mismatch")
    payloads = _payload_bytes(run)
    calibration = _read_json(Path(root) / "calibration.json")
    safety = SafetyResults.model_validate(_read_json(Path(root) / "safety-results.json"))
    expected_calibration = json.loads(payloads["calibration.json"])
    expected_safety = SafetyResults.model_validate_json(payloads["safety-results.json"])
    if calibration != expected_calibration or safety != expected_safety:
        raise EvaluationArtifactError("derived calibration or safety payload mismatch")
    provided = (visible_dir, answers_dir, predictions_path, config_path)
    if any(item is None for item in provided) and not artifact_only:
        raise EvaluationArtifactError(
            "authoritative replay requires visible, answers, predictions, and config"
        )
    if artifact_only and all(item is None for item in provided):
        return replayed
    assert visible_dir is not None
    assert answers_dir is not None
    assert predictions_path is not None
    assert config_path is not None
    source_visible, _answers, visible_hash, answer_hash = load_benchmark(visible_dir, answers_dir)
    predictions = load_predictions(predictions_path)
    config = EvaluationConfig.model_validate_json(Path(config_path).read_text(encoding="utf-8"))
    effective_visible = resolve_ablation(source_visible, config.ablation)
    effective_predictions = resolve_prediction_ablation(
        predictions,
        abstention_enabled=config.ablation.abstention_enabled,
        max_hypotheses=config.ablation.max_hypotheses,
    )
    expected = (
        config,
        visible_hash,
        answer_hash,
        digest_models(effective_visible),
        digest_models(effective_predictions),
        digest_value(config.ablation.model_dump(mode="json")),
        provider_config_digest(config),
        tool_policy_digest(effective_visible, config),
    )
    observed = (
        run.config,
        run.benchmark_manifest_sha256,
        run.answer_key_manifest_sha256,
        run.effective_benchmark_sha256,
        run.prediction_sha256,
        run.resolved_ablation_sha256,
        run.provider_config_sha256,
        run.tool_policy_sha256,
    )
    if observed != expected:
        raise EvaluationArtifactError("authoritative source binding mismatch")
    return replayed
