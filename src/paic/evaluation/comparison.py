"""Deterministic comparison of two immutable evaluation runs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from paic.evaluation.artifact import replay_evaluation


class ComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_run_id: str
    right_run_id: str
    top1_delta: float
    brier_delta: float
    calibration_error_delta: float
    abstention_delta: float
    tool_calls_delta: float
    paired_wins: int = Field(ge=0)
    paired_losses: int = Field(ge=0)


def compare_runs(left_dir: str, right_dir: str) -> ComparisonReport:
    left = replay_evaluation(left_dir)
    right = replay_evaluation(right_dir)
    left_results = {item.case_id: item for item in left.results}
    right_results = {item.case_id: item for item in right.results}
    if set(left_results) != set(right_results):
        raise ValueError("comparison runs must contain the same case IDs")
    wins = sum(
        right_results[key].top1_correct and not left_results[key].top1_correct
        for key in left_results
    )
    losses = sum(
        left_results[key].top1_correct and not right_results[key].top1_correct
        for key in left_results
    )
    return ComparisonReport(
        left_run_id=left.config.run_id,
        right_run_id=right.config.run_id,
        top1_delta=right.aggregate.top1_accuracy - left.aggregate.top1_accuracy,
        brier_delta=right.aggregate.brier_score - left.aggregate.brier_score,
        calibration_error_delta=right.aggregate.expected_calibration_error
        - left.aggregate.expected_calibration_error,
        abstention_delta=right.aggregate.abstention_accuracy - left.aggregate.abstention_accuracy,
        tool_calls_delta=right.aggregate.mean_tool_calls - left.aggregate.mean_tool_calls,
        paired_wins=wins,
        paired_losses=losses,
    )
