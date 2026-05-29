"""Pydantic models for the repo registry.

Schema mirrors debb/.claude/skills/debug-repo/schemas/* with one
deliberate narrowing: zensql only generates SQL against Metabase, so
quickwit/prometheus sources are not modelled here. The JSON shape stays
debb-compatible for the `metabase` discriminator branch.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_DB_ENGINES = {"mariadb", "mysql", "postgres", "clickhouse", "oracle", "mssql"}


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetabaseSourceMetadata(_StrictBase):
    database: str = Field(min_length=1)
    database_id: int = Field(ge=1)
    database_type: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    tables: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def model_post_init(self, _: object) -> None:
        if self.database_type is not None and self.database_type not in _DB_ENGINES:
            raise ValueError(
                f"database_type {self.database_type!r} not in {sorted(_DB_ENGINES)}"
            )


class MetabaseSource(_StrictBase):
    name: Literal["metabase"]
    metadata: MetabaseSourceMetadata


class ConnectionBlock(_StrictBase):
    environment: str = Field(min_length=1)
    sources: list[MetabaseSource] = Field(default_factory=list)


class RepoEntry(_StrictBase):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1)
    path: str = Field(pattern=r"^/")
    tags: list[str] = Field(min_length=1)
    connection: list[ConnectionBlock] = Field(min_length=1)

    def metabase_sources(self, environment: str | None = None) -> list[MetabaseSource]:
        out: list[MetabaseSource] = []
        for block in self.connection:
            if environment and block.environment != environment:
                continue
            out.extend(block.sources)
        return out


class RegistryDocument(_StrictBase):
    version: int = 1
    repos: list[RepoEntry] = Field(default_factory=list)
