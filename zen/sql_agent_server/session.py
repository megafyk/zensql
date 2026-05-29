"""Per-user Claude Code conversation sessions.

Each Telegram user gets a deterministic Claude Code session id so follow-up
requests resume the same conversation and the agent remembers prior context
(e.g. "now filter that by last week"). See
https://code.claude.com/docs/en/sessions — we pass `--session-id <id>` to
create and `--resume <id>` to continue. Transcripts live under
`~/.claude/projects/<encoded-cwd>/<id>.jsonl`; Claude Code expires them after
`cleanupPeriodDays` (default 30).
"""
from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

# Fixed namespace so a given user_id always maps to the same session id —
# the on-disk transcript is reused across server restarts.
_SESSION_NS = uuid5(NAMESPACE_URL, "zensql.telegram.session")

# Substrings Claude Code emits when --session-id / --resume disagree with the
# on-disk state (verified against the CLI): `--session-id` on an existing id ->
# "is already in use"; `--resume` on a missing id -> "No conversation found".
_SESSION_STATE_ERROR_MARKERS = ("already in use", "no conversation found")


def session_id_for(user_id: str) -> str:
    """Deterministic per-user Claude Code session id (a UUID string)."""
    return str(uuid5(_SESSION_NS, user_id))


def session_file(project_dir: str, session_id: str) -> Path:
    """Transcript path Claude Code uses for `session_id` under `project_dir`.

    Claude Code encodes the absolute cwd by replacing every non-alphanumeric
    character with '-' (verified against the CLI)."""
    encoded = "".join(c if c.isalnum() else "-" for c in os.path.abspath(project_dir))
    return Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"


def is_session_state_error(text: str) -> bool:
    """True if output signals a --session-id/--resume vs on-disk mismatch."""
    low = text.lower()
    return any(marker in low for marker in _SESSION_STATE_ERROR_MARKERS)


class UserLocks:
    """Per-key asyncio locks so the same user's session is never resumed by two
    concurrent `claude` processes — concurrent resume corrupts the transcript."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


@lru_cache(maxsize=1)
def get_user_locks() -> UserLocks:
    """Process-wide singleton, shared across the per-request orchestrators."""
    return UserLocks()
