"""Outbound response models for the SQL agent server."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GeneratedSqlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    job_id: UUID
    sql: str | None = None
    # Set instead of `sql` when the user's message wasn't a data request and the
    # agent replied conversationally (a joke / small talk).
    chat_reply: str | None = None
    explanation: str | None = None
    tables_referenced: list[str] = Field(default_factory=list)
    # Metabase database name(s) the SQL targets, resolved from the registry,
    # so the bot can tell the user where to run it.
    metabase_databases: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
