"""Agent runner abstractions.

`AgentRunnerProtocol` is the seam between the orchestrator and whatever
spawns Claude Code. Production uses `ClaudeCodeRunner` (`asyncio.create_subprocess_exec`
on the `claude` CLI); tests use `FakeAgentRunner`.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from zen.sql_agent_server.session import is_session_state_error, session_file

# Env vars that must NOT leak into the spawned `claude -p` subprocess.
# A parent Claude Code session exports these. An ANTHROPIC_API_KEY (or
# ANTHROPIC_AUTH_TOKEN) outranks the machine's Claude Code subscription OAuth and
# has no fallback when invalid, so leaking one breaks headless auth. The
# CLAUDECODE/CLAUDE_CODE_* markers flag a nested invocation; dropping them lets the
# child run as a fresh top-level call authenticated via ~/.claude/.credentials.json.
_STRIPPED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_TMPDIR",
    "CLAUDE_EFFORT",
)


def subscription_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the environment minus vars that would override subscription OAuth or
    mark the call as nested, so `claude -p` authenticates via the local Claude
    Code subscription instead of a leaked/invalid API key."""
    env = dict(os.environ if base is None else base)
    for var in _STRIPPED_ENV_VARS:
        env.pop(var, None)
    return env


@dataclass
class AgentRunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class AgentRunnerProtocol(Protocol):
    async def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        mcp_config_path: str,
        allowed_tools: list[str],
        timeout_s: float,
        session_id: str | None = None,
    ) -> AgentRunResult: ...


class ClaudeCodeRunner:
    """Real runner: spawns `claude -p ...` and reads stdout."""

    def __init__(self, *, bin_path: str = "claude", project_dir: str = ".") -> None:
        self._bin = bin_path
        self._project_dir = project_dir

    async def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        mcp_config_path: str,
        allowed_tools: list[str],
        timeout_s: float,
        session_id: str | None = None,
    ) -> AgentRunResult:
        cmd = [
            self._bin,
            "-p",
            user_prompt,
            "--append-system-prompt",
            system_prompt,
            "--mcp-config",
            mcp_config_path,
            "--allowedTools",
            ",".join(allowed_tools),
            "--output-format",
            "text",
        ]
        if session_id is None:
            return await self._exec(cmd, timeout_s)

        # Resume the user's session if its transcript exists, else create it.
        resume = self._resume_exists(session_id)
        result = await self._exec(cmd + self._session_flag(session_id, resume), timeout_s)
        # If the guess disagreed with disk (cleanup, race, or a session created
        # elsewhere), the CLI says so — flip the mode once and retry.
        if (
            not result.timed_out
            and result.exit_code != 0
            and is_session_state_error(result.stderr or result.stdout or "")
        ):
            result = await self._exec(
                cmd + self._session_flag(session_id, not resume), timeout_s
            )
        return result

    @staticmethod
    def _session_flag(session_id: str, resume: bool) -> list[str]:
        return ["--resume", session_id] if resume else ["--session-id", session_id]

    def _resume_exists(self, session_id: str) -> bool:
        return session_file(self._project_dir, session_id).exists()

    async def _exec(self, cmd: list[str], timeout_s: float) -> AgentRunResult:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self._project_dir,
            env=subscription_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return AgentRunResult(stdout="", stderr="", exit_code=-1, timed_out=True)
        return AgentRunResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            exit_code=proc.returncode or 0,
            timed_out=False,
        )


@dataclass
class FakeAgentRunner:
    """Test fixture. Captures every `.run(...)` call into `calls`."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        mcp_config_path: str,
        allowed_tools: list[str],
        timeout_s: float,
        session_id: str | None = None,
    ) -> AgentRunResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "mcp_config_path": mcp_config_path,
                "allowed_tools": allowed_tools,
                "timeout_s": timeout_s,
                "session_id": session_id,
            }
        )
        return AgentRunResult(
            stdout=self.stdout,
            stderr=self.stderr,
            exit_code=self.exit_code,
            timed_out=self.timed_out,
        )
