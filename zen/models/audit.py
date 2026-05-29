"""Audit event model.

One JSONL line per event in `${AUDIT_LOG_DIR}/audit-YYYY-MM-DD.jsonl`. The
`details` field is free-form per event_type but MUST NOT contain raw user
text, raw generated SQL, or any credentials — use `redact_text` /
`redact_sql` helpers in `zen.sql_agent_server.audit` to summarise those.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warn", "error"]


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    request_id: UUID | None = None
    job_id: UUID | None = None
    actor: str
    payload_hash: str
    created_at: datetime
    severity: Severity = "info"
    details: dict[str, Any] = Field(default_factory=dict)
