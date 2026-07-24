"""Lineage-safe paired comparisons with deterministic bootstrap intervals."""

from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path
from typing import Literal

from pydantic import Field

from paic.artifacts.lease import artifact_reader
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.evaluation.artifact import replay_evaluation
from paic.evaluation.benchmark import digest_value
from paic.evaluation.models import StrictModel


class BootstrapInterval(StrictModel):
    confidence_level: float = Field(gt=0.0, lt=1.0)
    lower: float
    upper: float
    iterations: int = Field(ge=100)
    seed: int = Field(ge=0)


class ComparisonReport(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    left_run_id: str
    right_run_id: str
    benchmark_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_key_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    left_effective_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    right_effective_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    left_resolved_ablation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    right_resolved_ablation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    left_provider_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    right_provider_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    left_tool_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    right_tool_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    top1_delta: float
    primary_top1_delta: float
    brier_delta: float
    log_loss_delta: float
    calibration_error_delta: float
    abstention_delta: float
    coverage_delta: float
    tool_calls_delta: float
    paired_wins: int = Field(ge=0)
    paired_losses: int = Field(ge=0)
    top1_delta_interval: BootstrapInterval
    brier_delta_interval: BootstrapInterval


class ComparisonArtifactError(RuntimeError):
    pass


class ComparisonFile(StrictModel):
    relative_path: Literal["comparison.json"] = "comparison.json"
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComparisonManifest(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    artifact_type: Literal["evaluation-comparison"] = "evaluation-comparison"
    left_run_id: str
    right_run_id: str
    file: ComparisonFile


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * quantile)
    return ordered[index]


def _bootstrap_interval(
    deltas: list[float], *, seed: int, iterations: int, confidence_level: float
) -> BootstrapInterval:
    if len(deltas) < 2:
        raise ValueError("paired bootstrap requires at least two cases")
    if iterations < 100:
        raise ValueError("paired bootstrap requires at least 100 iterations")
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        draw = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        samples.append(sum(draw) / len(draw))
    alpha = (1.0 - confidence_level) / 2.0
    return BootstrapInterval(
        confidence_level=confidence_level,
        lower=_percentile(samples, alpha),
        upper=_percentile(samples, 1.0 - alpha),
        iterations=iterations,
        seed=seed,
    )


def compare_runs(
    left_dir: str | Path,
    right_dir: str | Path,
    *,
    visible_dir: str | Path | None = None,
    answers_dir: str | Path | None = None,
    left_predictions_path: str | Path | None = None,
    left_config_path: str | Path | None = None,
    right_predictions_path: str | Path | None = None,
    right_config_path: str | Path | None = None,
    artifact_only: bool = False,
    bootstrap_iterations: int = 2000,
    confidence_level: float = 0.95,
) -> ComparisonReport:
    left = replay_evaluation(
        left_dir,
        visible_dir=visible_dir,
        answers_dir=answers_dir,
        predictions_path=left_predictions_path,
        config_path=left_config_path,
        artifact_only=artifact_only,
    )
    right = replay_evaluation(
        right_dir,
        visible_dir=visible_dir,
        answers_dir=answers_dir,
        predictions_path=right_predictions_path,
        config_path=right_config_path,
        artifact_only=artifact_only,
    )
    if left.config.benchmark_id != right.config.benchmark_id:
        raise ValueError("comparison runs must use the same benchmark ID")
    if left.benchmark_manifest_sha256 != right.benchmark_manifest_sha256:
        raise ValueError("comparison runs must share source benchmark lineage")
    if left.answer_key_manifest_sha256 != right.answer_key_manifest_sha256:
        raise ValueError("comparison runs must share answer-key lineage")
    left_ids = [item.case_id for item in left.results]
    right_ids = [item.case_id for item in right.results]
    if len(left_ids) != len(set(left_ids)) or len(right_ids) != len(set(right_ids)):
        raise ValueError("comparison runs must not contain duplicate cases")
    if left_ids != right_ids:
        raise ValueError("comparison runs must contain the same ordered case IDs")
    top1_deltas = [
        float(right_result.top1_correct) - float(left_result.top1_correct)
        for left_result, right_result in zip(left.results, right.results, strict=True)
    ]
    brier_deltas = [
        right_result.brier_score - left_result.brier_score
        for left_result, right_result in zip(left.results, right.results, strict=True)
    ]
    seed_material = {
        "left": left.config.run_id,
        "right": right.config.run_id,
        "benchmark": left.benchmark_manifest_sha256,
        "answer": left.answer_key_manifest_sha256,
        "seed": left.config.seed ^ right.config.seed,
    }
    bootstrap_seed = int(digest_value(seed_material)[:16], 16)
    wins = sum(delta > 0 for delta in top1_deltas)
    losses = sum(delta < 0 for delta in top1_deltas)
    return ComparisonReport(
        left_run_id=left.config.run_id,
        right_run_id=right.config.run_id,
        benchmark_manifest_sha256=left.benchmark_manifest_sha256,
        answer_key_manifest_sha256=left.answer_key_manifest_sha256,
        case_ids_sha256=digest_value(left_ids),
        left_effective_benchmark_sha256=left.effective_benchmark_sha256,
        right_effective_benchmark_sha256=right.effective_benchmark_sha256,
        left_resolved_ablation_sha256=left.resolved_ablation_sha256,
        right_resolved_ablation_sha256=right.resolved_ablation_sha256,
        left_provider_config_sha256=left.provider_config_sha256,
        right_provider_config_sha256=right.provider_config_sha256,
        left_tool_policy_sha256=left.tool_policy_sha256,
        right_tool_policy_sha256=right.tool_policy_sha256,
        top1_delta=right.aggregate.top1_accuracy - left.aggregate.top1_accuracy,
        primary_top1_delta=(
            right.aggregate.primary_top1_accuracy - left.aggregate.primary_top1_accuracy
        ),
        brier_delta=right.aggregate.brier_score - left.aggregate.brier_score,
        log_loss_delta=right.aggregate.clipped_log_loss - left.aggregate.clipped_log_loss,
        calibration_error_delta=(
            right.aggregate.expected_calibration_error - left.aggregate.expected_calibration_error
        ),
        abstention_delta=(right.aggregate.abstention_accuracy - left.aggregate.abstention_accuracy),
        coverage_delta=right.aggregate.coverage - left.aggregate.coverage,
        tool_calls_delta=right.aggregate.mean_tool_calls - left.aggregate.mean_tool_calls,
        paired_wins=wins,
        paired_losses=losses,
        top1_delta_interval=_bootstrap_interval(
            top1_deltas,
            seed=bootstrap_seed,
            iterations=bootstrap_iterations,
            confidence_level=confidence_level,
        ),
        brier_delta_interval=_bootstrap_interval(
            brier_deltas,
            seed=bootstrap_seed + 1,
            iterations=bootstrap_iterations,
            confidence_level=confidence_level,
        ),
    )


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _write_durable(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def export_comparison(report: ComparisonReport, output_dir: str | Path) -> None:
    publisher = AtomicDirectoryPublisher(output_dir, overwrite=False)
    try:
        with publisher as staging:
            comparison_bytes = _canonical(report.model_dump(mode="json"))
            _write_durable(staging / "comparison.json", comparison_bytes)
            manifest = ComparisonManifest(
                left_run_id=report.left_run_id,
                right_run_id=report.right_run_id,
                file=ComparisonFile(
                    byte_size=len(comparison_bytes),
                    sha256=hashlib.sha256(comparison_bytes).hexdigest(),
                ),
            )
            manifest_bytes = _canonical(manifest.model_dump(mode="json"))
            _write_durable(staging / "manifest.json", manifest_bytes)
            _write_durable(
                staging / "_SUCCESS",
                (hashlib.sha256(manifest_bytes).hexdigest() + "\n").encode(),
            )
            publisher.commit()
    except ArtifactPublicationError as exc:
        raise ComparisonArtifactError(str(exc)) from exc


@artifact_reader
def load_comparison(root: str | Path) -> ComparisonReport:
    path = Path(root)
    if path.is_symlink() or not path.is_dir():
        raise ComparisonArtifactError("comparison root must be a regular directory")
    entries = list(path.iterdir())
    if {item.name for item in entries} != {"comparison.json", "manifest.json", "_SUCCESS"}:
        raise ComparisonArtifactError("comparison artifact is not closed-world")
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise ComparisonArtifactError("comparison artifact contains unsafe paths")
    try:
        manifest_bytes = (path / "manifest.json").read_bytes()
        marker = (path / "_SUCCESS").read_text(encoding="utf-8").strip()
        if hashlib.sha256(manifest_bytes).hexdigest() != marker:
            raise ComparisonArtifactError("comparison success marker mismatch")
        manifest = ComparisonManifest.model_validate_json(manifest_bytes)
        comparison_bytes = (path / "comparison.json").read_bytes()
        if (
            len(comparison_bytes) != manifest.file.byte_size
            or hashlib.sha256(comparison_bytes).hexdigest() != manifest.file.sha256
        ):
            raise ComparisonArtifactError("comparison payload hash mismatch")
        report = ComparisonReport.model_validate_json(comparison_bytes)
    except ComparisonArtifactError:
        raise
    except (OSError, ValueError) as exc:
        raise ComparisonArtifactError(f"cannot load comparison artifact: {exc}") from exc
    if report.left_run_id != manifest.left_run_id or report.right_run_id != manifest.right_run_id:
        raise ComparisonArtifactError("comparison identity mismatch")
    return report


def replay_comparison(
    comparison_dir: str | Path,
    left_dir: str | Path,
    right_dir: str | Path,
    *,
    visible_dir: str | Path | None = None,
    answers_dir: str | Path | None = None,
    left_predictions_path: str | Path | None = None,
    left_config_path: str | Path | None = None,
    right_predictions_path: str | Path | None = None,
    right_config_path: str | Path | None = None,
    artifact_only: bool = False,
) -> ComparisonReport:
    stored = load_comparison(comparison_dir)
    if (
        stored.top1_delta_interval.iterations != stored.brier_delta_interval.iterations
        or stored.top1_delta_interval.confidence_level
        != stored.brier_delta_interval.confidence_level
    ):
        raise ComparisonArtifactError("comparison interval configuration mismatch")
    replayed = compare_runs(
        left_dir,
        right_dir,
        visible_dir=visible_dir,
        answers_dir=answers_dir,
        left_predictions_path=left_predictions_path,
        left_config_path=left_config_path,
        right_predictions_path=right_predictions_path,
        right_config_path=right_config_path,
        artifact_only=artifact_only,
        bootstrap_iterations=stored.top1_delta_interval.iterations,
        confidence_level=stored.top1_delta_interval.confidence_level,
    )
    if stored != replayed:
        raise ComparisonArtifactError("comparison semantic replay mismatch")
    return replayed
