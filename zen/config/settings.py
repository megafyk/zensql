"""Typed application settings loaded from environment / .env file.

Single source of truth for runtime configuration across the SQL agent server,
Telegram bot, and MCP servers. Secrets use `pydantic.SecretStr` so they never
appear in `repr()` or default logging.
"""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class StatementFamily(StrEnum):
    SELECT = "SELECT"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_max_input_chars: int = Field(default=1000, ge=1, le=4096)

    agent_api_host: str = Field(default="0.0.0.0")
    agent_api_port: int = Field(default=8080, ge=1, le=65535)
    agent_api_token: SecretStr = Field(default=SecretStr(""))
    agent_api_base_url: str = Field(default="http://127.0.0.1:8080")
    # Max wall-clock for one `claude -p` run; the model can be slow, so allow 5 min.
    agent_timeout_s: int = Field(default=300, ge=1, le=600)
    allowed_statement_families: Annotated[list[StatementFamily], NoDecode] = Field(
        default_factory=lambda: [StatementFamily.SELECT]
    )

    claude_code_bin: str = Field(default="claude")
    claude_code_project_dir: str = Field(default="")
    mcp_config_path: str = Field(default=".mcp.json")
    # Resume a per-user Claude Code session so follow-up requests keep context.
    session_enabled: bool = Field(default=True)

    metabase_base_url: str = Field(default="")
    metabase_username: SecretStr = Field(default=SecretStr(""))
    metabase_password: SecretStr = Field(default=SecretStr(""))
    # When set, the Schema MCP authenticates via X-API-KEY (stateless) instead
    # of username/password session login.
    metabase_api_key: SecretStr = Field(default=SecretStr(""))
    metabase_query_timeout_s: int = Field(default=15, ge=1, le=120)

    registry_path: str = Field(default=".claude/skills/sql_add_repo/registry.json")
    code_graph_allowed_roots: Annotated[list[str], NoDecode] = Field(default_factory=list)

    audit_log_dir: str = Field(default="var/audit")
    log_level: str = Field(default="INFO")

    @field_validator("allowed_statement_families", mode="before")
    @classmethod
    def _parse_families(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [x.strip().upper() for x in v.split(",") if x.strip()]
        return v

    @field_validator("code_graph_allowed_roots", mode="before")
    @classmethod
    def _parse_roots(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
