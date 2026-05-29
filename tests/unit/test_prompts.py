from __future__ import annotations

from uuid import uuid4

from zen.models.requests import ContextHints, UserSqlRequest
from zen.registry.models import (
    ConnectionBlock,
    MetabaseSource,
    MetabaseSourceMetadata,
    RepoEntry,
)
from zen.sql_agent_server.prompts import (
    build_system_prompt,
    build_user_prompt,
    format_registered_sources,
)


def _repo() -> RepoEntry:
    return RepoEntry(
        name="acme",
        description="d",
        path="/x",
        tags=["t"],
        connection=[
            ConnectionBlock(
                environment="production",
                sources=[
                    MetabaseSource(
                        name="metabase",
                        metadata=MetabaseSourceMetadata(
                            database="prod_db",
                            database_id=642,
                            database_type="mariadb",
                            schema_="acme_schema",
                            tables=["orders"],
                        ),
                    )
                ],
            )
        ],
    )


def test_system_prompt_contains_safety_rules() -> None:
    sp = build_system_prompt()
    assert "TEXT ONLY" in sp
    assert "mcp__schema__" in sp
    assert "Refuse INSERT/UPDATE/DELETE/DDL" in sp
    assert "```sql" in sp


def test_system_prompt_allows_chat_for_non_sql() -> None:
    sp = build_system_prompt()
    assert "```chat" in sp
    assert "joke" in sp.lower()


def test_user_prompt_embeds_request_with_sentinels() -> None:
    req = UserSqlRequest(
        request_id=uuid4(),
        source="telegram",
        user_id="tg:1",
        text="new received orders this week",
        context_hints=ContextHints(
            preferred_repos=["orders-service"],
            preferred_schemas=["cdcn_log_central"],
        ),
    )
    up = build_user_prompt(req)
    assert "<user_request>" in up
    assert "new received orders this week" in up
    assert "</user_request>" in up
    assert "orders-service" in up
    assert "cdcn_log_central" in up


def test_user_prompt_handles_empty_hints() -> None:
    req = UserSqlRequest(
        request_id=uuid4(),
        source="telegram",
        user_id="tg:1",
        text="anything",
    )
    up = build_user_prompt(req)
    assert "preferred_repos: []" in up
    assert "preferred_schemas: []" in up


def test_format_registered_sources_lists_database_ids() -> None:
    out = format_registered_sources([_repo()])
    assert "database_id=642" in out
    assert 'schema="acme_schema"' in out
    assert "never guess a database_id" in out


def test_format_registered_sources_empty_when_no_repos() -> None:
    assert format_registered_sources([]) == ""


def test_user_prompt_includes_registered_sources() -> None:
    req = UserSqlRequest(
        request_id=uuid4(), source="telegram", user_id="tg:1", text="count orders"
    )
    sources = format_registered_sources([_repo()])
    up = build_user_prompt(req, sources)
    assert "database_id=642" in up
    assert 'schema="acme_schema"' in up
