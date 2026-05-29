from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from zen.registry.models import (
    ConnectionBlock,
    MetabaseSource,
    MetabaseSourceMetadata,
    RegistryDocument,
    RepoEntry,
)
from zen.registry.store import (
    DuplicateRepoError,
    RegistryStore,
    RepoNotFoundError,
)


def _orders_entry() -> RepoEntry:
    return RepoEntry(
        name="orders-service",
        description="Orders management service",
        path="/srv/repos/orders-service",
        tags=["orders", "payment"],
        connection=[
            ConnectionBlock(
                environment="production",
                sources=[
                    MetabaseSource(
                        name="metabase",
                        metadata=MetabaseSourceMetadata(
                            database="prod_orders",
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


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


def test_repo_entry_round_trip_preserves_schema_alias() -> None:
    entry = _orders_entry()
    payload = entry.model_dump(by_alias=True, mode="json")
    md = payload["connection"][0]["sources"][0]["metadata"]
    assert "schema" in md
    assert md["schema"] == "cdcn_log_central"
    assert "schema_" not in md
    parsed = RepoEntry.model_validate(payload)
    assert parsed == entry


def test_repo_entry_rejects_invalid_name_pattern() -> None:
    with pytest.raises(ValidationError):
        RepoEntry(
            name="Orders Service",  # uppercase + space
            description="x",
            path="/x",
            tags=["a"],
            connection=[ConnectionBlock(environment="dev", sources=[])],
        )


def test_repo_entry_rejects_relative_path() -> None:
    with pytest.raises(ValidationError):
        RepoEntry(
            name="x",
            description="x",
            path="srv/x",
            tags=["a"],
            connection=[ConnectionBlock(environment="dev", sources=[])],
        )


def test_repo_entry_requires_at_least_one_tag_and_connection() -> None:
    with pytest.raises(ValidationError):
        RepoEntry(name="x", description="x", path="/x", tags=[], connection=[])


def test_metabase_metadata_rejects_unknown_engine() -> None:
    with pytest.raises(ValueError):
        MetabaseSourceMetadata(database="db", database_id=1, database_type="cassandra")


def test_metabase_metadata_rejects_invalid_database_id() -> None:
    with pytest.raises(ValidationError):
        MetabaseSourceMetadata(database="db", database_id=0)


def test_source_accepts_metabase_name() -> None:
    cb = ConnectionBlock.model_validate(
        {
            "environment": "production",
            "sources": [
                {"name": "metabase", "metadata": {"database": "d", "database_id": 1}},
            ],
        }
    )
    assert isinstance(cb.sources[0], MetabaseSource)


@pytest.mark.parametrize("bad_name", ["quickwit", "prometheus", "elastic", "kafka"])
def test_source_rejects_non_metabase_name(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        ConnectionBlock.model_validate(
            {
                "environment": "production",
                "sources": [{"name": bad_name, "metadata": {}}],
            }
        )


def test_metabase_sources_filters_by_environment() -> None:
    entry = RepoEntry(
        name="multi-env",
        description="x",
        path="/x",
        tags=["a"],
        connection=[
            ConnectionBlock(
                environment="production",
                sources=[
                    MetabaseSource(
                        name="metabase",
                        metadata=MetabaseSourceMetadata(database="p", database_id=1),
                    )
                ],
            ),
            ConnectionBlock(
                environment="staging",
                sources=[
                    MetabaseSource(
                        name="metabase",
                        metadata=MetabaseSourceMetadata(database="s", database_id=2),
                    )
                ],
            ),
        ],
    )
    assert [s.metadata.database_id for s in entry.metabase_sources()] == [1, 2]
    prod_only = entry.metabase_sources("production")
    assert [s.metadata.database_id for s in prod_only] == [1]


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------


def test_load_empty_when_file_absent(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    doc = store.load()
    assert doc == RegistryDocument()
    assert doc.repos == []


def test_register_persists_atomically(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "registry.json"
    store = RegistryStore(path)
    entry = _orders_entry()

    store.register(entry)

    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["version"] == 1
    assert on_disk["repos"][0]["name"] == "orders-service"
    assert (
        on_disk["repos"][0]["connection"][0]["sources"][0]["metadata"]["schema"]
        == "cdcn_log_central"
    )
    # No tempfile leftovers in the parent dir
    leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".registry-")]
    assert leftovers == []


def test_register_rejects_duplicate_name(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    with pytest.raises(DuplicateRepoError):
        store.register(_orders_entry())


def test_list_repos_round_trips_multiple_entries(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    second = _orders_entry().model_copy(update={"name": "auth-service", "path": "/srv/auth"})
    store.register(second)
    names = [r.name for r in store.list_repos()]
    assert names == ["orders-service", "auth-service"]


def test_get_returns_entry_and_raises_when_missing(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    assert store.get("orders-service").name == "orders-service"
    with pytest.raises(RepoNotFoundError):
        store.get("does-not-exist")


def test_update_merges_fields(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    updated = store.update("orders-service", {"description": "Updated description"})
    assert updated.description == "Updated description"
    assert updated.tags == ["orders", "payment"]
    # persisted
    assert store.get("orders-service").description == "Updated description"


def test_update_can_rename_when_target_free(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    renamed = store.update("orders-service", {"name": "orders-svc-v2"})
    assert renamed.name == "orders-svc-v2"
    with pytest.raises(RepoNotFoundError):
        store.get("orders-service")


def test_update_rejects_rename_collision(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    store.register(_orders_entry().model_copy(update={"name": "auth-service", "path": "/srv/auth"}))
    with pytest.raises(DuplicateRepoError):
        store.update("orders-service", {"name": "auth-service"})


def test_update_missing_raises(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    with pytest.raises(RepoNotFoundError):
        store.update("ghost", {"description": "x"})


def test_delete_removes_entry(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    store.register(_orders_entry())
    deleted = store.delete("orders-service")
    assert deleted.name == "orders-service"
    assert store.list_repos() == []


def test_delete_missing_raises(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path / "registry.json")
    with pytest.raises(RepoNotFoundError):
        store.delete("ghost")
