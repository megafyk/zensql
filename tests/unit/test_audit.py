from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from zen.sql_agent_server.audit import (
    AuditLogger,
    redact_sql,
    redact_text,
)

# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


def test_redact_text_returns_hash_and_safe_preview() -> None:
    out = redact_text("hello world!")
    assert "sha256" in out and len(out["sha256"]) == 64
    # special chars replaced with '·'
    assert out["preview"] == "hello world·"


def test_redact_text_truncates_to_64_chars() -> None:
    out = redact_text("a" * 200)
    assert len(out["preview"]) == 64


def test_redact_sql_returns_hash_and_length() -> None:
    sql = "SELECT 1 FROM orders LIMIT 1"
    out = redact_sql(sql)
    assert out["length"] == len(sql)
    assert out["sha256"] != ""


# ---------------------------------------------------------------------------
# AuditLogger I/O
# ---------------------------------------------------------------------------


def test_log_appends_jsonl_line(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path, actor="test")
    rid = uuid4()
    event = logger.log(
        "request_received",
        request_id=rid,
        details={"text": redact_text("hello")},
    )
    path = logger.daily_path()
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_type"] == "request_received"
    assert payload["actor"] == "test"
    assert payload["request_id"] == str(rid)
    assert payload["event_id"] == str(event.event_id)
    assert "text" in payload["details"]
    assert payload["details"]["text"]["sha256"]


def test_log_multiple_events_appends(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    logger.log("a")
    logger.log("b")
    logger.log("c")
    lines = logger.daily_path().read_text().splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["a", "b", "c"]


def test_daily_path_uses_utc_date(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    fixed = datetime(2026, 5, 19, tzinfo=UTC)
    assert logger.daily_path(now=fixed).name == "audit-2026-05-19.jsonl"


def test_strips_credentials_from_details(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    logger.log(
        "test_event",
        details={
            "user": "alice",
            "password": "hunter2",
            "session_token": "abc123",
            "nested": {"api_key": "secret-key"},
            "agent_api_token": "xxx",
        },
    )
    payload = json.loads(logger.daily_path().read_text())
    d = payload["details"]
    assert d["user"] == "alice"
    assert d["password"] == "<redacted>"
    assert d["session_token"] == "<redacted>"
    assert d["nested"]["api_key"] == "<redacted>"
    assert d["agent_api_token"] == "<redacted>"


def test_raw_text_never_appears_in_log(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    secret_phrase = "this is the user's secret prompt"
    logger.log("request_received", details={"text": redact_text(secret_phrase)})
    payload = logger.daily_path().read_text()
    assert secret_phrase not in payload


def test_raw_sql_never_appears_in_log(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    sql = "SELECT secret_column FROM hidden_table WHERE x = 'tell-tale-string'"
    logger.log("sql_generated", details={"sql": redact_sql(sql)})
    payload = logger.daily_path().read_text()
    assert "tell-tale-string" not in payload
    assert "hidden_table" not in payload


def test_payload_hash_is_stable(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    a = logger.build_event("x", details={"a": 1, "b": 2})
    b = logger.build_event("x", details={"b": 2, "a": 1})  # different key order
    assert a.payload_hash == b.payload_hash
