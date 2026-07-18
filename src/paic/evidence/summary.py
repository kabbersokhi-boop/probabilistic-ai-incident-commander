"""Summaries for operational evidence artifacts."""

from __future__ import annotations

import json

import polars as pl

from paic.evidence.types import LoadedEvidence


def build_evidence_summary(loaded: LoadedEvidence) -> dict[str, object]:
    records = loaded.tables["evidence_records"]
    health = loaded.tables["service_health"]
    quality = loaded.tables["evidence_quality_results"]
    return {
        "manifest": loaded.manifest.model_dump(mode="json"),
        "evidence_types": records.group_by("evidence_type").len().sort("evidence_type").to_dicts(),
        "roles": records.group_by("evidence_role").len().sort("evidence_role").to_dicts(),
        "service_health": health.group_by("service", "status")
        .len()
        .sort("service", "status")
        .to_dicts(),
        "timeline_preview": loaded.tables["incident_timeline"].head(20).to_dicts(),
        "quality": {
            "passed": quality.filter(pl.col("status") == "pass").height,
            "failed": quality.filter(pl.col("status") == "fail").height,
            "warnings": quality.filter(pl.col("status") == "warn").height,
        },
    }


def evidence_summary_json(loaded: LoadedEvidence) -> str:
    return json.dumps(build_evidence_summary(loaded), indent=2, sort_keys=True, default=str)
