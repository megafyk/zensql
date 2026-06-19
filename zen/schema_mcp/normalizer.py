"""Convert raw `information_schema.*` rows into typed metadata models."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zen.models.metadata import (
    ColumnKey,
    ColumnMetadata,
    IndexMetadata,
    PartitionMetadata,
    Relationship,
)

TableKey = tuple[str, str]


def _yes(v: Any) -> bool:
    if isinstance(v, str):
        return v.upper() == "YES"
    return bool(v)


def _opt_str(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _coerce_key(v: Any) -> ColumnKey:
    if v in (None, ""):
        return None
    sv = str(v).upper()
    if sv in {"PRI", "UNI", "MUL"}:
        return sv  # type: ignore[return-value]
    return ""


def normalize_columns(rows: Sequence[Sequence[Any]]) -> dict[TableKey, list[ColumnMetadata]]:
    # Row shape: (schema, table, name, data_type, is_nullable, default, key, extra, comment, ord).
    out: dict[TableKey, list[ColumnMetadata]] = {}
    for r in rows:
        schema, table, name, data_type, is_nullable, default, key, extra, comment, _ord = r
        col = ColumnMetadata(
            name=name,
            data_type=str(data_type),
            is_nullable=_yes(is_nullable),
            default=_opt_str(default),
            key=_coerce_key(key),
            extra=_opt_str(extra),
            comment=_opt_str(comment),
        )
        out.setdefault((str(schema), str(table)), []).append(col)
    return out


def normalize_indexes(rows: Sequence[Sequence[Any]]) -> dict[TableKey, list[IndexMetadata]]:
    """Row shape: (schema, table, index_name, column_name, non_unique, index_type, seq_in_index).

    Aggregates multi-column indexes by `(schema, table, index_name)`.
    """
    bucket: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: dict[TableKey, list[str]] = {}

    for r in rows:
        schema, table, index_name, column_name, non_unique, index_type, _seq = r
        key = (str(schema), str(table), str(index_name))
        if key not in bucket:
            bucket[key] = {
                "columns": [],
                "unique": not bool(non_unique),
                "type": _opt_str(index_type),
            }
            order.setdefault((str(schema), str(table)), []).append(str(index_name))
        bucket[key]["columns"].append(str(column_name))

    out: dict[TableKey, list[IndexMetadata]] = {}
    for (schema, table, index_name), agg in bucket.items():
        out.setdefault((schema, table), []).append(
            IndexMetadata(
                name=index_name,
                columns=agg["columns"],
                unique=bool(agg["unique"]),
                type=agg["type"],
            )
        )
    # preserve discovery order
    for table_key, names in order.items():
        pos = {n: i for i, n in enumerate(names)}
        out[table_key].sort(key=lambda idx: pos[idx.name])
    return out


def normalize_partitions(rows: Sequence[Sequence[Any]]) -> dict[TableKey, list[PartitionMetadata]]:
    """Row shape: (schema, table, partition_name, method, expression, description, table_rows)."""
    out: dict[TableKey, list[PartitionMetadata]] = {}
    for r in rows:
        schema, table, partition_name, method, expression, description, table_rows = r
        out.setdefault((str(schema), str(table)), []).append(
            PartitionMetadata(
                name=str(partition_name),
                method=str(method),
                expression=_opt_str(expression),
                description=_opt_str(description),
                table_rows=int(table_rows) if table_rows is not None else None,
            )
        )
    return out


def normalize_relationships(
    rows: Sequence[Sequence[Any]],
) -> dict[TableKey, list[Relationship]]:
    """Row shape: (schema, from_table, from_column, to_table, to_column, constraint)."""
    out: dict[TableKey, list[Relationship]] = {}
    for r in rows:
        schema, from_table, from_column, to_table, to_column, constraint = r
        out.setdefault((str(schema), str(from_table)), []).append(
            Relationship(
                from_table=str(from_table),
                from_column=str(from_column),
                to_table=str(to_table),
                to_column=str(to_column),
                constraint=_opt_str(constraint),
            )
        )
    return out


def _extract_rows(payload: dict[str, Any]) -> list[list[Any]]:
    """Pluck the rows array out of a Metabase /api/dataset response."""
    data = payload.get("data") or {}
    rows = data.get("rows") or []
    return [list(r) for r in rows]
