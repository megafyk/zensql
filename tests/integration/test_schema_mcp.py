from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from zen.config.settings import Settings
from zen.mcp_tools.errors import DatabaseNotAllowedError
from zen.registry.models import (
    ConnectionBlock,
    MetabaseSource,
    MetabaseSourceMetadata,
    RepoEntry,
)
from zen.registry.store import RegistryStore
from zen.schema_mcp import tools


class FakeMetabaseClient:
    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[int, str]] = []

    def queue(self, key: str, payload: dict[str, Any]) -> None:
        self._responses[key] = payload

    async def run_native_metadata_query(
        self,
        database_id: int,
        sql: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((database_id, sql))
        for key, payload in self._responses.items():
            if key in sql:
                return payload
        return {"data": {"cols": [], "rows": []}}


def _registry_with_orders(tmp_path: Path) -> RegistryStore:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(
        RepoEntry(
            name="orders-service",
            description="Orders service",
            path="/srv/repos/orders-service",
            tags=["orders"],
            connection=[
                ConnectionBlock(
                    environment="production",
                    sources=[
                        MetabaseSource(
                            name="metabase",
                            metadata=MetabaseSourceMetadata(
                                database="prod",
                                database_id=312,
                                database_type="mariadb",
                                schema="cdcn_log_central",
                                tables=["orders", "customers"],
                            ),
                        )
                    ],
                )
            ],
        )
    )
    return store


def _settings() -> Settings:
    return Settings(_env_file=None, agent_api_token=SecretStr("t"))


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


async def test_get_table_metadata_rejects_disallowed_db(tmp_path: Path) -> None:
    registry = _registry_with_orders(tmp_path)
    with pytest.raises(DatabaseNotAllowedError):
        await tools.get_table_metadata(
            database_id=999,
            table_names=["orders"],
            settings=_settings(),
            client=FakeMetabaseClient(),
            registry=registry,
        )


async def test_empty_registry_rejects_all(tmp_path: Path) -> None:
    registry = RegistryStore(tmp_path / "registry.json")
    with pytest.raises(DatabaseNotAllowedError) as exc:
        await tools.get_table_metadata(
            database_id=312,
            table_names=["orders"],
            settings=_settings(),
            client=FakeMetabaseClient(),
            registry=registry,
        )
    assert "register one" in str(exc.value)


async def test_corrupt_registry_surfaces_typed_error(tmp_path: Path) -> None:
    """Malformed JSON in registry.json must surface as DatabaseNotAllowedError,
    not an untyped Python traceback through the MCP boundary."""
    bad = tmp_path / "registry.json"
    bad.write_text("{ not valid json }")
    registry = RegistryStore(bad)
    with pytest.raises(DatabaseNotAllowedError) as exc:
        await tools.get_table_metadata(
            database_id=312,
            table_names=["orders"],
            settings=_settings(),
            client=FakeMetabaseClient(),
            registry=registry,
        )
    assert "registry unavailable" in str(exc.value)


# ---------------------------------------------------------------------------
# get_table_metadata happy paths
# ---------------------------------------------------------------------------


async def test_get_table_metadata_columns_only(tmp_path: Path) -> None:
    registry = _registry_with_orders(tmp_path)
    client = FakeMetabaseClient()
    client.queue(
        "information_schema.columns",
        {
            "data": {
                "cols": [],
                "rows": [
                    ["cdcn_log_central", "orders", "id", "bigint", "NO", None, "PRI", "auto_increment", None, 1],
                    ["cdcn_log_central", "orders", "status", "varchar(32)", "NO", "'new'", "", None, None, 2],
                ],
            }
        },
    )

    out = await tools.get_table_metadata(
        database_id=312,
        table_names=["orders"],
        include_columns=True,
        settings=_settings(),
        client=client,
        registry=registry,
    )

    assert len(out["tables"]) == 1
    t = out["tables"][0]
    assert t["schema"] == "cdcn_log_central"
    assert t["name"] == "orders"
    names = [c["name"] for c in t["columns"]]
    assert names == ["id", "status"]
    assert t["indexes"] == []
    assert t["partitions"] == []
    assert t["relationships"] == []
    assert out["source"]["database_id"] == 312
    assert "Read-only" in out["warning"]


async def test_get_table_metadata_all_includes(tmp_path: Path) -> None:
    registry = _registry_with_orders(tmp_path)
    client = FakeMetabaseClient()
    client.queue(
        "information_schema.columns",
        {"data": {"rows": [["cdcn_log_central", "orders", "id", "bigint", "NO", None, "PRI", "", None, 1]]}},
    )
    client.queue(
        "information_schema.statistics",
        {"data": {"rows": [["cdcn_log_central", "orders", "PRIMARY", "id", 0, "BTREE", 1]]}},
    )
    client.queue(
        "information_schema.partitions",
        {"data": {"rows": []}},
    )
    client.queue(
        "information_schema.key_column_usage",
        {
            "data": {
                "rows": [
                    ["cdcn_log_central", "orders", "customer_id", "customers", "id", "fk_orders_customer"],
                ]
            }
        },
    )

    out = await tools.get_table_metadata(
        database_id=312,
        table_names=["orders"],
        include_columns=True,
        include_indexes=True,
        include_partitions=True,
        include_relationships=True,
        settings=_settings(),
        client=client,
        registry=registry,
    )

    t = out["tables"][0]
    assert t["indexes"][0]["name"] == "PRIMARY"
    assert t["relationships"][0]["to_table"] == "customers"
    assert t["relationships"][0]["constraint"] == "fk_orders_customer"


# ---------------------------------------------------------------------------
# search_tables
# ---------------------------------------------------------------------------


async def test_search_tables_returns_matches(tmp_path: Path) -> None:
    registry = _registry_with_orders(tmp_path)
    client = FakeMetabaseClient()
    client.queue(
        "information_schema.tables",
        {
            "data": {
                "rows": [
                    ["cdcn_log_central", "orders"],
                    ["cdcn_log_central", "order_items"],
                ]
            }
        },
    )

    out = await tools.search_tables(
        database_id=312,
        query="order",
        settings=_settings(),
        client=client,
        registry=registry,
    )
    names = {m["name"] for m in out["matches"]}
    assert names == {"orders", "order_items"}


# ---------------------------------------------------------------------------
# get_relationships
# ---------------------------------------------------------------------------


async def test_get_relationships_returns_fks(tmp_path: Path) -> None:
    registry = _registry_with_orders(tmp_path)
    client = FakeMetabaseClient()
    client.queue(
        "information_schema.key_column_usage",
        {
            "data": {
                "rows": [
                    ["cdcn_log_central", "orders", "customer_id", "customers", "id", "fk1"],
                ]
            }
        },
    )

    out = await tools.get_relationships(
        database_id=312,
        table_names=["orders"],
        settings=_settings(),
        client=client,
        registry=registry,
    )
    assert len(out["relationships"]) == 1
    assert out["relationships"][0]["to_table"] == "customers"
