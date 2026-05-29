"""Thin subprocess wrapper around the `code-review-graph` CLI.

zensql calls these helpers when a repo is added or removed from its registry so
the upstream code-review-graph multi-repo registry at
`~/.code-review-graph/registry.json` stays in sync. We deliberately do **not**
run `code-review-graph install` — that modifies the registered repo's own
`.claude/` / `CLAUDE.md` / `AGENTS.md` files, which is debb's use case but not
ours.

All functions return a `CrgSyncResult` dict with a stable shape so callers can
log a single record per attempt regardless of outcome.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import TypedDict

_CRG_BIN = "code-review-graph"

_DEFAULT_REGISTER_TIMEOUT_S = 30
_DEFAULT_UNREGISTER_TIMEOUT_S = 30
_DEFAULT_BUILD_TIMEOUT_S = 600


class CrgSyncResult(TypedDict, total=False):
    ran: bool
    ok: bool
    command: str
    exit_code: int
    stdout: str
    stderr: str
    skipped_reason: str
    error: str


def _which() -> str | None:
    return shutil.which(_CRG_BIN)


def _skipped(reason: str, hint: str | None = None) -> CrgSyncResult:
    out: CrgSyncResult = {"ran": False, "skipped_reason": reason}
    if hint:
        out["stderr"] = hint
    return out


def _invoke(cmd: list[str], timeout: int) -> CrgSyncResult:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "ran": True,
            "ok": False,
            "command": " ".join(cmd),
            "error": f"command did not finish within {timeout}s",
        }
    except OSError as e:
        return {"ran": True, "ok": False, "command": " ".join(cmd), "error": str(e)}
    return {
        "ran": True,
        "ok": proc.returncode == 0,
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def sync_register(
    name: str,
    path: str,
    *,
    skip: bool = False,
    timeout: int = _DEFAULT_REGISTER_TIMEOUT_S,
) -> CrgSyncResult:
    """Add `path` to the upstream registry with `name` as its alias."""
    if skip:
        return _skipped("--no-graph-sync")
    cli = _which()
    if cli is None:
        return _skipped(
            "code-review-graph CLI not found on PATH",
            hint="install via `uv add code-review-graph`",
        )
    return _invoke([cli, "register", path, "--alias", name], timeout)


def sync_unregister(
    name: str,
    *,
    skip: bool = False,
    timeout: int = _DEFAULT_UNREGISTER_TIMEOUT_S,
) -> CrgSyncResult:
    """Remove the alias `name` from the upstream registry."""
    if skip:
        return _skipped("--no-graph-sync")
    cli = _which()
    if cli is None:
        return _skipped("code-review-graph CLI not found on PATH")
    return _invoke([cli, "unregister", name], timeout)


def sync_build(
    path: str,
    *,
    skip: bool = False,
    timeout: int = _DEFAULT_BUILD_TIMEOUT_S,
) -> CrgSyncResult:
    """Parse the repo at `path` into the upstream graph database."""
    if skip:
        return _skipped("--no-graph-build")
    cli = _which()
    if cli is None:
        return _skipped("code-review-graph CLI not found on PATH")
    return _invoke([cli, "build", "--repo", path], timeout)
