"""SQL safety validator.

Post-generation gate. Runs in order:
1. Refuse MySQL executable comments (`/*! ... */` — MariaDB runs their
   contents), then denylist keyword pre-check on text with string literals,
   quoted identifiers, and comments stripped (defense in depth — catches
   anything sqlglot might miscategorize, without false-positives on quoted
   text).
2. sqlglot parse (dialect="mysql"; MariaDB is mostly compatible).
3. Exactly one statement.
4. Statement family ∈ allowed_families (default {SELECT}; UNION/INTERSECT/
   EXCEPT of SELECTs count as SELECT, with INTO refused on every leaf).
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

# Map sqlglot expression types to family strings. Set operations over SELECTs
# (UNION/INTERSECT/EXCEPT) are read-only, so they count as the SELECT family;
# the per-leaf INTO check below still refuses SELECT INTO in any arm.
_FAMILY_BY_TYPE: dict[type[exp.Expr], str] = {
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
    exp.Union: "SELECT",
    exp.Intersect: "SELECT",
    exp.Except: "SELECT",
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


# One left-to-right lexical scan over string literals, quoted identifiers, and
# comments. Whichever construct opens first wins, so quotes inside comments and
# comment markers inside strings don't confuse the keyword pre-check.
# `--` only starts a MySQL comment when followed by whitespace or end-of-line
# (`--x` is double minus) — mirroring that rule keeps this lexer from hiding
# tokens the server would execute.
_LEXICAL_NOISE_RE = re.compile(
    r"""
      '(?:[^'\\]|\\.|'')*'      # single-quoted string ('' and \' escapes)
    | "(?:[^"\\]|\\.|"")*"      # double-quoted string
    | `[^`]*(?:``[^`]*)*`       # backtick-quoted identifier
    | /\*.*?\*/                 # block comment
    | --(?=\s|$)[^\n]*          # -- line comment (MySQL requires whitespace)
    | \#[^\n]*                  # MySQL \# line comment
    """,
    re.VERBOSE | re.DOTALL,
)

# MySQL `/*!` and MariaDB `/*M!` executable comments. Matched against the RAW
# text — deliberately also inside string literals — because no legitimate
# read-only draft ever contains the marker, while any lexer-confusion trick
# that hides one from a smarter scan would hand the human reviewer SQL that
# MariaDB executes.
_EXECUTABLE_COMMENT_RE = re.compile(r"/\*M?!", re.IGNORECASE)


def _strip_literals_and_comments(sql: str) -> str:
    return _LEXICAL_NOISE_RE.sub(" ", sql)


def _find_executable_comment(sql: str) -> str | None:
    """Return the start of the first `/*!`/`/*M!` executable-comment marker,
    or None. MariaDB/MySQL execute their contents, so a 'safe' SELECT could
    smuggle a payload past both the denylist and sqlglot."""
    m = _EXECUTABLE_COMMENT_RE.search(sql)
    if m is None:
        return None
    return sql[m.start() : m.start() + 120]


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

        # 1. Denylist keyword pre-check, with string literals, quoted
        # identifiers, and comments removed so e.g. `note = 'please update'`
        # is not a false positive. Executable comments are refused outright
        # because MariaDB runs their contents.
        executable = _find_executable_comment(cleaned)
        if executable:
            return _fail(
                rep,
                "EXECUTABLE_COMMENT",
                "executable comment (/*! or /*M!) not allowed",
                evidence={"comment": executable[:120]},
            )
        stripped = _strip_literals_and_comments(cleaned)
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

        parsed: list[exp.Expr] = [s for s in statements if s is not None]
        if len(parsed) != 1:
            return _fail(
                rep,
                "MULTI_STATEMENT",
                f"exactly one statement permitted, got {len(parsed)}",
            )

        stmt = parsed[0]
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

        # SELECT ... INTO @var / OUTFILE / DUMPFILE — refuse even though family
        # is SELECT. Checked on every SELECT leaf so UNION arms are covered.
        for sel in stmt.find_all(exp.Select):
            if sel.args.get("into"):
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
            matched_by_name: dict[str, TableMetadata] = {}

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
                    matched_by_name[matched.name] = matched

            # Columns qualified with a real table name are checked against that
            # table. Unqualified columns are checked only when exactly one known
            # table is in play (sqlglot doesn't bind columns to tables for us);
            # with multiple tables — or an alias qualifier — they are skipped.
            sole_table = (
                next(iter(matched_by_name.values()))
                if len(matched_by_name) == 1 and not unknown_tables
                else None
            )
            for col in stmt.find_all(exp.Column):
                if col.name == "*":
                    continue
                target = matched_by_name.get(col.table) if col.table else sole_table
                if target is None:
                    continue
                if col.name not in {c.name for c in target.columns}:
                    ref = f"{target.name}.{col.name}"
                    if ref not in unknown_columns:
                        unknown_columns.append(ref)

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

        # 4. Risk-pattern warnings. Only select-list stars count — COUNT(*)
        # and other aggregate stars are not a SELECT *.
        if any(
            isinstance(e, exp.Star)
            or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
            for sel in stmt.find_all(exp.Select)
            for e in sel.expressions
        ):
            rep.warnings.append("SELECT * detected — prefer explicit column lists")

        has_limit = bool(stmt.args.get("limit")) or (
            # A set operation is bounded when every arm carries its own LIMIT.
            isinstance(stmt, exp.SetOperation)
            and all(sel.args.get("limit") for sel in stmt.find_all(exp.Select))
        )
        if isinstance(stmt, (exp.Select, exp.SetOperation)) and not has_limit:
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
