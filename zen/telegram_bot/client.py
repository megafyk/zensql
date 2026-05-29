"""HTTP client used by the Telegram bot to call the SQL agent server."""
from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import SecretStr

from zen.models.requests import UserSqlRequest
from zen.models.responses import GeneratedSqlResponse


class AgentClientProtocol(Protocol):
    async def generate(self, request: UserSqlRequest) -> GeneratedSqlResponse: ...


class AgentClient:
    def __init__(
        self,
        base_url: str,
        token: SecretStr,
        timeout_s: float = 65.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = httpx.Timeout(timeout_s, connect=10.0)

    async def generate(self, request: UserSqlRequest) -> GeneratedSqlResponse:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self._base_url}/v1/sql/generate",
                json=request.model_dump(mode="json"),
                headers={
                    "Authorization": f"Bearer {self._token.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
            r.raise_for_status()
            return GeneratedSqlResponse.model_validate(r.json())
