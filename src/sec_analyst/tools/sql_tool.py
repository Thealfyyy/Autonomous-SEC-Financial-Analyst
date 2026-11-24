"""Read-only SQL validation and safe execution for XBRL quantitative queries."""

from __future__ import annotations

import re
from typing import Any, Protocol

import psycopg
from pydantic import BaseModel

ALLOWED_RELATIONS: frozenset[str] = frozenset(
    {"v_latest_xbrl_facts", "xbrl_facts", "filings"}
)

FORBIDDEN_KEYWORDS_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"COPY|EXEC|EXECUTE|CALL|MERGE|VACUUM|SET|LOCK|COMMENT|REPLACE|ATTACH)\b",
    re.IGNORECASE,
)

# Matches FROM <table_name> or JOIN <table_name>
RELATION_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
    re.IGNORECASE,
)

LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


class SqlValidationError(ValueError):
    """Raised when a generated SQL query violates read-only or allowlist guardrails."""


class SqlResult(BaseModel):
    """Structured output returned by the read-only SQL tool."""

    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class SqlExecutor(Protocol):
    """Protocol for read-only SQL execution (real Postgres or test fake)."""

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> SqlResult: ...


def _strip_sql_comments(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line = re.sub(r"--[^\r\n]*", " ", no_block)
    return no_line.strip()


def validate_readonly_sql(raw_sql: str) -> str:
    """Validate and normalize a read-only SELECT/WITH query against allowed XBRL tables."""
    cleaned = _strip_sql_comments(raw_sql)
    if not cleaned:
        raise SqlValidationError("SQL query is empty.")

    # Allow at most one trailing semicolon; reject multi-statement injection.
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        raise SqlValidationError("Multi-statement SQL queries are strictly forbidden.")

    if not re.match(r"^(SELECT|WITH)\b", cleaned, flags=re.IGNORECASE):
        raise SqlValidationError("Only SELECT or WITH (CTE) read-only queries are allowed.")

    forbidden = FORBIDDEN_KEYWORDS_RE.search(cleaned)
    if forbidden:
        raise SqlValidationError(
            f"Forbidden SQL keyword detected: '{forbidden.group(1).upper()}'."
        )

    # Ensure every referenced relation is in our allowlist (or a CTE alias defined in WITH).
    cte_names = {
        m.lower()
        for m in re.findall(r"(?:\bWITH|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(", cleaned, re.IGNORECASE)
    }
    for match in RELATION_REF_RE.finditer(cleaned):
        rel = match.group(1).lower().removeprefix("public.")
        if rel not in ALLOWED_RELATIONS and rel not in cte_names:
            raise SqlValidationError(
                f"Access to relation '{match.group(1)}' is not permitted. "
                f"Allowed: {sorted(ALLOWED_RELATIONS)}"
            )

    limit_match = LIMIT_RE.search(cleaned)
    if limit_match is None:
        cleaned = f"{cleaned} LIMIT {DEFAULT_LIMIT}"
    elif int(limit_match.group(1)) > MAX_LIMIT:
        raise SqlValidationError(f"LIMIT cannot exceed {MAX_LIMIT} rows.")

    return cleaned


class PostgresReadOnlySqlExecutor:
    """Executes validated read-only SQL against PostgreSQL with a 5s statement timeout."""

    def __init__(self, readonly_database_url: str, timeout_ms: int = 5000) -> None:
        self.readonly_database_url = readonly_database_url
        self.timeout_ms = timeout_ms

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> SqlResult:
        safe_sql = validate_readonly_sql(sql)
        with psycopg.connect(self.readonly_database_url) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL TRANSACTION READ ONLY;")
            cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)};")
            cur.execute(safe_sql, params or ())
            columns = [desc.name for desc in (cur.description or [])]
            raw_rows = cur.fetchall()

        dict_rows = [dict(zip(columns, row, strict=True)) for row in raw_rows]
        return SqlResult(
            sql=safe_sql,
            columns=columns,
            rows=dict_rows,
            row_count=len(dict_rows),
        )
