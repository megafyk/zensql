from __future__ import annotations

import pytest

from zen.mcp_tools.errors import WriteAttemptError
from zen.schema_mcp import queries


def test_columns_query_basic() -> None:
    sql = queries.build_columns_query(["cdcn_log_central"], ["orders", "customers"])
    assert "information_schema.columns" in sql
    assert "'cdcn_log_central'" in sql
    assert "'orders'" in sql
    assert "'customers'" in sql
    assert "ORDER BY table_schema, table_name, ordinal_position" in sql


def test_indexes_query_basic() -> None:
    sql = queries.build_indexes_query(["s1"], ["t1"])
    assert "information_schema.statistics" in sql
    assert "ORDER BY table_schema, table_name, index_name, seq_in_index" in sql


def test_partitions_query_basic() -> None:
    sql = queries.build_partitions_query(["s1"], ["t1"])
    assert "information_schema.partitions" in sql
    assert "partition_name IS NOT NULL" in sql


def test_relationships_query_basic() -> None:
    sql = queries.build_relationships_query(["s1"], ["t1"])
    assert "information_schema.key_column_usage" in sql
    assert "referenced_table_name IS NOT NULL" in sql


def test_search_query_basic() -> None:
    sql = queries.build_search_query("order", 10)
    assert "information_schema.tables" in sql
    assert "LIKE '%order%'" in sql
    assert "LIMIT 10" in sql


def test_search_query_scopes_by_schema() -> None:
    sql = queries.build_search_query("order", 10, ["cdcn_log_central"])
    assert "AND table_schema IN ('cdcn_log_central')" in sql


def test_search_query_escapes_special_chars() -> None:
    sql = queries.build_search_query("a%b_c'd", 5)
    assert "%" in sql.split("LIKE '")[1].split("%'")[0]  # raw % brackets still present
    assert "\\%" in sql
    assert "\\_" in sql
    assert "''" in sql or "''" in sql  # single-quote escape


@pytest.mark.parametrize("bad", ["a;b", "a b", "a' OR 1=1", "-- comment", "orders/*", "a.b"])
def test_columns_query_rejects_bad_identifiers(bad: str) -> None:
    with pytest.raises(WriteAttemptError):
        queries.build_columns_query(["s"], [bad])


def test_empty_lists_match_nothing() -> None:
    # No interpolation should ever produce a wide-open WHERE clause.
    sql = queries.build_columns_query([], [])
    assert "table_schema IN (NULL)" in sql
    assert "table_name IN (NULL)" in sql


def test_search_query_rejects_oversize_pattern() -> None:
    with pytest.raises(WriteAttemptError):
        queries.build_search_query("x" * 65, 10)


def test_search_query_rejects_empty_pattern() -> None:
    with pytest.raises(WriteAttemptError):
        queries.build_search_query("", 10)


@pytest.mark.parametrize("limit", [0, -1, 201, 1000])
def test_search_query_rejects_bad_limit(limit: int) -> None:
    with pytest.raises(WriteAttemptError):
        queries.build_search_query("x", limit)
