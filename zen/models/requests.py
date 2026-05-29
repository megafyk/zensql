"""Inbound request models for the SQL agent server."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContextHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_repos: list[str] = Field(default_factory=list)
    preferred_schemas: list[str] = Field(default_factory=list)


class UserSqlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    source: Literal["telegram"]
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=8000)
    context_hints: ContextHints = Field(default_factory=ContextHints)
