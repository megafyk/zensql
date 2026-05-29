from __future__ import annotations

from pathlib import Path
from uuid import UUID

from zen.sql_agent_server.session import (
    is_session_state_error,
    session_file,
    session_id_for,
)


def test_session_id_is_deterministic_and_per_user() -> None:
    a1 = session_id_for("tg:1")
    a2 = session_id_for("tg:1")
    b = session_id_for("tg:2")
    assert a1 == a2  # stable across calls (survives restarts)
    assert a1 != b  # distinct per user
    UUID(a1)  # valid UUID string


def test_session_file_encodes_cwd_and_id() -> None:
    sid = "0c60a6a6-7ccd-40f4-b108-aad289a37127"
    p = session_file("/home/myadmin/tools/projects/zensql", sid)
    assert p.name == f"{sid}.jsonl"
    # Non-alphanumeric chars of the abspath become '-'.
    assert p.parent.name == "-home-myadmin-tools-projects-zensql"
    assert p.parent.parent == Path.home() / ".claude" / "projects"


def test_is_session_state_error_matches_cli_messages() -> None:
    assert is_session_state_error("Error: Session ID abc is already in use.")
    assert is_session_state_error("No conversation found with session ID: abc")
    assert not is_session_state_error("Invalid API key · Fix external API key")
    assert not is_session_state_error("")
