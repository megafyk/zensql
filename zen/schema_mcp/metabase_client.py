"""Async Metabase API client.

Single instance per Schema MCP process. Owns one `httpx.AsyncClient`.
When `METABASE_API_KEY` is set, every request authenticates statelessly via
the `X-API-KEY` header. Otherwise the client authenticates lazily with
username/password via `POST /api/session` and refreshes the session once on
401. Exposes a narrow read-only surface:

- `list_databases()` — `GET /api/database`
- `run_native_metadata_query(database_id, sql, params=None)` — `POST /api/dataset`

`run_native_metadata_query` calls `_assert_information_schema_only(sql)`
**before** any HTTP roundtrip, so non-`information_schema` SQL never leaves
this process. The allowed `information_schema.<table>` set is small and
hardcoded.

Retry policy: 5xx is retried up to 3 attempts with exponential backoff
(0.5s, 1.0s, 2.0s). 4xx is never retried except for one 401-refresh path on
`POST /api/dataset`.
"""
from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

import httpx
import sqlglot
from sqlglot import exp

from zen.config.settings import Settings
from zen.mcp_tools.errors import (
    MetabaseAuthFailedError,
    MetabaseQueryFailedError,
    UpstreamTimeoutError,
    WriteAttemptError,
)

_DEFAULT_RETRIES = 3
_BACKOFF_BASE_S = 0.5

_ALLOWED_INFO_SCHEMA_TABLES: frozenset[str] = frozenset(
    {
        "columns",
        "statistics",
        "partitions",
        "key_column_usage",
        "referential_constraints",
        "tables",
    }
)


def _assert_information_schema_only(sql: str) -> None:
    """Reject anything that isn't a single SELECT over `information_schema.*`.

    Raises:
        WriteAttemptError: SQL violates the read-only-metadata invariant.
    """
    cleaned = sql.strip()
    if not cleaned:
        raise WriteAttemptError("empty SQL")

    try:
        statements = sqlglot.parse(cleaned, dialect="mysql")
    except (sqlglot.errors.ParseError, ValueError) as e:
        raise WriteAttemptError(f"unparseable SQL: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise WriteAttemptError(
            f"exactly one statement permitted, got {len(statements)}"
        )

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise WriteAttemptError(f"only SELECT permitted, got {type(stmt).__name__}")

    seen_tables = False
    for table in stmt.find_all(exp.Table):
        seen_tables = True
        db = (table.db or "").lower()
        if db != "information_schema":
            raise WriteAttemptError(
                f"non-information_schema reference: {table.sql(dialect='mysql')}"
            )
        if table.name.lower() not in _ALLOWED_INFO_SCHEMA_TABLES:
            raise WriteAttemptError(
                f"information_schema.{table.name} not in allowlist"
            )
    if not seen_tables:
        raise WriteAttemptError("SELECT must reference at least one information_schema table")


class MetabaseClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(settings.metabase_query_timeout_s),
                write=10.0,
                pool=10.0,
            )
        )
        self._session_token: str | None = None
        self._auth_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._settings.metabase_base_url.rstrip("/")

    async def __aenter__(self) -> MetabaseClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ auth

    async def authenticate(self) -> str:
        """Force a fresh `/api/session` login and return the session id."""
        async with self._auth_lock:
            return await self._login_locked()

    async def _login_locked(self) -> str:
        body = {
            "username": self._settings.metabase_username.get_secret_value(),
            "password": self._settings.metabase_password.get_secret_value(),
        }
        try:
            resp = await self._client.post(f"{self.base_url}/api/session", json=body)
        except httpx.TimeoutException as e:
            raise UpstreamTimeoutError("metabase /api/session timed out") from e
        if resp.status_code in (401, 403):
            raise MetabaseAuthFailedError(
                f"/api/session rejected credentials ({resp.status_code})"
            )
        resp.raise_for_status()
        token = resp.json().get("id")
        if not token:
            raise MetabaseAuthFailedError("/api/session response missing 'id'")
        self._session_token = str(token)
        return self._session_token

    async def _ensure_session(self) -> str:
        if self._session_token is not None:
            return self._session_token
        async with self._auth_lock:
            # Re-check inside the lock: concurrent first calls would otherwise
            # each pay their own POST /api/session login.
            if self._session_token is None:
                return await self._login_locked()
            return self._session_token

    # ------------------------------------------------------------------ low-level

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        allow_refresh: bool = True,
    ) -> httpx.Response:
        api_key = self._settings.metabase_api_key.get_secret_value()
        last_resp: httpx.Response | None = None
        for attempt in range(_DEFAULT_RETRIES):
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if api_key:
                headers["X-API-KEY"] = api_key
            else:
                headers["X-Metabase-Session"] = await self._ensure_session()
            try:
                resp = await self._client.request(
                    method, f"{self.base_url}{path}", headers=headers, json=json
                )
            except httpx.TimeoutException as e:
                raise UpstreamTimeoutError(f"metabase {method} {path} timed out") from e
            last_resp = resp

            if resp.status_code == 401 and not api_key and allow_refresh:
                self._session_token = None
                allow_refresh = False
                continue
            if 500 <= resp.status_code < 600 and attempt < _DEFAULT_RETRIES - 1:
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            return resp

        assert last_resp is not None
        return last_resp

    # ------------------------------------------------------------------ public

    async def list_databases(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/api/database")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            return list(data["data"])
        if isinstance(data, list):
            return data
        return []

    async def run_native_metadata_query(
        self,
        database_id: int,
        sql: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _assert_information_schema_only(sql)
        params = params or {}
        body: dict[str, Any] = {
            "database": database_id,
            "type": "native",
            "native": {
                "query": sql,
                "template-tags": {
                    name: {
                        "name": name,
                        "display-name": name,
                        "type": "text",
                        "required": True,
                    }
                    for name in params
                },
            },
            "parameters": [
                {
                    "type": "text",
                    "target": ["variable", ["template-tag", name]],
                    "value": value,
                }
                for name, value in params.items()
            ],
        }
        resp = await self._request("POST", "/api/dataset", json=body)
        if resp.status_code == 401:
            raise MetabaseAuthFailedError("session refresh did not recover from 401")
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        # Metabase reports query-execution failures (SQL errors, permission
        # errors, warehouse outages) in the body under a 2xx status — without
        # this check they would normalize to "zero rows".
        if payload.get("status") == "failed" or payload.get("error"):
            raise MetabaseQueryFailedError(str(payload.get("error") or "query failed")[:500])
        return payload
