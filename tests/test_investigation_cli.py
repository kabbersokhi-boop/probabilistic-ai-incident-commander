from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.cli import main
from paic.evidence.io import load_evidence


def _script(evidence_ids: list[str]) -> dict[str, list[dict[str, object]]]:
    return {
        "nvidia/nemotron-3-super-120b-a12b": [
            {
                "model": "nvidia/nemotron-3-super-120b-a12b",
                "tool_calls": [
                    {
                        "id": "search",
                        "name": "evidence__search",
                        "arguments": {"query": "", "limit": 2},
                    }
                ],
                "usage": {"total_tokens": 5},
            },
            {
                "model": "nvidia/nemotron-3-super-120b-a12b",
                "tool_calls": [
                    {
                        "id": "submit",
                        "name": "submit_investigation",
                        "arguments": {
                            "summary": "The first hypothesis is favored.",
                            "hypotheses": [
                                {
                                    "hypothesis_id": "first",
                                    "title": "First hypothesis",
                                    "prior_probability": 0.5,
                                    "rationale": "Two evidence records support it.",
                                    "evidence": [
                                        {
                                            "evidence_record_id": evidence_ids[0],
                                            "direction": "support",
                                            "likelihood_ratio": 5,
                                            "explanation": "First evidence.",
                                        },
                                        {
                                            "evidence_record_id": evidence_ids[1],
                                            "direction": "support",
                                            "likelihood_ratio": 3,
                                            "explanation": "Second evidence.",
                                        },
                                    ],
                                    "falsifiers": [
                                        "The primary service stays healthy after the change."
                                    ],
                                },
                                {
                                    "hypothesis_id": "second",
                                    "title": "Second hypothesis",
                                    "prior_probability": 0.5,
                                    "rationale": "Competing explanation.",
                                    "evidence": [
                                        {
                                            "evidence_record_id": evidence_ids[1],
                                            "direction": "contradict",
                                            "likelihood_ratio": 0.2,
                                            "explanation": "Evidence contradicts it.",
                                        }
                                    ],
                                    "falsifiers": [
                                        "The alternative service lacks a matching regression."
                                    ],
                                },
                            ],
                        },
                    }
                ],
                "usage": {"total_tokens": 10},
            },
        ],
        "qwen/qwen3.5-122b-a10b": [],
        "nvidia/nemotron-3-nano-30b-a3b": [],
    }


def test_investigation_cli_scripted_run_validate_replay_and_benchmark(
    repo_root: Path,
    impact_smoke_dataset_dir: Path,
    evidence_smoke_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = (
        load_evidence(evidence_smoke_dir)
        .tables["evidence_records"]
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "incident_id": "checkout-address-validation-smoke",
                "question": "What caused the incident?",
                "dataset_dir": str(impact_smoke_dataset_dir),
                "evidence_dir": str(evidence_smoke_dir),
                "audit_dir": str(tmp_path / "audit"),
            }
        ),
        encoding="utf-8",
    )
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(_script(ids)), encoding="utf-8")
    output = tmp_path / "investigation"
    config = repo_root / "configs" / "investigation" / "smoke.yaml"

    assert main(["investigate", "models", "--config", str(config)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "investigate",
                "run",
                "--request",
                str(request_path),
                "--config",
                str(config),
                "--output-dir",
                str(output),
                "--provider-script",
                str(script_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "investigate",
                "validate",
                "--investigation-dir",
                str(output),
                "--dataset-dir",
                str(impact_smoke_dataset_dir),
                "--evidence-dir",
                str(evidence_smoke_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["investigate", "replay", "--investigation-dir", str(output)]) == 0
    capsys.readouterr()

    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "case_id": "smoke",
                    "investigation_dir": str(output),
                    "true_hypothesis_id": "first",
                    "should_abstain": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    assert main(["investigate", "benchmark", "--cases", str(cases)]) == 0
    benchmark = json.loads(capsys.readouterr().out)
    assert benchmark["top1_accuracy"] == 1.0
