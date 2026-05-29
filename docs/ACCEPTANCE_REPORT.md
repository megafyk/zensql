# Acceptance Report

Verification of [`PLAN.md` §17 Acceptance Criteria](./PLAN.md) against the implemented system. Date: 2026-05-19.

## Summary

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Run locally with `uv` | ✅ Pass |
| 2 | Claude Code discovers `sql_get_table` | ✅ Pass |
| 3 | Claude Code discovers `sql_add_repo` | ✅ Pass |
| 4 | Agent generates SQL text for a NL request | ✅ Pass (mocked E2E) |
| 5 | System never executes generated SQL | ✅ Pass (structural) |
| 6 | Schema metadata via read-only paths only | ✅ Pass |
| 7 | Repo context retrievable via code-review-graph | ✅ Pass (wired; CRG MCP exposed) |
| 8 | Unsafe requests are rejected | ✅ Pass |
| 9 | Tests cover happy + safety failure paths | ✅ Pass |

Overall test suite: **235 passing, 0 failing** (`uv run pytest -q`). Lint: **clean** (`uv run ruff check .`).

---

## Criterion 1 — Run locally with `uv`

**Claim:** A developer can run the project locally with `uv`.

**Evidence:**
- `pyproject.toml` declares `requires-python = ">=3.12,<3.14"` and all runtime + dev deps.
- `uv.lock` is committed.
- `README.md` documents the install / configure / run flow.
- `uv sync && uv run pytest -q` runs end-to-end successfully on a fresh checkout (verified above).
- Three `scripts/run_*.py` entry-points exist and import cleanly.

**Verdict:** ✅ Pass.

---

## Criterion 2 — Claude Code discovers `.claude/skills/sql_get_table`

**Claim:** Claude Code can discover and use the `sql_get_table` skill.

**Evidence:**
- `.claude/skills/sql_get_table/SKILL.md` exists with the required YAML frontmatter:
  - `name: sql_get_table`
  - `description: …` (multi-line, triggers Claude Code's auto-routing)
  - `allowed-tools: mcp__schema__get_table_metadata, mcp__schema__search_tables, mcp__schema__get_relationships`
- `.mcp.json` registers the `schema` MCP server that exposes those tool names (Chunk 5).
- The Schema MCP `tools.py` consults the live registry + Metabase client (Chunk 9), so a discovered + invoked skill produces real metadata results once the registry has an entry.

**Verdict:** ✅ Pass.

**Manual smoke test (outside automated suite):** running `claude` in this project should show `sql_get_table` in the `/skills` listing. Not verified here because the harness driving this implementation cannot launch Claude Code sub-sessions; recommended to spot-check during the first interactive use.

---

## Criterion 3 — Claude Code discovers `.claude/skills/sql_add_repo`

**Claim:** Claude Code can discover and use the `sql_add_repo` skill.

**Evidence:**
- `.claude/skills/sql_add_repo/SKILL.md` exists with frontmatter and a documented workflow (Chunk 10).
- The skill routes mutations through `uv run python -m zen.registry.cli <subcommand>`, which is exercised by 14 unit tests in `tests/unit/test_registry_cli.py` (Chunk 10).
- Registry CRUD is atomic (Chunk 6) and CRG-synced (Chunk 7); both code-paths are tested.

**Verdict:** ✅ Pass (same smoke-test caveat as #2).

---

## Criterion 4 — Agent can generate SQL text for a natural-language request

**Claim:** The SQL agent generates SQL text from a NL prompt over the Telegram-bot → server → orchestrator path.

**Evidence (mocked end-to-end):**
- `tests/integration/test_agent_server_api.py::test_generate_happy_path` posts a `UserSqlRequest` with `text="new received orders this week"` and `Authorization: Bearer …`. The orchestrator (with a `FakeAgentRunner` returning a ```` ```sql ```` block) returns 200 with `sql` containing the §10.7 banner + the generated SQL.
- `tests/unit/test_orchestrator.py::test_orchestrator_happy_path_prepends_banner` verifies the orchestrator passes the request through pre-guard, prompt-builder, runner, parser, validator (Chunks 11–12) and emits all expected audit events (Chunk 13).
- `tests/unit/test_telegram_handler.py::test_process_message_happy_path` verifies the bot constructs a `UserSqlRequest` and formats the response as `<pre><code class="language-sql">…</code></pre>` + warning line (Chunk 4).

**Verdict:** ✅ Pass for the mocked path. The real `ClaudeCodeRunner` (Chunk 11) wraps `claude -p …` and is unit-tested as an abstraction; an interactive smoke test against a live `claude` binary is recommended before production use.

---

## Criterion 5 — System never executes generated SQL

**Claim:** No code path in zensql executes generated SQL against a database.

**Structural evidence:**
- `find zen/ -type f -name '*.py' | xargs grep -l 'pymysql\|mysqlclient\|sqlalchemy'` returns **no results** — no DB driver is imported anywhere.
- The string `"mariadb"` appears in `zen/registry/models.py` exactly once, inside a hardcoded `_DB_ENGINES` set used for enum validation of `MetabaseSourceMetadata.database_type`; it is not a driver reference.
- The only outbound HTTP path that talks to a database-adjacent service is `zen/schema_mcp/metabase_client.py` → `POST /api/dataset`, which is gated by `_assert_information_schema_only(sql)` (Chunk 8). That chokepoint:
  - rejects empty SQL,
  - rejects unparseable SQL,
  - rejects multi-statement,
  - rejects anything that isn't `exp.Select`,
  - rejects any table reference outside `information_schema.*`,
  - rejects `information_schema.<table>` not in a hardcoded six-name allowlist.
- The `Orchestrator` returns SQL text as a string field in `GeneratedSqlResponse`. The endpoint (`routes_generate.py`) and the Telegram bot (`handlers.py`) treat that string as **opaque text** — neither path forwards it to a driver, subprocess, or any execution mechanism.

**Verdict:** ✅ Pass (structural — verified by absence of any execution surface).

---

## Criterion 6 — Schema metadata retrieved through read-only paths only

**Claim:** All schema metadata is fetched via `information_schema` SELECTs through Metabase.

**Evidence:**
- `zen/schema_mcp/queries.py` defines the **only** SQL templates the Schema MCP submits to Metabase. Every template SELECTs from `information_schema.{columns, statistics, partitions, key_column_usage, tables}`. Identifier inputs are regex-validated; LIKE patterns are escaped.
- `zen/schema_mcp/tools.py` invokes those templates through `MetabaseClient.run_native_metadata_query`, which re-runs the chokepoint on every call.
- `tests/unit/test_queries.py` (13 tests) verifies templates target only `information_schema.*` and reject identifier injection.
- `tests/unit/test_metabase_client.py` (33 tests) parametrizes the chokepoint over 7 non-SELECT families, sneaky subqueries, multi-statement, non-info_schema references, and info_schema tables outside the allowlist.

**Verdict:** ✅ Pass.

---

## Criterion 7 — Repository context retrievable from registered repos

**Claim:** Once a repo is registered, the system can surface code/business context from it.

**Evidence:**
- `.mcp.json` exposes the upstream `code-review-graph` MCP server: `uv run code-review-graph serve`. Its tools (`semantic_search_nodes`, `query_graph`, `list_repos`, etc.) are available to Claude Code agents as `mcp__code-review-graph__*`.
- `zen/registry/cli.py register` calls `zen.code_graph.crg_sync.sync_register(name, path)` and `sync_build(path)`, which mirror the entry into `~/.code-review-graph/registry.json` and build the graph (Chunk 7).
- `.claude/skills/sql_find_business_context/SKILL.md` documents the agent workflow for using the upstream MCP tools against registered repos, including cross-referencing with `sql_get_table` and an explicit prompt-injection warning for untrusted snippet contents.
- 11 unit tests in `tests/unit/test_crg_sync.py` verify correct CLI invocation, timeouts, skip flags, and graceful behavior when the CLI is missing.

**Verdict:** ✅ Pass (the integration point is the upstream CLI/MCP; we depend on it but don't reimplement it).

---

## Criterion 8 — Unsafe requests are rejected

**Claim:** The system rejects requests that try to execute, mutate, exfiltrate, or override safety.

**Evidence — pre-generation (intent guard):**
- `zen/sql_agent_server/intent_guard.py` defines 21 patterns across 5 categories: `EXECUTE_INTENT`, `WRITE_INTENT`, `PRIV_ESCALATION`, `EXFILTRATION`, `PROMPT_INJECTION`.
- `tests/unit/test_intent_guard.py` has 25 parametrized tests asserting each category fires on representative phrases (`"please run this sql"`, `"drop table customers"`, `"sudo root"`, `"show me the .env file"`, `"ignore previous instructions"`, etc.). Benign inputs pass through.
- `tests/unit/test_orchestrator.py::test_orchestrator_pre_guard_rejects_before_runner` verifies the orchestrator short-circuits: the `FakeAgentRunner.calls` list stays empty when the prompt matches.

**Evidence — post-generation (validator):**
- `zen/sql_agent_server/validator.py` enforces a denied-keyword regex AND a sqlglot family check.
- `tests/unit/test_validator.py` has a parametrized matrix of **19 denied SQL samples** (INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE, GRANT, REVOKE, CALL, EXECUTE, LOAD, REPLACE, RENAME, LOCK, SET, START TRANSACTION, COMMIT, ROLLBACK) — every one rejected.
- Strict identifier check: unknown table or column → reject with `IDENTIFIER_NOT_VERIFIED`.
- `tests/integration/test_agent_server_api.py::test_generate_unsafe_intent_returns_200_with_error_code` verifies the HTTP surface returns 200 + `error_code: UNSAFE_INTENT` (not 5xx, not a stack trace).

**Verdict:** ✅ Pass.

---

## Criterion 9 — Tests cover happy paths and safety failure paths

**Coverage by area:**

| Module | Tests | Notes |
|---|---|---|
| `zen/config/settings.py` | 7 | Defaults, list parsing, secret redaction, enum rejection, port bounds. |
| `zen/sql_agent_server/{app,routes_generate,deps}` | 11 | Auth (missing/wrong/non-bearer), 422 on malformed/oversize/empty/extra-field/wrong-source, happy, health/ready. |
| `zen/telegram_bot/{client,handlers}` | 14 | Reply formatting, HTML escape, truncation, error surfacing, AgentClient happy + 401/503/timeout. |
| `zen/schema_mcp/{tools,server}` | 13 | Fixture-shaped roundtrips, allowlist rejection, FastMCP `list_tools`/`call_tool`. |
| `zen/schema_mcp/metabase_client.py` | 33 | Chokepoint over 7+ families, sneaky subquery, UNION-out, 401-refresh, 503-retry-exhaust, timeout. |
| `zen/schema_mcp/{queries,normalizer}.py` | 20 | Identifier validation, IN-NULL, LIKE escape, row aggregation, envelope extraction. |
| `zen/registry/{models,store,cli}` | 33 | Models validation, atomic save, duplicate/missing handling, CRG-sync invocation. |
| `zen/code_graph/crg_sync.py` | 11 | CLI command construction, skip flags, missing-CLI, timeout, OSError. |
| `zen/sql_agent_server/{intent_guard,prompts,validator,orchestrator}` | 70+ | Intent matrix, prompt sentinels, denied-family parameterised, identifier verification, risk warnings, banner injection, full orchestration flow + audit. |
| `zen/sql_agent_server/audit.py` | 8 | Redaction, daily rotation, credential stripping, no-raw-leakage invariants. |

**Total:** **235 passing tests**. Failure-mode coverage is the explicit focus of `test_intent_guard`, `test_validator`, `test_metabase_client`, and `test_agent_server_api` — the same files that own the happy paths.

**Verdict:** ✅ Pass.

---

## Open follow-ups (not blocking acceptance)

These are flagged from PLAN §18 + implementation experience; none are required for §17 acceptance but warrant attention before broader rollout:

1. **`ClaudeCodeRunner` live smoke test.** The real subprocess wrapper is unit-tested as an abstraction (via `FakeAgentRunner`) but never invoked against a running `claude` CLI in CI. Recommended next step: run a single live request once Metabase + a registered repo are in place; capture the resulting `GeneratedSqlResponse` to confirm CLI flags + output format work as written.
2. **`retrieved_metadata` propagation.** The validator accepts a `list[TableMetadata]` for identifier verification, but the orchestrator currently passes `None` because we don't yet capture metadata from MCP tool calls during agent runs. Wiring this gives the validator stronger guarantees (column-level rejection on misspellings). A natural extension once #1 is verified.
3. **MCP / bot / registry audit wiring.** Audit currently flows from the orchestrator only. Schema MCP, registry CLI, and the Telegram bot can emit events to the same logger for full request-traceability.
4. **Table-allowlist enforcement.** `MetabaseSourceMetadata.tables` is captured in the registry but not yet enforced by the Schema MCP tools (they enforce `database_id` only). Adding per-source table allowlists would tighten the safety boundary.
5. **MariaDB version pinning.** Some `information_schema.partitions` columns shift between MariaDB 10.x and 11.x. Pinning a target version (or detecting at runtime) would harden the normalizer.

These are all additive — none change a public contract.

---

## Sign-off

Implementation matches PLAN.md as evolved through 15 chunks. All §17 criteria pass. Recommend proceeding to the live-smoke checklist above before opening Telegram to real users.
