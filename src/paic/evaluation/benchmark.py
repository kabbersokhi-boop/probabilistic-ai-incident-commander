"""Load and bind visible cases, hidden answers, and scripted predictions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from paic.evaluation.models import EvaluationConfig, HiddenAnswerKey, Prediction, VisibleCase


class BenchmarkError(RuntimeError):
    pass


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def digest_models(items: Sequence[BaseModel]) -> str:
    return digest_value([item.model_dump(mode="json") for item in items])


def provider_config_digest(config: EvaluationConfig) -> str:
    return digest_value(
        {
            "provider_label": config.provider_label,
            "provider_configuration": config.provider_configuration,
        }
    )


def tool_policy_digest(cases: list[VisibleCase], config: EvaluationConfig) -> str:
    return digest_value(
        {
            "max_tool_calls": config.ablation.max_tool_calls,
            "cases": [
                {"case_id": case.case_id, "allowed_tools": case.allowed_tools} for case in cases
            ],
        }
    )


def _regular_file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BenchmarkError(f"{label} is unavailable: {path}") from exc
    if path.is_symlink() or not resolved.is_file():
        raise BenchmarkError(f"{label} must be a regular non-symlink file")
    return resolved


def _load_model_list(path: Path, model: type[_ModelT], label: str) -> list[_ModelT]:
    resolved = _regular_file(path, label)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("top-level JSON value must be a list")
        return [model.model_validate(item) for item in payload]
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise BenchmarkError(f"invalid {label}: {exc}") from exc


def _validate_distinct_roots(visible_root: Path, answer_root: Path) -> None:
    try:
        visible = visible_root.resolve(strict=True)
        answers = answer_root.resolve(strict=True)
    except OSError as exc:
        raise BenchmarkError("benchmark roots must exist") from exc
    if visible_root.is_symlink() or answer_root.is_symlink():
        raise BenchmarkError("benchmark roots must not be symlinks")
    if not visible.is_dir() or not answers.is_dir():
        raise BenchmarkError("benchmark roots must be directories")
    if visible == answers or visible in answers.parents or answers in visible.parents:
        raise BenchmarkError("visible and hidden roots must be separate and non-nested")


def _validate_case_ids(
    items: Sequence[VisibleCase | HiddenAnswerKey | Prediction], label: str
) -> list[str]:
    case_ids = [item.case_id for item in items]
    if not case_ids:
        raise BenchmarkError(f"{label} must not be empty")
    if len(case_ids) != len(set(case_ids)):
        raise BenchmarkError(f"{label} case IDs must be unique")
    return case_ids


def _normalise_label(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _validate_hidden_label_isolation(
    visible: Sequence[VisibleCase], answers: Sequence[HiddenAnswerKey]
) -> None:
    """Reject direct answer-label disclosure in fields supplied to prediction generation."""

    for case, answer in zip(visible, answers, strict=True):
        visible_text = _normalise_label(case.incident_input)
        visible_identifiers = {
            _normalise_label(case.case_id),
            _normalise_label(case.family),
            *(_normalise_label(item) for item in case.evidence_ids),
            *(_normalise_label(item) for item in case.allowed_tools),
        }
        hidden_labels = [answer.root_cause_id, *answer.acceptable_alternates]
        for hidden_label in hidden_labels:
            normalised = _normalise_label(hidden_label)
            if not normalised:
                continue
            if normalised in visible_identifiers or re.search(
                rf"(?:^| ){re.escape(normalised)}(?: |$)", visible_text
            ):
                raise BenchmarkError(
                    f"visible case directly discloses hidden answer label: {case.case_id}"
                )


def load_benchmark(
    visible_dir: str | Path, answers_dir: str | Path
) -> tuple[list[VisibleCase], list[HiddenAnswerKey], str, str]:
    visible_root = Path(visible_dir)
    answer_root = Path(answers_dir)
    _validate_distinct_roots(visible_root, answer_root)
    visible = _load_model_list(visible_root / "cases.json", VisibleCase, "visible cases")
    answers = _load_model_list(answer_root / "answer-keys.json", HiddenAnswerKey, "answer keys")
    visible_ids = _validate_case_ids(visible, "visible benchmark")
    answer_ids = _validate_case_ids(answers, "answer keys")
    if answer_ids != visible_ids:
        raise BenchmarkError("answer keys must exactly match visible case IDs and order")
    _validate_hidden_label_isolation(visible, answers)
    return visible, answers, digest_models(visible), digest_models(answers)


def load_predictions(path: str | Path) -> list[Prediction]:
    predictions = _load_model_list(Path(path), Prediction, "predictions")
    _validate_case_ids(predictions, "predictions")
    return predictions


def resolve_ablation(cases: list[VisibleCase], config: Any) -> list[VisibleCase]:
    """Apply only agent-visible ablations; hidden answers are never consulted."""

    removed_terms: list[str] = []
    if config.remove_lineage:
        removed_terms.append("lineage")
    if config.remove_history:
        removed_terms.extend(["history", "historical"])
    if config.remove_contradictions:
        removed_terms.extend(["contradiction", "contradictory", "contradicts", "contradict"])

    def keep(value: str) -> bool:
        lowered = value.lower()
        return not any(term in lowered for term in removed_terms)

    def redact(value: str) -> str:
        result = value
        for term in sorted(removed_terms, key=len, reverse=True):
            result = re.sub(re.escape(term), "[REDACTED]", result, flags=re.IGNORECASE)
        return result

    return [
        case.model_copy(
            update={
                "incident_input": redact(case.incident_input),
                "evidence_ids": [item for item in case.evidence_ids if keep(item)],
                "allowed_tools": [item for item in case.allowed_tools if keep(item)],
            }
        )
        for case in cases
    ]


def resolve_prediction_ablation(
    predictions: list[Prediction], *, abstention_enabled: bool, max_hypotheses: int
) -> list[Prediction]:
    """Apply runtime configuration changes without reading hidden answer keys."""

    resolved: list[Prediction] = []
    for prediction in predictions:
        ranked = prediction.ranked_hypotheses[:max_hypotheses]
        probability_mass = math.fsum(prediction.probabilities[item] for item in ranked)
        if probability_mass <= 0.0:
            raise BenchmarkError("ablation removed all positive probability mass")
        probabilities = {item: prediction.probabilities[item] / probability_mass for item in ranked}
        payload = prediction.model_dump(mode="python")
        payload.update(
            {
                "ranked_hypotheses": ranked,
                "probabilities": probabilities,
                "abstained": prediction.abstained if abstention_enabled else False,
            }
        )
        resolved.append(Prediction.model_validate(payload))
    return resolved
