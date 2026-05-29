"""Normalized schema-metadata models.

Produced by the normalizer module from raw `information_schema.*` rows and
consumed by the Schema MCP tools. Identifier fields are regex-validated so
they're safe to interpolate into MCP tool responses and (post-validation) into
SQL templates.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")

ColumnKey = Literal["PRI", "UNI", "MUL", ""] | None


def _check_identifier(value: str, field_name: str) -> str:
    if not _IDENT_RE.match(value):
        raise ValueError(f"invalid identifier for {field_name}: {value!r}")
    return value


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnMetadata(_StrictBase):
    name: str
    data_type: str
    is_nullable: bool
    default: str | None = None
    key: ColumnKey = None
    extra: str | None = None
    comment: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _check_identifier(v, "column name")


class IndexMetadata(_StrictBase):
    name: str
    columns: list[str] = Field(default_factory=list)
    unique: bool
    type: str | None = None

    @field_validator("columns")
    @classmethod
    def _validate_columns(cls, v: list[str]) -> list[str]:
        for c in v:
            _check_identifier(c, "index column")
        return v


class PartitionMetadata(_StrictBase):
    name: str
    method: str
    expression: str | None = None
    description: str | None = None
    table_rows: int | None = None


class Relationship(_StrictBase):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    constraint: str | None = None

    @field_validator("from_table", "to_table", "from_column", "to_column")
    @classmethod
    def _validate_ident(cls, v: str) -> str:
        return _check_identifier(v, "relationship identifier")


class TableMetadata(_StrictBase):
    schema_: str = Field(alias="schema")
    name: str
    comment: str | None = None
    columns: list[ColumnMetadata] = Field(default_factory=list)
    indexes: list[IndexMetadata] = Field(default_factory=list)
    partitions: list[PartitionMetadata] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    retrieved_at: datetime | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("schema_", "name")
    @classmethod
    def _validate_ident(cls, v: str) -> str:
        return _check_identifier(v, "table identifier")
