---
name: sql_add_repo
description: |
  Manage the zensql repository registry — register, list, update, or remove
  service repos that record per-environment Metabase connections. The registry
  is the sole source of truth for which Metabase database IDs the Schema MCP
  may query; without an entry the system refuses all metadata calls. On
  register/delete this skill also syncs the change into the upstream
  `code-review-graph` registry at `~/.code-review-graph/registry.json` so
  semantic search can resolve a repo alias to its graph. Use whenever the user
  wants to "add a repo", "register this service", "remove a repo", "list
  repos", "update a repo", or invokes /sql_add_repo.
allowed-tools:
  - Bash
  - AskUserQuestion
  - Read
  - Write
---

# sql_add_repo

zensql's registry CRUD skill. Mirrors the debb `debug-repo` skill pattern with
zensql-specific scope. Owns `registry.json` next to this SKILL.md.

## Operations

```
1. Register a new service repo
2. List all registered repos
3. Get a single repo by name
4. Update an existing repo
5. Delete a repo
```

Use `AskUserQuestion` to disambiguate if the user's intent isn't obvious from
their message.

## Step 1 — Determine the operation

Default to interactive prompts. If the user says "I'll paste JSON" or supplies
a JSON object up front, accept that path instead — the CLI validates the
schema before writing.

## Step 2 — Collect inputs (interactive)

For **register** / **update**, gather these fields (one at a time via
`AskUserQuestion` or plain prompts):

| Field | Type | Notes |
|---|---|---|
| `name` | string | Lowercase, kebab- or snake_case, unique. Pattern `^[a-z0-9][a-z0-9_-]{0,63}$`. |
| `description` | string | Short purpose summary. |
| `path` | string | **Absolute** filesystem path (`/...`). Verify it exists **and** lives under one of `CODE_GRAPH_ALLOWED_ROOTS` — the CLI refuses paths outside those roots, and refuses every registration when the variable is unset. |
| `tags` | list[string] | At least one. Domain categorization (lowercase kebab-case recommended). |
| `connection` | list | At least one `{environment, sources[]}` block. |

For each `connection[i].sources[j].name = "metabase"` the `metadata` block requires:

| Field | Type | Notes |
|---|---|---|
| `database` | string | Metabase database name as shown in admin UI (human label). |
| `database_id` | integer ≥ 1 | The numeric id sent as `database` in `/api/dataset`. Find at `<metabase>/admin/databases/<id>`. |
| `database_type` | string | One of `mariadb`/`mysql`/`postgres`/`clickhouse`/`oracle`/`mssql`. |
| `schema` | string | Schema name. Qualifies tables in generated SQL. |
| `tables` | list[string] | Allowlist of tables the service may touch. |

Other `name` values (e.g. `quickwit`, `prometheus`) are rejected — zensql
only consumes Metabase data.

## Step 3 — Run the operation

All mutations go through the registry CLI. Pipe a JSON entry on stdin:

```bash
echo '<JSON-ENTRY>' | uv run python -m zen.registry.cli register
echo '<PATCH-JSON>' | uv run python -m zen.registry.cli update <name>
uv run python -m zen.registry.cli list
uv run python -m zen.registry.cli get <name>
uv run python -m zen.registry.cli delete <name>
```

Output is JSON on stdout (success) or stderr (error). Surface the JSON to the
user verbatim — the structure is stable and human-readable.

### Optional flags

- `--no-graph-sync` — skip the `code-review-graph register/unregister/build`
  call. Use when CRG isn't installed or for a dry run.
- `--no-graph-build` — register with CRG but skip the (potentially long) build.
- `--registry-path PATH` — for tests or alternate registries.

## Step 4 — Confirm graph sync

`register` triggers two CRG calls: `register` (cheap) and `build` (can take
minutes). Warn the user before running `register` on a large repo — they may
prefer `--no-graph-build` and a later `code-review-graph build --repo <path>`
out of band.

`delete` triggers `code-review-graph unregister`.

If the CLI is not on PATH, `crg_*` results include `"ran": false,
"skipped_reason": "code-review-graph CLI not found on PATH"`. That's fine for
zensql-only operation; warn the user that semantic search via
`sql_find_business_context` won't work until they `uv add code-review-graph`.

## Hard rules

- Never write `registry.json` directly. Always go through the CLI — it owns
  schema validation, atomic save, and CRG sync.
- Never register a `path` that doesn't exist on disk.
- The `path` must resolve under a `CODE_GRAPH_ALLOWED_ROOTS` entry; the CLI
  returns `path_not_allowed` (exit 1) for anything outside, and for **all**
  paths when `CODE_GRAPH_ALLOWED_ROOTS` is empty. Check this before collecting
  the rest of the entry (saves a roundtrip), and tell the user to set the env
  var if it's unset.
- Reject names that don't match `^[a-z0-9][a-z0-9_-]{0,63}$` before sending to
  the CLI (the CLI will reject them anyway; saves a roundtrip).
- A registered repo's `metabase.database_id` enters the Schema MCP allowlist.
  Be explicit with the user about which databases they're authorizing.
