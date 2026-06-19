"""Schema MCP tool implementations.

Each tool:
- Validates that `database_id` belongs to a registered repo's metabase source.
- Builds an `information_schema.*` SQL template (queries.py).
- Submits it through `MetabaseClient.run_native_metadata_query` — which re-runs
  the info_schema chokepoint before any HTTP call.
- Normalizes the rows into typed pydantic models.
- Returns a stable JSON envelope.

Production wiring (real client + registry) lives in `server.py`. The tools are
injection-friendly so tests can supply fakes.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from zen.config.settings import Settings, get_settings
from zen.mcp_tools.errors import DatabaseNotAllowedError
from zen.models.metadata import (
    ColumnMetadata,
    IndexMetadata,
    PartitionMetadata,
    Relationship,
    TableMetadata,
)
from zen.registry.store import RegistryError, RegistryStore
from zen.schema_mcp import normalizer, queries
from zen.schema_mcp.normalizer import TableKey

if TYPE_CHECKING:
    from zen.registry.models import RepoEntry

_WARNING = "Read-only metadata access; SQL is not executed by this system."


class _MetabaseClientLike(Protocol):
    async def run_native_metadata_query(
        self,
        database_id: int,
        sql: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _assert_database_allowed(
    database_id: int, registry: RegistryStore
) -> list[RepoEntry]:
    try:
        repos = registry.list_repos()
    except RegistryError as e:
        raise DatabaseNotAllowedError(f"registry unavailable: {e}") from e
    allowed: set[int] = set()
    for repo in repos:
        for src in repo.metabase_sources():
            allowed.add(src.metadata.database_id)
    if not allowed:
        raise DatabaseNotAllowedError(
            "no registered repo declares any metabase database — register one with sql_add_repo"
        )
    if database_id not in allowed:
        raise DatabaseNotAllowedError(
            f"database_id {database_id} not in any registered repo's metabase sources"
        )
    return repos


def _schemas_for_database(database_id: int, repos: list[RepoEntry]) -> list[str]:
    """Distinct schemas the registry declares for `database_id`, in order.
    Used when the caller omits schema_names — interpolating an empty list
    would produce `IN (NULL)`, which matches nothing and silently reports
    every table as missing."""
    out: list[str] = []
    for repo in repos:
        for src in repo.metabase_sources():
            meta = src.metadata
            if meta.database_id == database_id and meta.schema_ and meta.schema_ not in out:
                out.append(meta.schema_)
    return out


def _resolve_schemas(
    schema_names: list[str] | None, database_id: int, repos: list[RepoEntry]
) -> list[str]:
    schemas = schema_names or _schemas_for_database(database_id, repos)
    if not schemas:
        raise ValueError(
            "schema_names is required — no registered metabase source declares "
            f"a schema for database_id {database_id}"
        )
    return schemas


def _default_registry(settings: Settings) -> RegistryStore:
    return RegistryStore(settings.registry_path)


async def get_table_metadata(
    database_id: int,
    schema_names: list[str] | None = None,
    table_names: list[str] | None = None,
    include_columns: bool = True,
    include_indexes: bool = False,
    include_partitions: bool = False,
    include_relationships: bool = False,
    reason: str = "",
    *,
    settings: Settings | None = None,
    client: _MetabaseClientLike | None = None,
    registry: RegistryStore | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    reg = registry or _default_registry(s)
    repos = _assert_database_allowed(database_id, reg)

    if not table_names:
        raise ValueError("table_names is required")
    schemas = _resolve_schemas(schema_names, database_id, repos)

    if client is None:
        raise ValueError("client is required")

    # The enabled queries hit independent information_schema tables — run them
    # concurrently instead of paying up to four sequential roundtrips.
    wanted: list[tuple[str, str]] = []
    if include_columns:
        wanted.append(("columns", queries.build_columns_query(schemas, table_names)))
    if include_indexes:
        wanted.append(("indexes", queries.build_indexes_query(schemas, table_names)))
    if include_partitions:
        wanted.append(("partitions", queries.build_partitions_query(schemas, table_names)))
    if include_relationships:
        wanted.append(
            ("relationships", queries.build_relationships_query(schemas, table_names))
        )

    payloads: dict[str, dict[str, Any]] = {}
    try:
        async with asyncio.TaskGroup() as tg:
            pending = {
                key: tg.create_task(client.run_native_metadata_query(database_id, sql))
                for key, sql in wanted
            }
    except ExceptionGroup as eg:
        # Re-raise the first underlying error so the MCP boundary still sees
        # the typed McpToolError instead of an opaque ExceptionGroup.
        raise eg.exceptions[0] from eg
    payloads = {key: task.result() for key, task in pending.items()}

    columns_by_table: dict[TableKey, list[ColumnMetadata]] = {}
    indexes_by_table: dict[TableKey, list[IndexMetadata]] = {}
    partitions_by_table: dict[TableKey, list[PartitionMetadata]] = {}
    rels_by_table: dict[TableKey, list[Relationship]] = {}

    if "columns" in payloads:
        columns_by_table = normalizer.normalize_columns(
            normalizer._extract_rows(payloads["columns"])
        )
    if "indexes" in payloads:
        indexes_by_table = normalizer.normalize_indexes(
            normalizer._extract_rows(payloads["indexes"])
        )
    if "partitions" in payloads:
        partitions_by_table = normalizer.normalize_partitions(
            normalizer._extract_rows(payloads["partitions"])
        )
    if "relationships" in payloads:
        rels_by_table = normalizer.normalize_relationships(
            normalizer._extract_rows(payloads["relationships"])
        )

    seen_keys: set[tuple[str, str]] = set()
    for d in (columns_by_table, indexes_by_table, partitions_by_table, rels_by_table):
        seen_keys.update(d.keys())

    now = _now()
    tables: list[dict[str, Any]] = []
    for schema, name in sorted(seen_keys):
        if name not in table_names:
            continue
        if schemas and schema not in schemas:
            continue
        tm = TableMetadata(
            schema_=schema,
            name=name,
            comment=None,
            columns=columns_by_table.get((schema, name), []),
            indexes=indexes_by_table.get((schema, name), []),
            partitions=partitions_by_table.get((schema, name), []),
            relationships=rels_by_table.get((schema, name), []),
            retrieved_at=now,
        )
        tables.append(tm.model_dump(by_alias=True, mode="json"))

    return {
        "tables": tables,
        "source": {"system": "metabase", "database_id": database_id},
        "retrieved_at": now.isoformat(),
        "limitations": [],
        "warning": _WARNING,
    }


async def search_tables(
    database_id: int,
    query: str,
    schema_names: list[str] | None = None,
    max_results: int = 20,
    reason: str = "",
    *,
    settings: Settings | None = None,
    client: _MetabaseClientLike | None = None,
    registry: RegistryStore | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    reg = registry or _default_registry(s)
    _assert_database_allowed(database_id, reg)

    if client is None:
        raise ValueError("client is required")

    sql = queries.build_search_query(query, max_results, schema_names)
    payload = await client.run_native_metadata_query(database_id, sql)
    rows = normalizer._extract_rows(payload)

    matches: list[dict[str, Any]] = []
    for r in rows:
        schema, name = str(r[0]), str(r[1])
        matches.append({"schema": schema, "name": name, "row_estimate": None})

    return {"matches": matches, "warning": _WARNING}


async def get_relationships(
    database_id: int,
    schema_names: list[str] | None = None,
    table_names: list[str] | None = None,
    reason: str = "",
    *,
    settings: Settings | None = None,
    client: _MetabaseClientLike | None = None,
    registry: RegistryStore | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    reg = registry or _default_registry(s)
    repos = _assert_database_allowed(database_id, reg)

    if not table_names:
        raise ValueError("table_names is required")
    if client is None:
        raise ValueError("client is required")

    schemas = _resolve_schemas(schema_names, database_id, repos)
    sql = queries.build_relationships_query(schemas, table_names)
    payload = await client.run_native_metadata_query(database_id, sql)
    rels = normalizer.normalize_relationships(normalizer._extract_rows(payload))

    out: list[dict[str, Any]] = []
    for (_schema, _table), items in rels.items():
        for r in items:
            out.append(r.model_dump(mode="json"))

    return {"relationships": out, "warning": _WARNING}
