"""Schema MCP server — registers metadata tools and runs stdio transport.

Launched by Claude Code as a child process per `.mcp.json`. Tool names exposed
to the agent are namespaced by the server name ("schema"), e.g.
`mcp__schema__get_table_metadata`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from zen.config.settings import get_settings
from zen.registry.store import RegistryStore
from zen.schema_mcp import tools
from zen.schema_mcp.metabase_client import MetabaseClient

mcp = FastMCP("schema")

_client: MetabaseClient | None = None
_client_lock = asyncio.Lock()
_registry: RegistryStore | None = None


async def _get_client() -> MetabaseClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = MetabaseClient(get_settings())
    return _client


def _get_registry() -> RegistryStore:
    global _registry
    if _registry is None:
        _registry = RegistryStore(get_settings().registry_path)
    return _registry


@mcp.tool()
async def get_table_metadata(
    database_id: int,
    schema_names: list[str] | None = None,
    table_names: list[str] | None = None,
    include_columns: bool = True,
    include_indexes: bool = False,
    include_partitions: bool = False,
    include_relationships: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """Retrieve normalized MariaDB table metadata for SQL generation.

    Returns columns, indexes, partitions, and FK relationships as requested.
    Reads from Metabase via `information_schema` only — never executes
    user-generated SQL.
    """
    client = await _get_client()
    return await tools.get_table_metadata(
        database_id=database_id,
        schema_names=schema_names,
        table_names=table_names,
        include_columns=include_columns,
        include_indexes=include_indexes,
        include_partitions=include_partitions,
        include_relationships=include_relationships,
        reason=reason,
        client=client,
        registry=_get_registry(),
    )


@mcp.tool()
async def search_tables(
    database_id: int,
    query: str,
    schema_names: list[str] | None = None,
    max_results: int = 20,
    reason: str = "",
) -> dict[str, Any]:
    """Discover candidate MariaDB tables by name substring."""
    client = await _get_client()
    return await tools.search_tables(
        database_id=database_id,
        query=query,
        schema_names=schema_names,
        max_results=max_results,
        reason=reason,
        client=client,
        registry=_get_registry(),
    )


@mcp.tool()
async def get_relationships(
    database_id: int,
    schema_names: list[str] | None = None,
    table_names: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Return foreign-key relationships among the requested tables."""
    client = await _get_client()
    return await tools.get_relationships(
        database_id=database_id,
        schema_names=schema_names,
        table_names=table_names,
        reason=reason,
        client=client,
        registry=_get_registry(),
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
