---
name: sql_get_table
description: |
  Retrieve normalized MariaDB table metadata (columns, indexes, partitions,
  foreign-key relationships) from the Schema MCP. Use this skill BEFORE
  drafting any SELECT so column and table names are confirmed against real
  metadata rather than guessed. Triggered when the user asks for SQL,
  references an unknown table, or invokes /sql_get_table.
allowed-tools:
  - mcp__schema__get_table_metadata
  - mcp__schema__search_tables
  - mcp__schema__get_relationships
---

# sql_get_table

zensql's first line of context for any SQL-generation task. The Schema MCP
talks to Metabase read-only (`/api/dataset` against `information_schema.*`)
and never executes user-generated SQL.

## When to use this skill

- The user asks for a SELECT and you don't have the exact column list memorized.
- A table name appears in the request but you can't confirm it's real or which
  schema it lives in.
- You're joining two tables and need to confirm the FK column on both sides.

## Workflow

### 1. Resolve the database

zensql's metadata path is gated by the **repo registry**. Pick a `database_id`
that belongs to a registered repo. If you don't know which database the user's
question is about:

- Ask the user which service/repo this is for (use `AskUserQuestion`).
- Or list registered repos with `uv run python -m zen.registry.cli list` and
  show them the choices.

### 2. Discover tables (if the user's hint is vague)

If the user said "orders" but you don't know the exact table name:

```
mcp__schema__search_tables(database_id=312, query="order", max_results=10)
```

`query` is matched as a `LIKE '%query%'` substring on `information_schema.tables.table_name`.

### 3. Pull metadata

For each table you'll touch in the query:

```
mcp__schema__get_table_metadata(
  database_id=312,
  schema_names=["cdcn_log_central"],
  table_names=["orders"],
  include_columns=true,
  include_indexes=true,
  include_relationships=true   # if the SELECT joins
)
```

Only set `include_*=true` for what you actually need — keeps the response small.

### 4. Resolve joins explicitly

If you're joining multiple tables and the metadata didn't give you FK info,
call:

```
mcp__schema__get_relationships(
  database_id=312,
  schema_names=["cdcn_log_central"],
  table_names=["orders", "customers"]
)
```

## Output contract

Every response includes a `warning` field:
`"Read-only metadata access; SQL is not executed by this system."`

Surface that to the user verbatim, or paraphrase as
**"AI-generated SQL — review before executing."** in your reply.

## Failure modes

- `DATABASE_NOT_ALLOWED` — the database id isn't in any registered repo's
  metabase sources. Tell the user to register a repo first via the
  `sql_add_repo` skill.
- `MetabaseAuthFailedError` — Metabase auth failed. Stop; tell the user to
  check `METABASE_USERNAME` / `METABASE_PASSWORD`, or `METABASE_API_KEY` if the
  deployment uses API-key auth.
- `METABASE_QUERY_FAILED` — Metabase accepted the request but the metadata
  query itself failed (SQL/permission error, warehouse outage). This is **not**
  "table doesn't exist" — surface the error and retry/escalate; don't fabricate.
- `ValueError: schema_names is required` — you omitted `schema_names` and the
  registered source declares no schema. Pass `schema_names` explicitly (or have
  the user add a `schema` to the registry entry).
- Empty `tables` list in response — the requested tables don't exist in the
  queried schema. Confirm with the user; don't fabricate columns.

## Hard rules

- Never invent column names. If `get_table_metadata` didn't return a column,
  it doesn't exist (or you queried the wrong schema).
- Never call `mcp__schema__*` with a `database_id` you haven't first verified
  via the registry.
- The system **cannot** execute SQL. If the user asks "run this query", tell
  them it's text-only and they need to copy it into their SQL client.
