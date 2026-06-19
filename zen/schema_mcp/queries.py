"""SQL-template builders for `information_schema.*` queries.

These are the only SQL strings the Schema MCP ever submits to Metabase.
Identifier inputs (schema/table names) are regex-validated before being
interpolated; the LIKE pattern in `build_search_query` is escaped. The
Metabase client's `_assert_information_schema_only` chokepoint re-parses
each generated SQL string before any HTTP call as defense-in-depth.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from zen.mcp_tools.errors import WriteAttemptError

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SEARCH_PATTERN_MAX_LEN = 64
_LIMIT_MAX = 200


def _validate_identifier(name: str, kind: str = "identifier") -> str:
    if not _IDENT_RE.match(name):
        raise WriteAttemptError(f"invalid {kind} {name!r}")
    return name


def _in_list_sql(names: Iterable[str], kind: str) -> str:
    validated = [_validate_identifier(n, kind) for n in names]
    if not validated:
        return "(NULL)"
    return "(" + ",".join(f"'{n}'" for n in validated) + ")"


# LIKE escape character. Deliberately not backslash: backslash is also the
# MySQL string-literal escape, and the doubly-escaped result ("ESCAPE '\'")
# is unparseable — the backslash eats the closing quote.
_LIKE_ESCAPE_CHAR = "|"


def _escape_like(pattern: str) -> str:
    return (
        pattern.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{_LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{_LIKE_ESCAPE_CHAR}_")
        # Backslash is consumed by the string-literal layer — double it there.
        .replace("\\", "\\\\")
        .replace("'", "''")
    )


def build_columns_query(schemas: Iterable[str], tables: Iterable[str]) -> str:
    return (
        "SELECT table_schema, table_name, column_name, data_type, "
        "is_nullable, column_default, column_key, extra, column_comment, "
        "ordinal_position "
        "FROM information_schema.columns "
        f"WHERE table_schema IN {_in_list_sql(schemas, 'schema')} "
        f"AND table_name IN {_in_list_sql(tables, 'table')} "
        "ORDER BY table_schema, table_name, ordinal_position"
    )


def build_indexes_query(schemas: Iterable[str], tables: Iterable[str]) -> str:
    return (
        "SELECT table_schema, table_name, index_name, column_name, "
        "non_unique, index_type, seq_in_index "
        "FROM information_schema.statistics "
        f"WHERE table_schema IN {_in_list_sql(schemas, 'schema')} "
        f"AND table_name IN {_in_list_sql(tables, 'table')} "
        "ORDER BY table_schema, table_name, index_name, seq_in_index"
    )


def build_partitions_query(schemas: Iterable[str], tables: Iterable[str]) -> str:
    return (
        "SELECT table_schema, table_name, partition_name, partition_method, "
        "partition_expression, partition_description, table_rows "
        "FROM information_schema.partitions "
        f"WHERE table_schema IN {_in_list_sql(schemas, 'schema')} "
        f"AND table_name IN {_in_list_sql(tables, 'table')} "
        "AND partition_name IS NOT NULL"
    )


def build_relationships_query(schemas: Iterable[str], tables: Iterable[str]) -> str:
    return (
        "SELECT table_schema, table_name, column_name, referenced_table_name, "
        "referenced_column_name, constraint_name "
        "FROM information_schema.key_column_usage "
        f"WHERE table_schema IN {_in_list_sql(schemas, 'schema')} "
        f"AND table_name IN {_in_list_sql(tables, 'table')} "
        "AND referenced_table_name IS NOT NULL"
    )


def build_search_query(pattern: str, limit: int, schemas: Iterable[str] | None = None) -> str:
    if not pattern:
        raise WriteAttemptError("search pattern cannot be empty")
    if len(pattern) > _SEARCH_PATTERN_MAX_LEN:
        raise WriteAttemptError("search pattern too long")
    if not (1 <= int(limit) <= _LIMIT_MAX):
        raise WriteAttemptError(f"limit must be in [1, {_LIMIT_MAX}]")
    escaped = _escape_like(pattern)
    schemas_list = list(schemas or [])
    schema_clause = (
        f" AND table_schema IN {_in_list_sql(schemas_list, 'schema')}"
        if schemas_list
        else ""
    )
    return (
        "SELECT table_schema, table_name "
        "FROM information_schema.tables "
        f"WHERE table_name LIKE '%{escaped}%' ESCAPE '{_LIKE_ESCAPE_CHAR}'"
        f"{schema_clause} "
        f"LIMIT {int(limit)}"
    )
