"""AST-checked, bounded, in-memory DuckDB execution."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

import duckdb
import polars as pl
import sqlglot
from sqlglot import exp


class SQLPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class SQLPolicy:
    max_complexity: int = 80
    timeout_seconds: float = 3.0
    row_limit: int = 500
    byte_limit: int = 100_000
    memory_limit: str = "256MB"


def _alias_map(tree: exp.Expression) -> tuple[dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue
        if table.name in ctes:
            aliases[table.alias_or_name or table.name] = table.name
        else:
            aliases[table.alias_or_name or table.name] = table.name
    return aliases, ctes


def validate_sql(
    query: str,
    tables: set[str],
    columns: dict[str, set[str]],
    max_complexity: int = 80,
) -> exp.Expression:
    if not isinstance(query, str) or not query.strip():
        raise SQLPolicyError("query is required")
    try:
        parsed = sqlglot.parse(query, read="duckdb")
    except Exception as exc:
        raise SQLPolicyError("invalid SQL") from exc
    if len(parsed) != 1:
        raise SQLPolicyError("multiple statements are not allowed")
    tree = parsed[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLPolicyError("only SELECT statements are allowed")
    forbidden_names = {
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Alter",
        "Drop",
        "TruncateTable",
        "Command",
        "Copy",
        "Attach",
        "Detach",
        "Install",
        "Load",
        "Pragma",
        "Call",
        "Transaction",
        "Use",
        "Grant",
        "Revoke",
    }
    forbidden = tuple(
        cls for name in forbidden_names if (cls := getattr(exp, name, None)) is not None
    )
    if any(tree.find(cls) for cls in forbidden):
        raise SQLPolicyError("statement is not read-only")
    aliases, cte_names = _alias_map(tree)
    for node in tree.walk():
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                raise SQLPolicyError("table functions are forbidden")
            name = node.name
            if node.db or node.catalog:
                raise SQLPolicyError("catalog and schema access are forbidden")
            if name not in tables and name not in cte_names:
                raise SQLPolicyError(f"unknown table: {name}")
        if isinstance(node, (exp.FileFormatProperty, exp.LocationProperty)):
            raise SQLPolicyError("filesystem access is forbidden")
        if isinstance(node, exp.Anonymous):
            name = str(node.name).lower()
            if name in {
                "getenv",
                "current_setting",
                "query",
                "query_table",
                "query_table_range",
            } or name.startswith(
                (
                    "read_",
                    "http",
                    "parquet_",
                    "csv_",
                    "json_",
                    "sqlite_",
                    "postgres_",
                    "mysql_",
                    "iceberg_",
                    "delta_",
                    "azure_",
                    "s3_",
                    "glob",
                    "load_",
                )
            ):
                raise SQLPolicyError("external or dynamic functions are forbidden")
        if isinstance(node, exp.Column):
            if node.name == "*":
                continue
            qualifier = node.table
            if qualifier:
                underlying = aliases.get(qualifier)
                if underlying is None:
                    raise SQLPolicyError(f"unknown table qualifier: {qualifier}")
                if underlying in columns and node.name not in columns[underlying]:
                    raise SQLPolicyError(f"unknown column: {qualifier}.{node.name}")
            elif not any(node.name in values for values in columns.values()):
                raise SQLPolicyError(f"unknown column: {node.name}")
    if sum(1 for _ in tree.walk()) > max_complexity:
        raise SQLPolicyError("query complexity exceeds limit")
    return tree


def execute(
    query: str,
    frames: dict[str, pl.DataFrame],
    *,
    policy: SQLPolicy | None = None,
    requested_limit: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    active = policy or SQLPolicy()
    row_limit = min(requested_limit or active.row_limit, active.row_limit)
    columns = {name: set(frame.columns) for name, frame in frames.items()}
    tree = validate_sql(query, set(frames), columns, active.max_complexity)
    conn = duckdb.connect(database=":memory:")
    timer = threading.Timer(active.timeout_seconds, conn.interrupt)
    try:
        conn.execute("SET threads=1")
        conn.execute(f"SET memory_limit='{active.memory_limit}'")
        conn.execute("SET enable_external_access=false")
        for name, frame in frames.items():
            conn.register(name, frame.to_arrow())
        timer.start()
        try:
            arrow = conn.execute(tree.sql(dialect="duckdb")).to_arrow_table()
        except Exception as exc:
            message = str(exc).lower()
            if "interrupt" in message:
                raise SQLPolicyError("query execution timed out") from exc
            raise SQLPolicyError("query execution failed") from exc
        rows = arrow.to_pylist()
        if tree.args.get("order") is None:
            rows.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        while rows:
            encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()
            if len(encoded) <= active.byte_limit:
                break
            rows.pop()
            truncated = True
        return rows, truncated
    finally:
        timer.cancel()
        conn.close()
