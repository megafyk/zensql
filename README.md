# zensql

A Telegram-fronted agent that drafts MariaDB SQL queries from natural language. Backed by Claude Code + two MCP servers (Metabase metadata, code-review-graph context). **Generates SQL text only — never executes it.**

- Full plan: [`docs/PLAN.md`](docs/PLAN.md).
- Implementation log: [`docs/progress.md`](docs/progress.md).

## Requirements

- Python ≥ 3.12 (3.13 OK)
- [`uv`](https://docs.astral.sh/uv/) ≥ 0.11
- A Metabase instance with a service-account user/password (read-only privileges to your application's `information_schema` is sufficient)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Claude Code CLI (`claude`) on `$PATH` — used by the orchestrator as a subprocess

## Install

```bash
uv sync
```

That creates `.venv/`, installs runtime + dev dependencies (including `code-review-graph`), and registers the project as editable.

## Configure

```bash
cp .env.example .env
$EDITOR .env
```

Required keys:

| Key | Why |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token. |
| `AGENT_API_TOKEN` | Shared secret between bot and SQL agent server. Any string ≥ 16 chars. |
| `METABASE_BASE_URL` | e.g. `https://metabase.internal.example.com` |
| `METABASE_USERNAME`, `METABASE_PASSWORD` | Read-only service account. |
| `CODE_GRAPH_ALLOWED_ROOTS` | Comma-separated absolute directory prefixes — `sql_add_repo` will refuse paths outside these. |

Optional:

- `AGENT_API_HOST` / `AGENT_API_PORT` — server bind. Default `0.0.0.0:8080`.
- `AGENT_API_BASE_URL` — what the Telegram bot uses to call the server. Default `http://127.0.0.1:8080`.
- `AGENT_TIMEOUT_S` — how long the orchestrator waits for a Claude Code subprocess. Default 60s.
- `ALLOWED_STATEMENT_FAMILIES` — default `SELECT`. Add `EXPLAIN`/`SHOW` only if you know why.
- `REGISTRY_PATH` — where `registry.json` lives. Default `.claude/skills/sql_add_repo/registry.json`.
- `AUDIT_LOG_DIR` — JSONL audit destination. Default `var/audit`.

## Register your first repo

The Schema MCP refuses **all** Metabase queries until at least one repo is registered (that's how it knows which `database_id`s are legal). Use the CLI:

```bash
cat <<'EOF' | uv run python -m zen.registry.cli register
{
  "name": "orders-service",
  "description": "Orders management service",
  "path": "/srv/repos/orders-service",
  "tags": ["orders"],
  "connection": [{
    "environment": "production",
    "sources": [{
      "name": "metabase",
      "metadata": {
        "database": "prod_orders",
        "database_id": 312,
        "database_type": "mariadb",
        "schema": "cdcn_log_central",
        "tables": ["orders", "customers"]
      }
    }]
  }]
}
EOF
```

In a Claude Code session against this project, prefer the **`sql_add_repo` skill** — it walks you through the same payload interactively and surfaces errors back as plain language.

`register` also syncs to `~/.code-review-graph/registry.json` and triggers a graph build for that path. Use `--no-graph-build` to skip the (potentially long) build:

```bash
uv run python -m zen.registry.cli register --no-graph-build < entry.json
```

CLI surface: `register`, `list`, `get <name>`, `update <name>` (patch JSON on stdin), `delete <name>`.

## Run the processes

Four processes total. In separate terminals (or under `tmux` / a process manager):

```bash
# 1. SQL agent server (HTTP)
uv run python scripts/run_server.py
# alternative: uv run uvicorn zen.sql_agent_server.app:app --host 0.0.0.0 --port 8080

# 2. Telegram bot (long-poll)
uv run python scripts/run_bot.py

# 3. Schema MCP server (stdio) — normally launched by Claude Code via .mcp.json,
#    only run manually for debugging
uv run python scripts/run_schema_mcp.py

# 4. code-review-graph MCP server (stdio) — same: normally launched by Claude Code
uv run code-review-graph serve
```

`.mcp.json` configures Claude Code to launch (3) and (4) on demand — you typically don't run them manually.

Smoke test the server:

```bash
curl -s http://127.0.0.1:8080/health
# {"status":"ok"}

curl -s -H "Authorization: Bearer $AGENT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"00000000-0000-0000-0000-000000000001","source":"telegram","user_id":"tg:1","text":"new orders this week"}' \
  http://127.0.0.1:8080/v1/sql/generate
```

## Run the tests

```bash
uv run pytest -q              # full suite (235 tests as of Chunk 13)
uv run pytest tests/unit -q   # unit only
uv run pytest -q -k validator # one module
```

Tests never reach a real Metabase, real Telegram, or real Claude Code CLI — `respx`, `FakeAgentClient`, `FakeAgentRunner`, `FakeMetabaseClient`, and `tmp_path`-backed `RegistryStore` keep everything in-process.

## Lint + type check

```bash
uv run ruff check zen tests
uv run mypy zen
```

## Fixtures + sample data

- `docs/metabase_*` — reference SQL + curl examples for the Metabase shape this project targets.
- `tests/integration/test_schema_mcp.py` — `_registry_with_orders` builds a one-repo registry with the `orders`/`customers` schema used across most tests.
- `tests/unit/test_validator.py::_orders_metadata` — `TableMetadata` fixture for identifier-verification tests.

## Troubleshooting

**`Metabase /api/session rejected credentials (401)`**
`METABASE_USERNAME` or `METABASE_PASSWORD` is wrong. The Metabase user does **not** need write privileges; read-only access to `information_schema` is enough.

**`no registered repo declares any metabase database — register one with sql_add_repo`**
The Schema MCP refuses to query Metabase until at least one `RepoEntry` exists with a `metabase` source. Use the registry CLI (above) or the `sql_add_repo` skill.

**`code-review-graph CLI not found on PATH`**
`uv add code-review-graph` (already in `pyproject.toml`; `uv sync` should install it). Verify with `uv run code-review-graph --help`.

**`Bash` tools blocked when running Claude Code on this project**
Project `.claude/settings.local.json` is an allowlist. Add the tool names you need (`Write`, `Edit`, `Bash(uv run *)`, etc.) or use `/permissions` interactively.

**Telegram bot replies with "Upstream error: ConnectError"**
The bot can't reach the SQL agent server. Check `AGENT_API_BASE_URL` (default `http://127.0.0.1:8080`) and that the server is running.

**Generated SQL is `error_code: UNSAFE_INTENT`**
The intent guard caught a pattern in the user's message (e.g. `delete from`, `run this sql`, `ignore previous instructions`). That's working as intended — there's no override.

**Generated SQL is `error_code: VALIDATION_FAILED`**
The agent produced SQL the validator refused (denied family, multi-statement, unknown identifier in strict mode). Check the `warnings` array in the response for which rule fired.

## Project layout

```
zensql/
├── .claude/                    # Claude Code config + skills
│   ├── skills/{sql_get_table,sql_add_repo,sql_find_business_context}/
│   ├── settings.json
│   └── settings.local.json
├── zen/                        # Python package
│   ├── sql_agent_server/       # FastAPI app, orchestrator, validator, audit
│   ├── schema_mcp/             # Schema MCP server + Metabase client + normalizer
│   ├── code_graph/             # code-review-graph CLI sync
│   ├── registry/               # Repo registry models + atomic store + CLI
│   ├── telegram_bot/           # aiogram bot + handler + AgentClient
│   ├── models/                 # Pydantic models (requests, responses, metadata, audit, safety)
│   ├── config/                 # pydantic-settings
│   └── mcp_tools/              # shared MCP utilities + typed errors
├── tests/{unit,integration,e2e}/
├── scripts/                    # run_server.py, run_bot.py, run_schema_mcp.py
├── docs/                       # PLAN.md, progress.md, Metabase reference
├── .mcp.json                   # Claude Code MCP server registry
├── .env.example
└── pyproject.toml
```
