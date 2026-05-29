"""System and user prompt builders for the Claude Code orchestration."""
from __future__ import annotations

from typing import TYPE_CHECKING

from zen.models.requests import UserSqlRequest

if TYPE_CHECKING:
    from zen.registry.models import RepoEntry

_SYSTEM_PROMPT = """\
You are the SQL drafting agent for zensql. You draft MariaDB-compatible SQL for
users' data questions — and you keep a friendly, light tone.

First decide what the user wants:

A) A DATA / SQL REQUEST — they want information from the database. Follow the
   "SQL reply format" below.
B) NOT a data request — a greeting, small talk, thanks, a request for a joke, or
   anything off-topic. Do NOT write SQL. Reply with a short, fun, light-hearted
   message (a quick joke or playful quip) in the SAME language as the user,
   wrapped in a fenced ```chat block:
   ```chat
   <your fun reply>
   ```

Hard rules (for data requests):
- Generate SQL TEXT ONLY. The system never executes your SQL.
- Use the `mcp__schema__*` tools to verify tables, columns, and relationships
  BEFORE you write any SQL. Never invent column names.
- Use the `mcp__code-review-graph__*` tools when the user's terms map to
  application code rather than column names.
- Default to a single SELECT statement. Refuse INSERT/UPDATE/DELETE/DDL.
- If the user asks you to "run" or "execute" the SQL, decline and explain
  that the system is text-only.

SQL reply format (exact):
1. A fenced ```sql block containing the final SQL.
2. One short paragraph (≤ 3 sentences) after the block explaining what the
   SQL does, including which tables it reads. Write this paragraph in the SAME
   language as the user's request (e.g. a Vietnamese question gets a Vietnamese
   explanation).

Reply with EITHER a ```chat block (B) or a ```sql block + explanation (A) —
nothing else.
"""

_USER_PROMPT_TEMPLATE = """\
<user_request>
{text}
</user_request>

Context hints:
- preferred_repos: {repos}
- preferred_schemas: {schemas}
{sources}Draft a single SELECT statement that answers the user_request. Verify every
table and column against `mcp__schema__get_table_metadata` before writing.
"""


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT


def format_registered_sources(repos: list[RepoEntry]) -> str:
    """One line per registered Metabase source so the agent uses real
    database_id values instead of guessing (a guess hits DATABASE_NOT_ALLOWED
    and the agent then gives up without SQL). Empty string when nothing is
    registered."""
    lines: list[str] = []
    for repo in repos:
        for block in repo.connection:
            for src in block.sources:
                m = src.metadata
                lines.append(
                    f'- repo "{repo.name}" [{block.environment}]: '
                    f'database_id={m.database_id}, database="{m.database}", '
                    f'schema="{m.schema_}", tables={m.tables}'
                )
    if not lines:
        return ""
    header = (
        "Registered data sources (authoritative — use ONLY these database_id "
        "values; never guess a database_id):"
    )
    return header + "\n" + "\n".join(lines)


def build_user_prompt(req: UserSqlRequest, registered_sources: str = "") -> str:
    sources = f"\n{registered_sources}\n\n" if registered_sources else "\n"
    return _USER_PROMPT_TEMPLATE.format(
        text=req.text,
        repos=req.context_hints.preferred_repos,
        schemas=req.context_hints.preferred_schemas,
        sources=sources,
    )
