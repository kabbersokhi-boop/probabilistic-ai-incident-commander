"""Append-only hash-chained invocation ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class AuditLedger:
    def __init__(self, directory: str | Path):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "invocations.jsonl"

    def append(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        policy: str,
        sources: dict[str, str],
    ) -> dict[str, Any]:
        records = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        previous = json.loads(records[-1])["record_sha256"] if records else "0" * 64
        safe_request = {
            k: v
            for k, v in request.items()
            if k
            not in {
                "dataset_dir",
                "analytics_dir",
                "detection_dir",
                "impact_dir",
                "evidence_dir",
                "audit_dir",
            }
        }
        safe_response = {k: v for k, v in response.items() if k != "result"}
        record = {
            "sequence": len(records) + 1,
            "previous_record_sha256": previous,
            "request_sha256": digest(safe_request),
            "result_sha256": response["result_sha256"],
            "policy_outcome": policy,
            "source_manifest_hashes": sources,
            "call_id": response["call_id"],
            "tool": response["tool"],
            "response": safe_response,
        }
        record["record_sha256"] = digest(record)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical(record) + "\n")
        return record

    def validate(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        previous = "0" * 64
        for expected, line in enumerate(lines, 1):
            record = json.loads(line)
            if (
                record.get("sequence") != expected
                or record.get("previous_record_sha256") != previous
            ):
                raise ValueError("audit ledger sequence or chain is invalid")
            supplied = record.pop("record_sha256", None)
            if supplied != digest(record):
                raise ValueError("audit ledger record hash is invalid")
            previous = supplied
