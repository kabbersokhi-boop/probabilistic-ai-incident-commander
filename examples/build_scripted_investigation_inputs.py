"""Build deterministic offline inputs for the investigation smoke workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paic.evidence.io import load_evidence
from paic.investigation.config import load_investigation_config


def build_inputs(
    *,
    dataset_dir: Path,
    evidence_dir: Path,
    config_path: Path,
    analytics_dir: Path | None,
    detection_dir: Path | None,
    impact_dir: Path | None,
    request_path: Path,
    script_path: Path,
    audit_dir: Path,
) -> None:
    config = load_investigation_config(config_path)
    loaded_evidence = load_evidence(evidence_dir)
    records = loaded_evidence.tables["evidence_records"]
    supporting = (
        records.filter(
            (records["evidence_role"] == "supporting")
            & records["title"].str.to_lowercase().str.contains("address", literal=True)
        )
        .get_column("evidence_record_id")
        .head(2)
        .to_list()
    )
    contradictory = (
        records.filter(records["evidence_role"] == "contradictory")
        .get_column("evidence_record_id")
        .head(1)
        .to_list()
    )
    if len(supporting) != 2 or len(contradictory) != 1:
        raise RuntimeError(
            "offline smoke requires two address-related supporting records and one contradictory record"
        )
    support_one, support_two = supporting
    contradiction = contradictory[0]
    request = {
        "incident_id": loaded_evidence.manifest.incident_id,
        "question": "What is the best-supported cause of the checkout incident?",
        "dataset_dir": str(dataset_dir),
        "analytics_dir": str(analytics_dir) if analytics_dir else None,
        "detection_dir": str(detection_dir) if detection_dir else None,
        "impact_dir": str(impact_dir) if impact_dir else None,
        "evidence_dir": str(evidence_dir),
        "audit_dir": str(audit_dir),
    }
    responses = [
        {
            "model": config.provider.models[0].model,
            "tool_calls": [
                {
                    "id": "smoke-supporting-evidence",
                    "name": "evidence__search",
                    "arguments": {"query": "strict", "limit": 20},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        {
            "model": config.provider.models[0].model,
            "tool_calls": [
                {
                    "id": "smoke-contradictory-evidence",
                    "name": "evidence__search",
                    "arguments": {"query": "retry", "limit": 20},
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        },
        {
            "model": config.provider.models[0].model,
            "tool_calls": [
                {
                    "id": "smoke-submit",
                    "name": "submit_investigation",
                    "arguments": {
                        "summary": "A primary-service change is better supported than an unrelated-service explanation.",
                        "hypotheses": [
                            {
                                "hypothesis_id": "primary-service-change",
                                "title": "Primary-service change regression",
                                "prior_probability": 0.5,
                                "rationale": "Two validated records align with the affected service and incident window.",
                                "evidence": [
                                    {
                                        "evidence_record_id": support_one,
                                        "direction": "support",
                                        "likelihood_ratio": 5.0,
                                        "explanation": "The first record is temporally relevant to the incident.",
                                    },
                                    {
                                        "evidence_record_id": support_two,
                                        "direction": "support",
                                        "likelihood_ratio": 3.0,
                                        "explanation": "The second record independently supports the change hypothesis.",
                                    },
                                ],
                                "falsifiers": [
                                    "No recovery after reverting the implicated change."
                                ],
                            },
                            {
                                "hypothesis_id": "unrelated-service",
                                "title": "Unrelated downstream degradation",
                                "prior_probability": 0.5,
                                "rationale": "Retained as a competing explanation.",
                                "evidence": [
                                    {
                                        "evidence_record_id": contradiction,
                                        "direction": "contradict",
                                        "likelihood_ratio": 0.2,
                                        "explanation": "The payment change is explicitly documented as misaligned with the affected stage and cohort.",
                                    },
                                    {
                                        "evidence_record_id": support_two,
                                        "direction": "contradict",
                                        "likelihood_ratio": 0.4,
                                        "explanation": "The regional address-validation evidence points away from a downstream payment cause.",
                                    },
                                ],
                                "falsifiers": [
                                    "Downstream errors rise before the primary-service change."
                                ],
                            },
                        ],
                        "explicit_unknowns": ["Recovery has not yet been verified."],
                        "recommended_next_steps": [
                            "Run a read-only post-change cohort comparison and verify recovery."
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
        },
    ]
    script: dict[str, list[dict[str, object]]] = {
        route.model: [] for route in config.provider.models
    }
    script[config.provider.models[0].model] = responses
    request_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    script_path.write_text(json.dumps(script, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--analytics-dir", type=Path)
    parser.add_argument("--detection-dir", type=Path)
    parser.add_argument("--impact-dir", type=Path)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    args = parser.parse_args()
    build_inputs(
        dataset_dir=args.dataset_dir,
        evidence_dir=args.evidence_dir,
        config_path=args.config,
        analytics_dir=args.analytics_dir,
        detection_dir=args.detection_dir,
        impact_dir=args.impact_dir,
        request_path=args.request,
        script_path=args.script,
        audit_dir=args.audit_dir,
    )


if __name__ == "__main__":
    main()
