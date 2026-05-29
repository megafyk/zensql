from __future__ import annotations

from zen.sql_agent_server.agent_runner import (
    AgentRunResult,
    ClaudeCodeRunner,
    subscription_env,
)


class _RecExec:
    """Records the cmds passed to ClaudeCodeRunner._exec and returns queued results."""

    def __init__(self, results: list[AgentRunResult]) -> None:
        self.results = list(results)
        self.cmds: list[list[str]] = []

    async def __call__(self, cmd: list[str], timeout_s: float) -> AgentRunResult:
        self.cmds.append(cmd)
        return self.results.pop(0)


def _runner(exec_results: list[AgentRunResult], resume_exists: bool) -> tuple[ClaudeCodeRunner, _RecExec]:
    r = ClaudeCodeRunner(project_dir=".")
    rec = _RecExec(exec_results)
    r._exec = rec  # type: ignore[method-assign]
    r._resume_exists = lambda _sid: resume_exists  # type: ignore[method-assign]
    return r, rec


async def _run(r: ClaudeCodeRunner) -> AgentRunResult:
    return await r.run(
        system_prompt="s", user_prompt="u", mcp_config_path=".mcp.json",
        allowed_tools=["t"], timeout_s=5.0, session_id="SID",
    )


async def test_runner_creates_session_when_no_transcript() -> None:
    r, rec = _runner([AgentRunResult(stdout="ok", stderr="", exit_code=0)], resume_exists=False)
    await _run(r)
    assert rec.cmds[0][-2:] == ["--session-id", "SID"]
    assert len(rec.cmds) == 1  # no flip


async def test_runner_resumes_when_transcript_exists() -> None:
    r, rec = _runner([AgentRunResult(stdout="ok", stderr="", exit_code=0)], resume_exists=True)
    await _run(r)
    assert rec.cmds[0][-2:] == ["--resume", "SID"]
    assert len(rec.cmds) == 1


async def test_runner_flips_to_resume_when_already_in_use() -> None:
    r, rec = _runner(
        [
            AgentRunResult(stdout="", stderr="Session ID SID is already in use.", exit_code=1),
            AgentRunResult(stdout="ok", stderr="", exit_code=0),
        ],
        resume_exists=False,
    )
    res = await _run(r)
    assert rec.cmds[0][-2:] == ["--session-id", "SID"]
    assert rec.cmds[1][-2:] == ["--resume", "SID"]
    assert res.exit_code == 0


async def test_runner_flips_to_create_when_no_conversation_found() -> None:
    r, rec = _runner(
        [
            AgentRunResult(stdout="", stderr="No conversation found with session ID: SID", exit_code=1),
            AgentRunResult(stdout="ok", stderr="", exit_code=0),
        ],
        resume_exists=True,
    )
    res = await _run(r)
    assert rec.cmds[0][-2:] == ["--resume", "SID"]
    assert rec.cmds[1][-2:] == ["--session-id", "SID"]
    assert res.exit_code == 0


async def test_runner_does_not_flip_on_non_session_error() -> None:
    r, rec = _runner(
        [AgentRunResult(stdout="", stderr="Invalid API key", exit_code=1)],
        resume_exists=True,
    )
    res = await _run(r)
    assert len(rec.cmds) == 1  # no retry for unrelated failures
    assert res.exit_code == 1


def test_subscription_env_strips_auth_and_nesting_vars() -> None:
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "ANTHROPIC_API_KEY": "sk-ant-invalid",
        "ANTHROPIC_AUTH_TOKEN": "bearer-token",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_SESSION_ID": "abc",
        "CLAUDE_CODE_EXECPATH": "/x/claude",
        "CLAUDE_CODE_TMPDIR": "/tmp/x",
        "CLAUDE_EFFORT": "max",
    }
    env = subscription_env(base)
    # The leaked auth + nesting markers are gone so claude falls back to OAuth.
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDE_CODE_EXECPATH" not in env
    assert "CLAUDE_CODE_TMPDIR" not in env
    assert "CLAUDE_EFFORT" not in env
    # Everything else is preserved untouched.
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"


def test_subscription_env_does_not_mutate_input() -> None:
    base = {"ANTHROPIC_API_KEY": "sk-ant-invalid", "PATH": "/usr/bin"}
    subscription_env(base)
    assert base["ANTHROPIC_API_KEY"] == "sk-ant-invalid"


def test_subscription_env_tolerates_missing_vars() -> None:
    env = subscription_env({"PATH": "/usr/bin"})
    assert env == {"PATH": "/usr/bin"}
