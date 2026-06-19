from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from pydantic import SecretStr

from zen.config.settings import Settings
from zen.mcp_tools.errors import (
    MetabaseAuthFailedError,
    MetabaseQueryFailedError,
    UpstreamTimeoutError,
    WriteAttemptError,
)
from zen.schema_mcp.metabase_client import (
    MetabaseClient,
    _assert_information_schema_only,
)


def _settings(url: str = "http://metabase.test") -> Settings:
    return Settings(
        _env_file=None,
        agent_api_token=SecretStr("t"),
        metabase_base_url=url,
        metabase_username=SecretStr("user"),
        metabase_password=SecretStr("pass"),
        metabase_query_timeout_s=2,
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _zero_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _zero_sleep)


# ---------------------------------------------------------------------------
# _assert_information_schema_only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.columns WHERE table_name = 'orders'",
        (
            "SELECT c.column_name FROM information_schema.columns c "
            "JOIN information_schema.statistics s ON c.table_name = s.table_name"
        ),
        "SELECT t.table_name FROM information_schema.tables t",
        "SELECT partition_name FROM information_schema.partitions WHERE table_schema = 'x'",
    ],
)
def test_chokepoint_allows_info_schema(sql: str) -> None:
    _assert_information_schema_only(sql)


def test_chokepoint_rejects_empty() -> None:
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only("   ")


def test_chokepoint_rejects_unparseable() -> None:
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only("SELECT FROM WHERE")


def test_chokepoint_rejects_multi_statement() -> None:
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only(
            "SELECT * FROM information_schema.columns; SELECT * FROM information_schema.tables"
        )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO information_schema.columns VALUES (1)",
        "UPDATE information_schema.columns SET column_name = 'x'",
        "DELETE FROM information_schema.columns",
        "DROP TABLE information_schema.columns",
        "TRUNCATE information_schema.columns",
        "ALTER TABLE information_schema.columns ADD COLUMN x INT",
        "CREATE TABLE x (a INT)",
    ],
)
def test_chokepoint_rejects_non_select(sql: str) -> None:
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only(sql)


def test_chokepoint_rejects_non_info_schema_table() -> None:
    with pytest.raises(WriteAttemptError) as exc:
        _assert_information_schema_only("SELECT * FROM cdcn_log_central.orders")
    assert "non-information_schema" in exc.value.message


def test_chokepoint_rejects_info_schema_table_not_in_allowlist() -> None:
    with pytest.raises(WriteAttemptError) as exc:
        _assert_information_schema_only("SELECT * FROM information_schema.processlist")
    assert "not in allowlist" in exc.value.message


def test_chokepoint_rejects_sneaky_subquery() -> None:
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name IN (SELECT name FROM mysql.user)"
        )


def test_chokepoint_rejects_union_to_non_info_schema() -> None:
    # UNION turns the statement into exp.Union, not exp.Select — also rejected.
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only(
            "SELECT column_name FROM information_schema.columns "
            "UNION ALL SELECT name FROM users"
        )


def test_chokepoint_rejects_no_table_select() -> None:
    with pytest.raises(WriteAttemptError):
        _assert_information_schema_only("SELECT 1")


# ---------------------------------------------------------------------------
# MetabaseClient.authenticate
# ---------------------------------------------------------------------------


@respx.mock
async def test_authenticate_stores_session_token() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "sess-abc"})
    async with MetabaseClient(_settings()) as client:
        token = await client.authenticate()
    assert token == "sess-abc"


@respx.mock
async def test_authenticate_401_raises_auth_failed() -> None:
    respx.post("http://metabase.test/api/session").respond(401, json={})
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(MetabaseAuthFailedError):
            await client.authenticate()


@respx.mock
async def test_authenticate_403_raises_auth_failed() -> None:
    respx.post("http://metabase.test/api/session").respond(403, json={})
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(MetabaseAuthFailedError):
            await client.authenticate()


@respx.mock
async def test_authenticate_missing_id_raises() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={})
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(MetabaseAuthFailedError):
            await client.authenticate()


# ---------------------------------------------------------------------------
# MetabaseClient.list_databases
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_databases_parses_legacy_shape() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    respx.get("http://metabase.test/api/database").respond(
        200, json=[{"id": 312, "name": "prod"}]
    )
    async with MetabaseClient(_settings()) as client:
        out = await client.list_databases()
    assert out == [{"id": 312, "name": "prod"}]


@respx.mock
async def test_list_databases_parses_data_envelope() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    respx.get("http://metabase.test/api/database").respond(
        200, json={"data": [{"id": 1}], "total": 1}
    )
    async with MetabaseClient(_settings()) as client:
        out = await client.list_databases()
    assert out == [{"id": 1}]


# ---------------------------------------------------------------------------
# MetabaseClient.run_native_metadata_query
# ---------------------------------------------------------------------------


@respx.mock
async def test_native_query_refuses_non_info_schema_before_http() -> None:
    route = respx.post("http://metabase.test/api/dataset").respond(200, json={})
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(WriteAttemptError):
            await client.run_native_metadata_query(312, "SELECT * FROM mysql.user")
    assert route.called is False


@respx.mock
async def test_native_query_happy_path_builds_correct_body() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    route = respx.post("http://metabase.test/api/dataset").respond(
        200, json={"data": {"cols": [{"name": "table_name"}], "rows": [["orders"]]}}
    )
    async with MetabaseClient(_settings()) as client:
        out = await client.run_native_metadata_query(
            312, "SELECT table_name FROM information_schema.tables"
        )
    assert out["data"]["rows"] == [["orders"]]
    import json as _json

    body = _json.loads(route.calls.last.request.read())
    assert body["database"] == 312
    assert body["type"] == "native"


@respx.mock
async def test_native_query_with_template_params() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    route = respx.post("http://metabase.test/api/dataset").respond(
        200, json={"data": {"cols": [], "rows": []}}
    )
    async with MetabaseClient(_settings()) as client:
        await client.run_native_metadata_query(
            312,
            "SELECT column_name FROM information_schema.columns WHERE table_name = {{tbl}}",
            params={"tbl": "orders"},
        )
    body = route.calls.last.request.read().decode()
    assert '"tbl"' in body
    assert '"orders"' in body


@respx.mock
async def test_native_query_refreshes_session_on_401() -> None:
    respx.post("http://metabase.test/api/session").mock(
        side_effect=[
            httpx.Response(200, json={"id": "old"}),
            httpx.Response(200, json={"id": "new"}),
        ]
    )
    respx.post("http://metabase.test/api/dataset").mock(
        side_effect=[
            httpx.Response(401, json={}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with MetabaseClient(_settings()) as client:
        out = await client.run_native_metadata_query(
            312, "SELECT * FROM information_schema.columns"
        )
    assert out == {"ok": True}


@respx.mock
async def test_native_query_second_401_raises_auth_failed() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "x"})
    respx.post("http://metabase.test/api/dataset").respond(401, json={})
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(MetabaseAuthFailedError):
            await client.run_native_metadata_query(
                312, "SELECT * FROM information_schema.columns"
            )


@respx.mock
async def test_native_query_retries_5xx_and_succeeds(no_sleep: None) -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    respx.post("http://metabase.test/api/dataset").mock(
        side_effect=[
            httpx.Response(503, json={}),
            httpx.Response(200, json={"data": {"cols": [], "rows": []}}),
        ]
    )
    async with MetabaseClient(_settings()) as client:
        out = await client.run_native_metadata_query(
            312, "SELECT * FROM information_schema.columns"
        )
    assert "data" in out


@respx.mock
async def test_native_query_exhausts_5xx_retries(no_sleep: None) -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    route = respx.post("http://metabase.test/api/dataset").respond(503, json={})
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await client.run_native_metadata_query(
                312, "SELECT * FROM information_schema.columns"
            )
    assert exc.value.response.status_code == 503
    assert route.call_count == 3


@respx.mock
async def test_native_query_body_status_failed_raises() -> None:
    """Metabase reports query errors as 2xx + status:'failed' in the body —
    they must not normalize to 'zero rows'."""
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    respx.post("http://metabase.test/api/dataset").respond(
        202,
        json={"status": "failed", "error": "Table 'nope.orders' doesn't exist"},
    )
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(MetabaseQueryFailedError) as exc:
            await client.run_native_metadata_query(
                312, "SELECT * FROM information_schema.columns"
            )
    assert "doesn't exist" in exc.value.message


@respx.mock
async def test_api_key_mode_skips_session_login() -> None:
    session_route = respx.post("http://metabase.test/api/session").respond(
        200, json={"id": "s"}
    )
    dataset_route = respx.post("http://metabase.test/api/dataset").respond(
        200, json={"data": {"cols": [], "rows": []}}
    )
    settings = _settings()
    settings.metabase_api_key = SecretStr("mb_key_123")
    async with MetabaseClient(settings) as client:
        await client.run_native_metadata_query(
            312, "SELECT * FROM information_schema.columns"
        )
    assert session_route.called is False
    assert dataset_route.calls.last.request.headers["X-API-KEY"] == "mb_key_123"


@respx.mock
async def test_concurrent_cold_calls_login_once() -> None:
    session_route = respx.post("http://metabase.test/api/session").respond(
        200, json={"id": "s"}
    )
    respx.post("http://metabase.test/api/dataset").respond(
        200, json={"data": {"cols": [], "rows": []}}
    )
    async with MetabaseClient(_settings()) as client:
        await asyncio.gather(
            *(
                client.run_native_metadata_query(
                    312, "SELECT * FROM information_schema.columns"
                )
                for _ in range(5)
            )
        )
    assert session_route.call_count == 1


@respx.mock
async def test_native_query_timeout_raises_upstream_timeout() -> None:
    respx.post("http://metabase.test/api/session").respond(200, json={"id": "s"})
    respx.post("http://metabase.test/api/dataset").mock(
        side_effect=httpx.ConnectTimeout("timeout")
    )
    async with MetabaseClient(_settings()) as client:
        with pytest.raises(UpstreamTimeoutError):
            await client.run_native_metadata_query(
                312, "SELECT * FROM information_schema.columns"
            )
