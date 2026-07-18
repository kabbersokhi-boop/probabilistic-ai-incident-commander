"""AST-checked, in-memory DuckDB execution."""

from __future__ import annotations

import json
from typing import Any

import duckdb
import polars as pl
import sqlglot
from sqlglot import exp


class SQLPolicyError(ValueError):
    pass


def validate_sql(
    query: str, tables: set[str], columns: dict[str, set[str]], max_complexity: int = 80
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
    if not isinstance(tree, (exp.Select, exp.Union)) and not (
        isinstance(tree, exp.With) or tree.find(exp.Select)
    ):
        raise SQLPolicyError("only SELECT statements are allowed")
    forbidden = tuple(
        cls
        for name in (
            "Insert",
            "Update",
            "Delete",
            "Merge",
            "Create",
            "Alter",
            "Drop",
            "Command",
            "Copy",
            "Attach",
            "Detach",
            "Install",
            "Load",
            "Pragma",
            "Call",
            "Transaction",
        )
        if (cls := getattr(exp, name, None)) is not None
    )
    if any(tree.find(cls) for cls in forbidden):
        raise SQLPolicyError("statement is not read-only")
    for node in tree.walk():
        if isinstance(node, exp.Anonymous) and (
            str(node.name).lower()
            in {
                "read_csv",
                "read_parquet",
                "httpfs",
                "read_json",
                "glob",
                "getenv",
                "current_setting",
                "read_text",
                "read_blob",
                "http_get",
            }
            or str(node.name).lower().startswith(("read_", "http", "load_"))
        ):
            raise SQLPolicyError("external table functions are forbidden")
        if isinstance(node, exp.Table):
            name = node.name
            cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
            if (name not in tables and name not in cte_names) or node.db or node.catalog:
                raise SQLPolicyError(f"unknown table: {name}")
        if isinstance(node, exp.Column):
            if node.table and node.table not in tables:
                raise SQLPolicyError("unknown table qualifier")
            if (
                node.name != "*"
                and not node.table
                and not any(node.name in values for values in columns.values())
            ):
                raise SQLPolicyError(f"unknown column: {node.name}")
    if sum(1 for _ in tree.walk()) > max_complexity:
        raise SQLPolicyError("query complexity exceeds limit")
    return tree


def execute(
    query: str, frames: dict[str, pl.DataFrame], row_limit: int = 500, byte_limit: int = 100_000
) -> tuple[list[dict[str, Any]], bool]:
    columns = {name: set(frame.columns) for name, frame in frames.items()}
    tree = validate_sql(query, set(frames), columns)
    conn = duckdb.connect(database=":memory:")
    try:
        for name, frame in frames.items():
            conn.register(name, frame.to_arrow())
        try:
            arrow = conn.execute(tree.sql(dialect="duckdb")).to_arrow_table()
        except Exception as exc:
            raise SQLPolicyError("query execution failed") from exc
        truncated = arrow.num_rows > row_limit
        rows = arrow.slice(0, row_limit).to_pylist()
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()
        if len(encoded) > byte_limit:
            while (
                rows
                and len(
                    json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()
                )
                > byte_limit
            ):
                rows.pop()
            truncated = True
        return rows, truncated
    finally:
        conn.close()
