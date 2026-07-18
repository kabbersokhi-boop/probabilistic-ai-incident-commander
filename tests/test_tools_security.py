from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from paic.tools.ledger import AuditLedger
from paic.tools.policy import authorize
from paic.tools.sql import SQLPolicyError, execute, validate_sql


def test_sql_blocks_external_table_functions_and_supports_aliases() -> None:
    frame = pl.DataFrame({"id": [1, 2], "value": ["a", "b"]})
    rows, truncated = execute("SELECT t.id FROM data t ORDER BY t.id", {"data": frame})
    assert rows == [{"id": 1}, {"id": 2}]
    assert not truncated
    attacks = [
        "SELECT * FROM parquet_scan('/etc/passwd')",
        "SELECT * FROM read_parquet('/tmp/x')",
        "SELECT * FROM sqlite_scan('/tmp/x', 'secrets')",
        "SELECT getenv('NVIDIA_API_KEY')",
        "SELECT * FROM data; SELECT * FROM data",
        "INSTALL httpfs",
    ]
    for query in attacks:
        with pytest.raises(SQLPolicyError):
            validate_sql(query, {"data"}, {"data": {"id", "value"}})


def test_policy_rejects_unknown_arguments() -> None:
    denied = authorize("investigator", "evidence.search", "1.0", {"query": "x", "bogus": 1})
    assert not denied.allowed
    assert denied.code == "invalid_arguments"
    allowed = authorize("investigator", "evidence.search", "1.0", {"query": "x"})
    assert allowed.allowed
    assert allowed.normalized_arguments["limit"] == 50


def test_ledger_reconstructs_request_and_receipt_hashes(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path)
    request = {
        "tool": "artifacts.summary",
        "dataset_dir": "/private/source",
        "arguments": {"api_key": "do-not-store"},
    }
    response = {
        "call_id": "c1",
        "tool": "artifacts.summary",
        "result": {"secret": "not-recorded"},
        "result_sha256": "a" * 64,
        "execution_status": "success",
    }
    ledger.append(request, response, policy="allow", sources={"dataset": "b" * 64})
    ledger.validate()
    text = ledger.path.read_text(encoding="utf-8")
    assert "/private/source" not in text
    assert "[PATH_REDACTED]" not in text
    assert "do-not-store" not in text
    assert "not-recorded" not in text
    raw = json.loads(text)
    raw["request_sha256"] = "0" * 64
    ledger.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="request hash"):
        ledger.validate()


def test_ledger_rejects_result_hash_not_bound_to_response_receipt(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path)
    response = {
        "call_id": "c1",
        "tool": "artifacts.summary",
        "result": {"private": "body"},
        "result_sha256": "a" * 64,
        "execution_status": "success",
    }
    ledger.append({"tool": "artifacts.summary"}, response, policy="allow", sources={})
    raw = json.loads(ledger.path.read_text(encoding="utf-8"))
    raw["result_sha256"] = "b" * 64
    raw["record_sha256"] = "0" * 64
    ledger.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="result hash"):
        ledger.validate()


def test_ledger_redacts_path_values_and_rejects_non_object_json(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path)
    ledger.append(
        {"tool": "sql.query", "arguments": {"query": "SELECT '/private/secrets'"}},
        {"call_id": "c1", "tool": "sql.query", "result": [], "result_sha256": "a" * 64},
        policy="allow",
        sources={},
    )
    assert "/private/secrets" not in ledger.path.read_text(encoding="utf-8")
    ledger.path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        ledger.validate()
