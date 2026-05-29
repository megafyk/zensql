from __future__ import annotations

from zen.schema_mcp import normalizer


def test_normalize_columns_groups_by_table() -> None:
    rows = [
        ["cdcn_log_central", "orders", "id", "bigint", "NO", None, "PRI", "auto_increment", None, 1],
        ["cdcn_log_central", "orders", "status", "varchar(32)", "NO", "'new'", "", None, None, 2],
        ["cdcn_log_central", "customers", "id", "bigint", "NO", None, "PRI", "", None, 1],
    ]
    out = normalizer.normalize_columns(rows)
    assert set(out.keys()) == {("cdcn_log_central", "orders"), ("cdcn_log_central", "customers")}
    orders_cols = {c.name: c for c in out[("cdcn_log_central", "orders")]}
    assert orders_cols["id"].is_nullable is False
    assert orders_cols["id"].key == "PRI"
    assert orders_cols["id"].extra == "auto_increment"
    assert orders_cols["status"].default == "'new'"
    assert orders_cols["status"].key in ("", None)


def test_normalize_columns_handles_nullable_yes() -> None:
    rows = [["s", "t", "comment", "text", "YES", None, "", None, None, 1]]
    cols = normalizer.normalize_columns(rows)[("s", "t")]
    assert cols[0].is_nullable is True


def test_normalize_indexes_aggregates_multi_column() -> None:
    rows = [
        ["s", "t", "PRIMARY", "id", 0, "BTREE", 1],
        ["s", "t", "ix_compound", "a", 1, "BTREE", 1],
        ["s", "t", "ix_compound", "b", 1, "BTREE", 2],
    ]
    out = normalizer.normalize_indexes(rows)
    by_name = {i.name: i for i in out[("s", "t")]}
    assert by_name["PRIMARY"].unique is True
    assert by_name["PRIMARY"].columns == ["id"]
    assert by_name["ix_compound"].unique is False
    assert by_name["ix_compound"].columns == ["a", "b"]


def test_normalize_partitions() -> None:
    rows = [
        ["s", "t", "p_2025_01", "RANGE", "TO_DAYS(created_at)", "TO_DAYS('2025-02-01')", 12345],
        ["s", "t", "p_2025_02", "RANGE", "TO_DAYS(created_at)", "TO_DAYS('2025-03-01')", None],
    ]
    out = normalizer.normalize_partitions(rows)
    parts = out[("s", "t")]
    assert [p.name for p in parts] == ["p_2025_01", "p_2025_02"]
    assert parts[0].method == "RANGE"
    assert parts[0].table_rows == 12345
    assert parts[1].table_rows is None


def test_normalize_relationships() -> None:
    rows = [
        ["s", "orders", "customer_id", "customers", "id", "fk_orders_customer"],
        ["s", "orders", "product_id", "products", "id", None],
    ]
    out = normalizer.normalize_relationships(rows)
    rels = out[("s", "orders")]
    assert len(rels) == 2
    assert rels[0].to_table == "customers"
    assert rels[0].constraint == "fk_orders_customer"
    assert rels[1].constraint is None


def test_extract_rows_pulls_from_dataset_envelope() -> None:
    payload = {"data": {"cols": [{"name": "x"}], "rows": [[1], [2]]}}
    assert normalizer._extract_rows(payload) == [[1], [2]]


def test_extract_rows_tolerates_missing_data() -> None:
    assert normalizer._extract_rows({}) == []
    assert normalizer._extract_rows({"data": {}}) == []
