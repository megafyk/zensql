"""FastAPI dependencies: settings, bearer auth, orchestrator."""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from zen.config.settings import Settings, get_settings
from zen.sql_agent_server.agent_runner import ClaudeCodeRunner
from zen.sql_agent_server.audit import AuditLogger
from zen.sql_agent_server.orchestrator import Orchestrator
from zen.sql_agent_server.session import get_user_locks


def get_settings_dep() -> Settings:
    return get_settings()


def _consteq(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def verify_bearer(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.agent_api_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    presented = authorization.removeprefix("Bearer ").strip()
    if not _consteq(presented, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def get_audit_logger_dep(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuditLogger:
    return AuditLogger(settings.audit_log_dir, actor="agent_server")


def get_orchestrator_dep(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    audit: Annotated[AuditLogger, Depends(get_audit_logger_dep)],
) -> Orchestrator:
    runner = ClaudeCodeRunner(
        bin_path=settings.claude_code_bin,
        project_dir=settings.claude_code_project_dir or ".",
    )
    return Orchestrator(settings, runner, audit=audit, user_locks=get_user_locks())
