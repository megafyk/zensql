"""SQL generation orchestrator.

Wires the inbound `UserSqlRequest` through the unsafe-intent pre-guard, into
a Claude Code subprocess (via `AgentRunnerProtocol`), parses the response,
and returns a `GeneratedSqlResponse`. The real safety validator from Chunk 12
will replace the current passthrough that just prepends the banner.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import uuid4

from zen.config.settings import Settings
from zen.models.requests import UserSqlRequest
from zen.models.responses import GeneratedSqlResponse
from zen.registry.store import RegistryError, RegistryStore
from zen.sql_agent_server.agent_runner import AgentRunnerProtocol
from zen.sql_agent_server.audit import AuditLogger, NullAuditLogger, redact_sql, redact_text
from zen.sql_agent_server.intent_guard import reject_if_unsafe_intent
from zen.sql_agent_server.prompts import (
    build_system_prompt,
    build_user_prompt,
    format_registered_sources,
)
from zen.sql_agent_server.session import UserLocks, session_id_for
from zen.sql_agent_server.validator import SqlSafetyValidator

if TYPE_CHECKING:
    from zen.registry.models import RepoEntry

_DEFAULT_ALLOWED_TOOLS = [
    "mcp__schema__get_table_metadata",
    "mcp__schema__search_tables",
    "mcp__schema__get_relationships",
    "mcp__code-review-graph__semantic_search_nodes_tool",
    "mcp__code-review-graph__list_repos_tool",
    "mcp__code-review-graph__query_graph_tool",
]

_SQL_BLOCK_RE = re.compile(r"```sql\s*\n(.+?)\n```", re.DOTALL | re.IGNORECASE)
_CHAT_BLOCK_RE = re.compile(r"```chat\s*\n(.+?)\n```", re.DOTALL | re.IGNORECASE)


def parse_agent_output(output: str) -> tuple[str, str]:
    """Extract the first fenced ```sql block plus any post-block explanation."""
    match = _SQL_BLOCK_RE.search(output)
    if not match:
        return "", ""
    sql = match.group(1).strip()
    explanation = output[match.end():].strip()
    return sql, explanation


def parse_chat_reply(output: str) -> str:
    """Extract a fenced ```chat block — a conversational (non-SQL) reply."""
    match = _CHAT_BLOCK_RE.search(output)
    return match.group(1).strip() if match else ""


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        runner: AgentRunnerProtocol,
        *,
        allowed_tools: list[str] | None = None,
        validator: SqlSafetyValidator | None = None,
        audit: AuditLogger | None = None,
        registry: RegistryStore | None = None,
        user_locks: UserLocks | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self._registry = registry or RegistryStore(settings.registry_path)
        # Per-instance by default (test isolation); production injects the
        # process-wide singleton via deps so concurrent requests share locks.
        self._user_locks = user_locks or UserLocks()
        self._allowed_tools = allowed_tools or _DEFAULT_ALLOWED_TOOLS
        self._validator = validator or SqlSafetyValidator(
            allowed_families={f.value for f in settings.allowed_statement_families},
            strict_identifier_check=settings.strict_identifier_check,
        )
        self._audit = audit or NullAuditLogger()

    def _load_repos(self) -> list[RepoEntry]:
        try:
            return self._registry.list_repos()
        except RegistryError:
            return []

    def _registered_sources(self) -> str:
        """Formatted registry sources for the prompt; empty if unavailable."""
        return format_registered_sources(self._load_repos())

    def _metabase_databases_for(self, tables_referenced: list[str]) -> list[str]:
        """Resolve the Metabase `database` name(s) for the SQL's referenced
        schemas, so the bot can point the user at the right database. Distinct,
        order-preserved; empty when no referenced schema maps to a metabase
        source (e.g. INFORMATION_SCHEMA queries)."""
        schema_to_db: dict[str, str] = {}
        for repo in self._load_repos():
            for block in repo.connection:
                for src in block.sources:
                    if src.name == "metabase" and src.metadata.schema_:
                        schema_to_db[src.metadata.schema_] = src.metadata.database
        out: list[str] = []
        for table in tables_referenced:
            schema = table.split(".", 1)[0] if "." in table else None
            db = schema_to_db.get(schema) if schema else None
            if db and db not in out:
                out.append(db)
        return out

    async def run(self, request: UserSqlRequest) -> GeneratedSqlResponse:
        job_id = uuid4()
        self._audit.log(
            "request_received",
            request_id=request.request_id,
            job_id=job_id,
            details={"user_id": request.user_id, "text": redact_text(request.text)},
        )

        violation = reject_if_unsafe_intent(request.text)
        if violation is not None:
            self._audit.log(
                "unsafe_intent_rejected",
                request_id=request.request_id,
                job_id=job_id,
                severity="warn",
                details={"rule": violation.rule, "evidence": violation.evidence},
            )
            return GeneratedSqlResponse(
                request_id=request.request_id,
                job_id=job_id,
                error_code="UNSAFE_INTENT",
                error_message=violation.detail,
                warnings=[],
            )

        session_id = (
            session_id_for(request.user_id) if self._settings.session_enabled else None
        )
        self._audit.log(
            "agent_invocation_started",
            request_id=request.request_id,
            job_id=job_id,
            details={"timeout_s": self._settings.agent_timeout_s, "session_id": session_id},
        )

        async def _run():
            return await self._runner.run(
                system_prompt=build_system_prompt(),
                user_prompt=build_user_prompt(request, self._registered_sources()),
                mcp_config_path=self._settings.mcp_config_path,
                allowed_tools=self._allowed_tools,
                timeout_s=float(self._settings.agent_timeout_s),
                session_id=session_id,
            )

        if session_id is not None:
            # Serialize same-user requests: concurrent --resume corrupts the
            # session transcript.
            async with self._user_locks.get(request.user_id):
                result = await _run()
        else:
            result = await _run()

        if result.timed_out:
            self._audit.log(
                "upstream_error",
                request_id=request.request_id,
                job_id=job_id,
                severity="error",
                details={"reason": "timeout"},
            )
            return GeneratedSqlResponse(
                request_id=request.request_id,
                job_id=job_id,
                error_code="TIMEOUT",
                error_message=f"agent did not respond within {self._settings.agent_timeout_s}s",
                warnings=[],
            )
        if result.exit_code != 0:
            snippet = (result.stderr or result.stdout or "").strip()
            snippet = snippet.splitlines()[-1] if snippet else ""
            snippet = snippet[:200]
            self._audit.log(
                "upstream_error",
                request_id=request.request_id,
                job_id=job_id,
                severity="error",
                details={
                    "reason": "non_zero_exit",
                    "exit_code": result.exit_code,
                    "stderr": (result.stderr or "")[:500],
                    "stdout": (result.stdout or "")[:500],
                },
            )
            msg = f"agent exited with code {result.exit_code}"
            if snippet:
                msg = f"{msg}: {snippet}"
            return GeneratedSqlResponse(
                request_id=request.request_id,
                job_id=job_id,
                error_code="AGENT_FAILED",
                error_message=msg,
                warnings=[],
            )

        self._audit.log(
            "agent_invocation_completed",
            request_id=request.request_id,
            job_id=job_id,
            details={"stdout_chars": len(result.stdout)},
        )

        sql, explanation = parse_agent_output(result.stdout)
        if not sql:
            # Not a data request? The agent replies conversationally — relay it
            # as-is (no SQL, no validation, no Metabase guide), not as an error.
            chat_reply = parse_chat_reply(result.stdout)
            if chat_reply:
                self._audit.log(
                    "chat_reply",
                    request_id=request.request_id,
                    job_id=job_id,
                    details={"chars": len(chat_reply)},
                )
                return GeneratedSqlResponse(
                    request_id=request.request_id,
                    job_id=job_id,
                    chat_reply=chat_reply,
                    warnings=[],
                )
            agent_message = result.stdout.strip()
            self._audit.log(
                "upstream_error",
                request_id=request.request_id,
                job_id=job_id,
                severity="warn",
                details={"reason": "no_sql_block", "stdout": agent_message[:500]},
            )
            # Relay the agent's own words: it usually explains why (a clarifying
            # question, an out-of-schema table, etc.) — far more useful than a
            # generic "no sql block" to the person who asked.
            if agent_message:
                msg = f"the agent responded without SQL:\n\n{agent_message[:1500]}"
            else:
                msg = "agent output did not contain a ```sql block"
            return GeneratedSqlResponse(
                request_id=request.request_id,
                job_id=job_id,
                error_code="NO_SQL_PRODUCED",
                error_message=msg,
                warnings=[],
            )

        report = self._validator.validate(
            sql,
            retrieved_metadata=None,
            request_id=str(request.request_id),
        )
        if not report.ok:
            self._audit.log(
                "safety_violation",
                request_id=request.request_id,
                job_id=job_id,
                severity="warn",
                details={
                    "sql": redact_sql(sql),
                    "violations": [v.model_dump() for v in report.violations],
                },
            )
            return GeneratedSqlResponse(
                request_id=request.request_id,
                job_id=job_id,
                error_code="VALIDATION_FAILED",
                error_message=report.summary,
                warnings=[v.detail for v in report.violations],
            )

        self._audit.log(
            "sql_generated",
            request_id=request.request_id,
            job_id=job_id,
            details={
                "sql": redact_sql(report.sql_with_banner or ""),
                "tables_referenced": report.tables_referenced,
                "warnings": report.warnings,
            },
        )

        return GeneratedSqlResponse(
            request_id=request.request_id,
            job_id=job_id,
            sql=report.sql_with_banner,
            explanation=explanation or None,
            warnings=["AI-generated SQL. Review before executing.", *report.warnings],
            tables_referenced=report.tables_referenced,
            metabase_databases=self._metabase_databases_for(report.tables_referenced),
        )
