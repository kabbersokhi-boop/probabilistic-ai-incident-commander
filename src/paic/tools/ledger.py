"""Append-only canonical JSONL ledger with independently verifiable receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:  # pragma: no cover - available on supported Linux/macOS runners
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


_PATH_FIELDS = {
    "dataset_dir",
    "analytics_dir",
    "detection_dir",
    "impact_dir",
    "evidence_dir",
    "audit_dir",
}
_SECRET_MARKERS = ("api_key", "token", "secret", "password", "authorization")
_PATH_VALUE = re.compile(r"(?:(?:file:)?/[^\s'\"\\]+|[A-Za-z]:\\[^\s'\"\\]+)")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _PATH_FIELDS or lowered == "path" or lowered.endswith("_path"):
                continue
            if any(marker in lowered for marker in _SECRET_MARKERS):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _PATH_VALUE.sub("[PATH_REDACTED]", value)
    return value


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.flush()
            os.fsync(handle.fileno())
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class AuditLedger:
    def __init__(self, directory: str | Path):
        self.root = Path(directory)
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise ValueError("audit ledger root must be a regular non-symlink directory")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "invocations.jsonl"
        self.lock_path = self.root / ".ledger.lock"

    def _ensure_regular_files(self) -> None:
        for path in (self.path, self.lock_path):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("audit ledger paths must be regular non-symlink files")

    def append(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        policy: str,
        sources: dict[str, str],
    ) -> dict[str, Any]:
        safe_request = _redact(request)
        response_receipt = _redact(
            {key: value for key, value in response.items() if key != "result"}
        )
        self._ensure_regular_files()
        with _locked(self.lock_path):
            # Never extend a corrupted history. Validation runs while the same
            # process-wide ledger lock is held, so another cooperating writer
            # cannot modify the file between validation and append.
            self._ensure_regular_files()
            self.validate()
            records = (
                self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
            )
            previous = json.loads(records[-1])["record_sha256"] if records else "0" * 64
            record: dict[str, Any] = {
                "sequence": len(records) + 1,
                "previous_record_sha256": previous,
                "request": safe_request,
                "request_sha256": digest(safe_request),
                "response_receipt": response_receipt,
                "response_receipt_sha256": digest(response_receipt),
                "result_sha256": response["result_sha256"],
                "policy_outcome": policy,
                "source_manifest_hashes": dict(sorted(sources.items())),
                "call_id": response["call_id"],
                "tool": response["tool"],
            }
            record["record_sha256"] = digest(record)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def validate(self) -> None:
        self._ensure_regular_files()
        lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        previous = "0" * 64
        seen: set[str] = set()
        for expected, line in enumerate(lines, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("audit ledger contains invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError("audit ledger record must be an object")
            supplied = record.get("record_sha256")
            if not isinstance(supplied, str) or supplied in seen:
                raise ValueError("audit ledger record hash is invalid or duplicated")
            seen.add(supplied)
            if (
                record.get("sequence") != expected
                or record.get("previous_record_sha256") != previous
            ):
                raise ValueError("audit ledger sequence or chain is invalid")
            if record.get("request_sha256") != digest(record.get("request")):
                raise ValueError("audit ledger request hash is invalid")
            if record.get("response_receipt_sha256") != digest(record.get("response_receipt")):
                raise ValueError("audit ledger response receipt hash is invalid")
            receipt = record.get("response_receipt")
            if not isinstance(receipt, dict):
                raise ValueError("audit ledger response receipt is invalid")
            receipt_result_hash = receipt.get("result_sha256")
            if record.get("result_sha256") != receipt_result_hash:
                raise ValueError("audit ledger result hash is inconsistent with response receipt")
            unsigned = dict(record)
            unsigned.pop("record_sha256", None)
            if supplied != digest(unsigned):
                raise ValueError("audit ledger record hash is invalid")
            previous = supplied
