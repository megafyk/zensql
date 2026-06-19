"""Append-only JSONL audit log + redaction helpers.

One line per event into `<log_dir>/audit-YYYY-MM-DD.jsonl`. Process-wide
serialisation via a threading.Lock so concurrent writes from FastAPI workers
don't interleave.

Redaction helpers:
- `redact_text(text)` → `{"sha256": ..., "preview": ...}` (preview is the
  first 64 chars with non-word characters replaced by '·' so a glance at the
  log shows *something* without leaking PII or injection payloads).
- `redact_sql(sql)` → `{"sha256": ..., "length": ...}`.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from zen.models.audit import AuditEvent, Severity

_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "session_token",
        "api_key",
        "secret",
        "agent_api_token",
        "metabase_password",
        "telegram_bot_token",
    }
)


def _sha(payload: Any) -> str:
    serialised = json.dumps(payload, default=str, sort_keys=True)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def redact_text(text: str, *, max_preview: int = 64) -> dict[str, str]:
    preview = re.sub(r"[^\w\s]", "·", text)[:max_preview]
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "preview": preview,
    }


def redact_sql(sql: str) -> dict[str, Any]:
    return {
        "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "length": len(sql),
    }


def _strip_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k.lower() in _CREDENTIAL_KEYS:
            out[k] = "<redacted>"
        elif isinstance(v, dict):
            out[k] = _strip_credentials(v)
        elif isinstance(v, list):
            # e.g. "violations": [{...}, ...] — dicts inside lists must not
            # bypass the credential-key scrub.
            out[k] = [_strip_credentials(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


class AuditLogger:
    def __init__(self, log_dir: Path | str, *, actor: str = "agent_server") -> None:
        self._log_dir = Path(log_dir)
        self._actor = actor
        self._lock = threading.Lock()

    @property
    def actor(self) -> str:
        return self._actor

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def daily_path(self, *, now: datetime | None = None) -> Path:
        ts = now or datetime.now(UTC)
        return self._log_dir / f"audit-{ts.strftime('%Y-%m-%d')}.jsonl"

    def build_event(
        self,
        event_type: str,
        *,
        request_id: UUID | None = None,
        job_id: UUID | None = None,
        actor: str | None = None,
        severity: Severity = "info",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        d = _strip_credentials(details or {})
        return AuditEvent(
            event_type=event_type,
            request_id=request_id,
            job_id=job_id,
            actor=actor or self._actor,
            payload_hash=_sha(d),
            created_at=datetime.now(UTC),
            severity=severity,
            details=d,
        )

    def emit(self, event: AuditEvent) -> None:
        path = self.daily_path(now=event.created_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json() + "\n"
        with self._lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def log(
        self,
        event_type: str,
        *,
        request_id: UUID | None = None,
        job_id: UUID | None = None,
        actor: str | None = None,
        severity: Severity = "info",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = self.build_event(
            event_type,
            request_id=request_id,
            job_id=job_id,
            actor=actor,
            severity=severity,
            details=details,
        )
        self.emit(event)
        return event


class NullAuditLogger(AuditLogger):
    """No-op subclass for tests that don't care about audit output."""

    def __init__(self) -> None:
        super().__init__(Path("/tmp/zensql-null-audit"), actor="test")

    def emit(self, event: AuditEvent) -> None:  # pragma: no cover - no I/O
        return
