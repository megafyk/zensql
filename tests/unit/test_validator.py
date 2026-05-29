from __future__ import annotations

import pytest

from zen.models.metadata import ColumnMetadata, TableMetadata
from zen.sql_agent_server.validator import SqlSafetyValidator


def _orders_metadata() -> list[TableMetadata]:
    return [
        TableMetadata(
            schema="cdcn_log_central",
            name="orders",
            columns=[
                ColumnMetadata(name="id", data_type="bigint", is_nullable=False, key="PRI"),
                ColumnMetadata(name="status", data_type="varchar", is_nullable=False),
                ColumnMetadata(name="customer_id", data_type="bigint", is_nullable=False),
                ColumnMetadata(name="created_at", data_type="datetime", is_nullable=False),
            ],
        ),
        TableMetadata(
            schema="cdcn_log_central",
            name="customers",
            columns=[
                ColumnMetadata(name="id", data_type="bigint", is_nullable=False, key="PRI"),
                ColumnMetadata(name="email", data_type="varchar", is_nullable=False),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_simple_select_passes() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("SELECT id FROM orders WHERE status = 'new' LIMIT 10")
    assert rep.ok, rep.summary
    assert rep.sql_with_banner is not None
    assert "AI-GENERATED SQL" in rep.sql_with_banner
    assert rep.tables_referenced == ["orders"]


def test_validated_sql_has_banner_with_request_id() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("SELECT 1 FROM orders LIMIT 1", request_id="abc-123")
    assert rep.ok
    assert "request_id: abc-123" in (rep.sql_with_banner or "")


def test_validated_sql_ends_with_semicolon() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("SELECT id FROM orders LIMIT 1")
    assert rep.ok
    assert (rep.sql_with_banner or "").rstrip("\n").endswith(";")


# ---------------------------------------------------------------------------
# Denied families
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id) VALUES (1)",
        "UPDATE orders SET status = 'shipped' WHERE id = 1",
        "DELETE FROM orders WHERE id = 1",
        "DROP TABLE orders",
        "TRUNCATE TABLE orders",
        "ALTER TABLE orders ADD COLUMN foo INT",
        "CREATE TABLE x (a INT)",
        "GRANT ALL ON orders TO u",
        "REVOKE ALL ON orders FROM u",
        "CALL my_proc()",
        "EXECUTE stmt1",
        "LOAD DATA INFILE '/etc/passwd' INTO TABLE x",
        "REPLACE INTO orders VALUES (1)",
        "RENAME TABLE a TO b",
        "LOCK TABLES orders WRITE",
        "SET @x = 1",
        "START TRANSACTION",
        "COMMIT",
        "ROLLBACK",
    ],
)
def test_denied_family_rejected(sql: str) -> None:
    v = SqlSafetyValidator()
    rep = v.validate(sql)
    assert rep.ok is False
    rules = {viol.rule for viol in rep.violations}
    assert "STATEMENT_FAMILY_DENIED" in rules or "UNPARSEABLE" in rules


def test_denied_keyword_caught_even_in_subquery() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("SELECT * FROM (DROP TABLE x) AS t")
    assert rep.ok is False


def test_select_into_var_rejected() -> None:
    """`SELECT ... INTO @var` parses as exp.Select but is not a clean read-only
    SELECT — refuse even though the family check would accept it."""
    v = SqlSafetyValidator()
    rep = v.validate("SELECT id INTO @x FROM orders LIMIT 1")
    assert rep.ok is False
    rules = {viol.rule for viol in rep.violations}
    assert "STATEMENT_FAMILY_DENIED" in rules


# ---------------------------------------------------------------------------
# Multi-statement & parsing
# ---------------------------------------------------------------------------


def test_multi_statement_rejected() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("SELECT 1 FROM orders; SELECT 2 FROM orders;")
    assert rep.ok is False
    rules = {viol.rule for viol in rep.violations}
    assert "MULTI_STATEMENT" in rules or "STATEMENT_FAMILY_DENIED" in rules


def test_empty_sql_rejected() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("   ")
    assert rep.ok is False


def test_unparseable_sql_rejected() -> None:
    v = SqlSafetyValidator()
    rep = v.validate("SELECT FROM WHERE")
    assert rep.ok is False


# ---------------------------------------------------------------------------
# Identifier checks
# ---------------------------------------------------------------------------


def test_strict_rejects_unknown_table() -> None:
    v = SqlSafetyValidator(strict_identifier_check=True)
    rep = v.validate("SELECT id FROM nope LIMIT 1", _orders_metadata())
    assert rep.ok is False
    assert any(viol.rule == "IDENTIFIER_NOT_VERIFIED" for viol in rep.violations)


def test_nonstrict_warns_on_unknown_table() -> None:
    v = SqlSafetyValidator(strict_identifier_check=False)
    rep = v.validate("SELECT id FROM nope LIMIT 1", _orders_metadata())
    assert rep.ok is True
    assert any("unknown tables" in w for w in rep.warnings)


def test_strict_accepts_known_table() -> None:
    v = SqlSafetyValidator(strict_identifier_check=True)
    rep = v.validate("SELECT id FROM orders LIMIT 1", _orders_metadata())
    assert rep.ok, rep.summary


def test_strict_rejects_unknown_column() -> None:
    v = SqlSafetyValidator(strict_identifier_check=True)
    rep = v.validate(
        "SELECT orders.no_such_col FROM orders LIMIT 1", _orders_metadata()
    )
    assert rep.ok is False
    assert any(viol.rule == "IDENTIFIER_NOT_VERIFIED" for viol in rep.violations)


# ---------------------------------------------------------------------------
# Risk warnings
# ---------------------------------------------------------------------------


def test_select_star_warns() -> None:
    v = SqlSafetyValidator(strict_identifier_check=False)
    rep = v.validate("SELECT * FROM orders LIMIT 1")
    assert rep.ok
    assert any("SELECT *" in w for w in rep.warnings)


def test_missing_limit_warns() -> None:
    v = SqlSafetyValidator(strict_identifier_check=False)
    rep = v.validate("SELECT id FROM orders")
    assert rep.ok
    assert any("LIMIT" in w for w in rep.warnings)


def test_suspicious_function_warns() -> None:
    v = SqlSafetyValidator(strict_identifier_check=False)
    rep = v.validate("SELECT SLEEP(5) FROM orders LIMIT 1")
    assert rep.ok
    assert any("SLEEP" in w for w in rep.warnings)


def test_load_file_warns() -> None:
    v = SqlSafetyValidator(strict_identifier_check=False)
    rep = v.validate("SELECT LOAD_FILE('/etc/passwd') FROM orders LIMIT 1")
    assert rep.ok
    assert any("LOAD_FILE" in w for w in rep.warnings)


# ---------------------------------------------------------------------------
# tables_referenced
# ---------------------------------------------------------------------------


def test_tables_referenced_collects_unique() -> None:
    v = SqlSafetyValidator(strict_identifier_check=False)
    rep = v.validate(
        "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id LIMIT 1"
    )
    assert rep.ok
    assert set(rep.tables_referenced) == {"orders", "customers"}
