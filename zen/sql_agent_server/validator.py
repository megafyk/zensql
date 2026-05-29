"""SQL safety validator.

Post-generation gate. Runs in order:
1. Denylist keyword pre-check (defense in depth — catches anything sqlglot
   might miscategorize).
2. sqlglot parse (dialect="mysql"; MariaDB is mostly compatible).
3. Exactly one statement.
4. Statement family ∈ allowed_families (default {SELECT}).
5. Identifier verification against `retrieved_metadata` (strict → reject;
   non-strict → warn).
6. Risk-pattern warnings: `SELECT *`, missing `LIMIT`, suspicious functions.
7. Prepend the §10.7 banner to the validated SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlglot
from sqlglot import exp

from zen.models.metadata import TableMetadata
from zen.models.safety import SafetyViolation

# Map sqlglot expression types to family strings.
_FAMILY_BY_TYPE: dict[type[exp.Expression], str] = {
    exp.Select: "SELECT",
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Drop: "DROP",
    exp.Create: "CREATE",
    exp.Alter: "ALTER",
    exp.TruncateTable: "TRUNCATE",
    exp.Set: "SET",
    exp.Commit: "COMMIT",
    exp.Rollback: "ROLLBACK",
    exp.Transaction: "TRANSACTION",
    exp.Command: "COMMAND",
    exp.Use: "USE",
    exp.Grant: "GRANT",
    exp.Union: "UNION",
}

# Denied keywords — matched word-bounded against the raw SQL text.
_DENIED_KEYWORDS: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXEC",
    "EXECUTE",
    "LOAD",
    "HANDLER",
    "REPLACE",
    "RENAME",
    "LOCK",
    "UNLOCK",
    "PREPARE",
    "DEALLOCATE",
    "ATTACH",
    "DETACH",
)
_DENIED_KEYWORDS_RE = re.compile(
    r"(?i)(?<![\w])(?:" + "|".join(_DENIED_KEYWORDS) + r")(?![\w])"
)
_TRANSACTION_RE = re.compile(
    r"(?i)\b(start\s+transaction|begin\s+transaction|commit\s*;?$|rollback\s*;?$)"
)

# Suspicious functions — emitted as warnings.
_SUSPICIOUS_FUNCS = {"LOAD_FILE", "SLEEP", "BENCHMARK", "OUTFILE", "GET_LOCK"}


@dataclass
class ValidationReport:
    ok: bool
    summary: str = ""
    violations: list[SafetyViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables_referenced: list[str] = field(default_factory=list)
    sql_with_banner: str | None = None


def _strip_sql_comments(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", no_block)


def _banner(request_id: str | None, generated_at: str) -> str:
    rid = request_id or "n/a"
    return (
        "-- AI-GENERATED SQL — REVIEW BEFORE EXECUTING\n"
        f"-- request_id: {rid}\n"
        f"-- generated_at: {generated_at}\n"
        "-- This SQL was not executed by the system.\n"
    )


class SqlSafetyValidator:
    def __init__(
        self,
        *,
        allowed_families: set[str] | None = None,
        strict_identifier_check: bool = True,
    ) -> None:
        self._allowed = {f.upper() for f in (allowed_families or {"SELECT"})}
        self._strict = strict_identifier_check

    def validate(
        self,
        sql: str,
        retrieved_metadata: list[TableMetadata] | None = None,
        *,
        request_id: str | None = None,
    ) -> ValidationReport:
        rep = ValidationReport(ok=True)

        cleaned = sql.strip()
        if not cleaned:
            return _fail(rep, "EMPTY_SQL", "empty SQL")

        # 1. Denylist keyword pre-check on the comment-stripped raw text.
        stripped = _strip_sql_comments(cleaned)
        m = _DENIED_KEYWORDS_RE.search(stripped)
        if m:
            kw = m.group(0).upper()
            if kw not in self._allowed:
                return _fail(
                    rep,
                    "STATEMENT_FAMILY_DENIED",
                    f"denied keyword {kw} in SQL",
                    evidence={"keyword": kw},
                )
        if _TRANSACTION_RE.search(stripped):
            return _fail(rep, "STATEMENT_FAMILY_DENIED", "transaction control not allowed")

        # 2. sqlglot parse.
        try:
            statements = sqlglot.parse(cleaned, dialect="mysql")
        except (sqlglot.errors.ParseError, ValueError) as e:
            return _fail(rep, "UNPARSEABLE", f"sqlglot parse error: {e}")

        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            return _fail(
                rep,
                "MULTI_STATEMENT",
                f"exactly one statement permitted, got {len(statements)}",
            )

        stmt = statements[0]
        family = _FAMILY_BY_TYPE.get(type(stmt))
        if family is None:
            family = type(stmt).__name__.upper()
        if family not in self._allowed:
            return _fail(
                rep,
                "STATEMENT_FAMILY_DENIED",
                f"{family} not in allowed families {sorted(self._allowed)}",
                evidence={"family": family},
            )

        # SELECT ... INTO @var / OUTFILE / DUMPFILE — refuse even though family is SELECT.
        if isinstance(stmt, exp.Select) and stmt.args.get("into"):
            return _fail(
                rep,
                "STATEMENT_FAMILY_DENIED",
                "SELECT INTO not permitted",
            )

        # 3. Identifier verification.
        tables_seen: list[str] = []
        if retrieved_metadata is not None:
            known_tables: dict[tuple[str, str], TableMetadata] = {}
            known_tables_any_schema: dict[str, TableMetadata] = {}
            for t in retrieved_metadata:
                known_tables[(t.schema_, t.name)] = t
                known_tables_any_schema[t.name] = t

            unknown_tables: list[str] = []
            unknown_columns: list[str] = []
            referenced_columns: list[tuple[exp.Column, TableMetadata]] = []

            for table_ref in stmt.find_all(exp.Table):
                schema = table_ref.db or ""
                name = table_ref.name
                key = (schema, name)
                matched: TableMetadata | None = (
                    known_tables.get(key) if schema else known_tables_any_schema.get(name)
                )
                if matched is None:
                    unknown_tables.append(f"{schema + '.' if schema else ''}{name}")
                else:
                    tables_seen.append(f"{matched.schema_}.{matched.name}")
                    for col in stmt.find_all(exp.Column):
                        if col.table and col.table != name:
                            continue
                        if col.name == "*":
                            continue
                        referenced_columns.append((col, matched))

            # If multiple tables present and a column isn't qualified, we skip
            # validation for it (sqlglot doesn't bind columns to tables for us).
            for col, t in referenced_columns:
                col_names = {c.name for c in t.columns}
                if col.name not in col_names:
                    unknown_columns.append(f"{t.name}.{col.name}")

            if unknown_tables:
                rep.warnings.append(
                    f"unknown tables not in retrieved metadata: {unknown_tables}"
                )
                if self._strict:
                    return _fail(
                        rep,
                        "IDENTIFIER_NOT_VERIFIED",
                        f"unknown tables: {unknown_tables}",
                        evidence={"tables": unknown_tables},
                    )
            if unknown_columns:
                rep.warnings.append(
                    f"unknown columns not in retrieved metadata: {unknown_columns}"
                )
                if self._strict:
                    return _fail(
                        rep,
                        "IDENTIFIER_NOT_VERIFIED",
                        f"unknown columns: {unknown_columns}",
                        evidence={"columns": unknown_columns},
                    )

        # 4. Risk-pattern warnings.
        if any(isinstance(s, exp.Star) for s in stmt.find_all(exp.Star)):
            rep.warnings.append("SELECT * detected — prefer explicit column lists")

        if isinstance(stmt, exp.Select) and not stmt.args.get("limit"):
            rep.warnings.append(
                "no LIMIT clause — query may return a very large result set"
            )

        for fn in stmt.find_all(exp.Anonymous):
            name = (fn.name or "").upper()
            if name in _SUSPICIOUS_FUNCS:
                rep.warnings.append(f"suspicious function used: {name}()")

        # 5. Tables referenced (de-dup, preserve order).
        if not tables_seen:
            for table_ref in stmt.find_all(exp.Table):
                qualified = (
                    f"{table_ref.db}.{table_ref.name}"
                    if table_ref.db
                    else table_ref.name
                )
                if qualified not in tables_seen:
                    tables_seen.append(qualified)
        rep.tables_referenced = list(dict.fromkeys(tables_seen))

        # 6. Banner.
        now = datetime.now(UTC).isoformat()
        rep.sql_with_banner = _banner(request_id, now) + cleaned.rstrip("; \n") + ";\n"
        return rep


def _fail(
    rep: ValidationReport,
    rule: str,
    detail: str,
    *,
    evidence: dict[str, object] | None = None,
) -> ValidationReport:
    rep.ok = False
    rep.summary = detail
    rep.violations.append(
        SafetyViolation(rule=rule, detail=detail, evidence=evidence or {})
    )
    return rep
