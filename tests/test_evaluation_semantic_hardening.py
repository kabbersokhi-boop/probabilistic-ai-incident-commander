from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.evaluation.benchmark import BenchmarkError, load_benchmark
from paic.evaluation.models import HiddenAnswerKey, Prediction, VisibleCase
from paic.evaluation.scoring import calibration_report, score_case


def test_confidence_tracks_ranked_top_hypothesis_probability() -> None:
    case = VisibleCase(case_id="case", family="family", incident_input="incident")
    answer = HiddenAnswerKey(case_id="case", root_cause_id="second")
    prediction = Prediction(
        case_id="case",
        ranked_hypotheses=["first", "second"],
        probabilities={"first": 0.4, "second": 0.6},
    )

    result = score_case(case, answer, prediction)
    ece, count, _ = calibration_report([answer], [prediction])

    assert not result.top1_correct
    assert result.confidence == 0.4
    assert count == 1
    assert ece == pytest.approx(0.4)


def test_authorized_action_outside_nonempty_allowlist_fails_safety() -> None:
    case = VisibleCase(case_id="case", family="family", incident_input="incident")
    answer = HiddenAnswerKey(
        case_id="case",
        root_cause_id="root",
        allowed_remediation_classes=["restart"],
    )
    prediction = Prediction(
        case_id="case",
        ranked_hypotheses=["root"],
        probabilities={"root": 1.0},
        proposed_remediation_class="delete-data",
        authorized_remediation_class="delete-data",
    )

    result = score_case(case, answer, prediction)

    assert result.remediation_correct is False
    assert result.prohibited_action_proposed
    assert result.prohibited_action_authorized


def test_benchmark_rejects_direct_hidden_label_disclosure(tmp_path: Path) -> None:
    visible = tmp_path / "visible"
    answers = tmp_path / "answers"
    visible.mkdir()
    answers.mkdir()
    (visible / "cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "case",
                    "family": "family",
                    "incident_input": "The root cause is checkout deployment.",
                }
            ]
        )
    )
    (answers / "answer-keys.json").write_text(
        json.dumps([{"case_id": "case", "root_cause_id": "checkout-deployment"}])
    )

    with pytest.raises(BenchmarkError, match="directly discloses"):
        load_benchmark(visible, answers)


def test_benchmark_rejects_hidden_label_in_structured_visible_fields(
    tmp_path: Path,
) -> None:
    visible = tmp_path / "visible"
    answers = tmp_path / "answers"
    visible.mkdir()
    answers.mkdir()
    (visible / "cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "case",
                    "family": "family",
                    "incident_input": "An incident occurred.",
                    "evidence_ids": ["checkout-deployment"],
                }
            ]
        )
    )
    (answers / "answer-keys.json").write_text(
        json.dumps([{"case_id": "case", "root_cause_id": "checkout-deployment"}])
    )

    with pytest.raises(BenchmarkError, match="directly discloses"):
        load_benchmark(visible, answers)
