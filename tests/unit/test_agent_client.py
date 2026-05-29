from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import SecretStr

from zen.models.requests import UserSqlRequest
from zen.telegram_bot.client import AgentClient


def _request() -> UserSqlRequest:
    return UserSqlRequest(
        request_id=uuid4(),
        source="telegram",
        user_id="tg:1",
        text="orders this week",
    )


@respx.mock
async def test_generate_happy_path() -> None:
    req = _request()
    route = respx.post("http://srv/v1/sql/generate").respond(
        200,
        json={
            "request_id": str(req.request_id),
            "job_id": str(uuid4()),
            "sql": "-- AI-GENERATED SQL\nSELECT 1;",
            "explanation": "ok",
            "tables_referenced": [],
            "warnings": ["AI-generated SQL. Review before executing."],
            "error_code": None,
            "error_message": None,
        },
    )
    client = AgentClient(base_url="http://srv", token=SecretStr("abc"))
    resp = await client.generate(req)
    assert resp.request_id == req.request_id
    assert resp.sql is not None and "SELECT 1" in resp.sql
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer abc"


@respx.mock
async def test_generate_strips_trailing_slash_in_base_url() -> None:
    respx.post("http://srv/v1/sql/generate").respond(
        200,
        json={
            "request_id": str(_request().request_id),
            "job_id": str(uuid4()),
            "sql": "SELECT 1;",
            "warnings": ["x"],
        },
    )
    client = AgentClient(base_url="http://srv///", token=SecretStr("abc"))
    await client.generate(_request())


@respx.mock
async def test_generate_raises_on_401() -> None:
    respx.post("http://srv/v1/sql/generate").respond(401)
    client = AgentClient(base_url="http://srv", token=SecretStr("bad"))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.generate(_request())
    assert exc.value.response.status_code == 401


@respx.mock
async def test_generate_raises_on_5xx() -> None:
    respx.post("http://srv/v1/sql/generate").respond(503)
    client = AgentClient(base_url="http://srv", token=SecretStr("abc"))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.generate(_request())
    assert exc.value.response.status_code == 503


@respx.mock
async def test_generate_raises_on_transport_error() -> None:
    respx.post("http://srv/v1/sql/generate").mock(side_effect=httpx.ConnectError("boom"))
    client = AgentClient(base_url="http://srv", token=SecretStr("abc"))
    with pytest.raises(httpx.ConnectError):
        await client.generate(_request())
