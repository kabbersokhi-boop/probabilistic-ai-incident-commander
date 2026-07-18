"""Separate visible benchmark inputs from hidden answer keys."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from paic.evaluation.models import HiddenAnswerKey, Prediction, VisibleCase


class BenchmarkError(RuntimeError):
    pass


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_benchmark(
    visible_dir: str | Path, answers_dir: str | Path
) -> tuple[list[VisibleCase], list[HiddenAnswerKey], str, str]:
    visible_root = Path(visible_dir)
    answer_root = Path(answers_dir)
    if (
        visible_root.resolve() == answer_root.resolve()
        or answer_root.resolve() in visible_root.resolve().parents
    ):
        raise BenchmarkError("hidden answers must be outside visible benchmark inputs")
    visible_path = visible_root / "cases.json"
    answer_path = answer_root / "answer-keys.json"
    try:
        visible = [
            VisibleCase.model_validate(item) for item in json.loads(visible_path.read_text())
        ]
        answers = [
            HiddenAnswerKey.model_validate(item) for item in json.loads(answer_path.read_text())
        ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid benchmark: {exc}") from exc
    visible_ids = [item.case_id for item in visible]
    answer_ids = [item.case_id for item in answers]
    if not visible_ids or len(visible_ids) != len(set(visible_ids)):
        raise BenchmarkError("visible case IDs must be non-empty and unique")
    if answer_ids != visible_ids or len(answer_ids) != len(set(answer_ids)):
        raise BenchmarkError("answer keys must exactly match visible case IDs and order")
    return visible, answers, digest_file(visible_path), digest_file(answer_path)


def load_predictions(path: str | Path) -> list[Prediction]:
    try:
        return [Prediction.model_validate(item) for item in json.loads(Path(path).read_text())]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid predictions: {exc}") from exc
