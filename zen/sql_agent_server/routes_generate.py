"""POST /v1/sql/generate — runs the orchestrator and returns SQL text only.

The orchestrator (see `orchestrator.py`) spawns Claude Code via an injected
`AgentRunnerProtocol`, applies pre-guard + post-parse, and never executes SQL.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from zen.models.requests import UserSqlRequest
from zen.models.responses import GeneratedSqlResponse
from zen.sql_agent_server.deps import get_orchestrator_dep, verify_bearer
from zen.sql_agent_server.orchestrator import Orchestrator

router = APIRouter(tags=["generate"])


@router.post(
    "/sql/generate",
    response_model=GeneratedSqlResponse,
    dependencies=[Depends(verify_bearer)],
)
async def generate_sql(
    req: UserSqlRequest,
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator_dep)],
) -> GeneratedSqlResponse:
    return await orchestrator.run(req)
