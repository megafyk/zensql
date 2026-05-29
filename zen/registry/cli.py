"""CLI wrapper around the repo registry.

Invoked by the `sql_add_repo` Claude skill. Reads JSON from stdin for
mutations, prints structured JSON to stdout, surfaces errors as JSON on
stderr. Exit code is 0 on success, non-zero on failure.

Subcommands:
  register              Read full entry JSON on stdin, validate, add, sync to CRG.
  list                  Print all entries as JSON.
  get <name>            Print one entry as JSON.
  update <name>         Read patch JSON on stdin, merge, save.
  delete <name>         Remove entry, unregister from CRG.

Flags:
  --no-graph-sync       Skip the code-review-graph register/unregister/build call.
  --no-graph-build      Skip just the build step (register still syncs).
  --registry-path PATH  Override registry path (default: Settings.registry_path).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import IO, Any

from pydantic import ValidationError

from zen.code_graph import crg_sync
from zen.config.settings import get_settings
from zen.registry.models import RepoEntry
from zen.registry.store import (
    DuplicateRepoError,
    RegistryStore,
    RepoNotFoundError,
)


def _resolve_path(arg_path: str | None) -> Path:
    if arg_path:
        return Path(arg_path).expanduser()
    return Path(get_settings().registry_path).expanduser()


def _emit(stdout: IO[str], payload: dict[str, Any]) -> None:
    json.dump(payload, stdout, indent=2, sort_keys=False)
    stdout.write("\n")


def _fail(stderr: IO[str], code: str, message: str, **extra: Any) -> int:
    payload = {"error": code, "message": message, **extra}
    json.dump(payload, stderr, indent=2, sort_keys=False)
    stderr.write("\n")
    return 1


def cmd_register(args: argparse.Namespace, stdin: IO[str], stdout: IO[str], stderr: IO[str]) -> int:
    try:
        raw = json.load(stdin)
    except json.JSONDecodeError as e:
        return _fail(stderr, "invalid_json", f"stdin is not valid JSON: {e}")

    try:
        entry = RepoEntry.model_validate(raw)
    except ValidationError as e:
        return _fail(
            stderr,
            "schema_error",
            "entry did not match RepoEntry schema",
            details=e.errors(),
        )

    store = RegistryStore(_resolve_path(args.registry_path))
    try:
        store.register(entry)
    except DuplicateRepoError as e:
        return _fail(stderr, "duplicate", str(e))

    crg_register = crg_sync.sync_register(entry.name, entry.path, skip=args.no_graph_sync)
    crg_build = crg_sync.sync_build(entry.path, skip=args.no_graph_sync or args.no_graph_build)

    _emit(
        stdout,
        {
            "status": "registered",
            "name": entry.name,
            "crg_register": crg_register,
            "crg_build": crg_build,
        },
    )
    return 0


def cmd_list(args: argparse.Namespace, _stdin: IO[str], stdout: IO[str], _stderr: IO[str]) -> int:
    store = RegistryStore(_resolve_path(args.registry_path))
    entries = [r.model_dump(by_alias=True, mode="json") for r in store.list_repos()]
    _emit(stdout, {"repos": entries})
    return 0


def cmd_get(args: argparse.Namespace, _stdin: IO[str], stdout: IO[str], stderr: IO[str]) -> int:
    store = RegistryStore(_resolve_path(args.registry_path))
    try:
        entry = store.get(args.name)
    except RepoNotFoundError as e:
        return _fail(stderr, "not_found", str(e))
    _emit(stdout, entry.model_dump(by_alias=True, mode="json"))
    return 0


def cmd_update(args: argparse.Namespace, stdin: IO[str], stdout: IO[str], stderr: IO[str]) -> int:
    try:
        patch = json.load(stdin)
    except json.JSONDecodeError as e:
        return _fail(stderr, "invalid_json", f"stdin is not valid JSON: {e}")
    if not isinstance(patch, dict):
        return _fail(stderr, "invalid_patch", "patch must be a JSON object")

    store = RegistryStore(_resolve_path(args.registry_path))
    try:
        updated = store.update(args.name, patch)
    except RepoNotFoundError as e:
        return _fail(stderr, "not_found", str(e))
    except DuplicateRepoError as e:
        return _fail(stderr, "duplicate", str(e))
    except ValidationError as e:
        return _fail(stderr, "schema_error", "patched entry failed validation", details=e.errors())

    _emit(stdout, {"status": "updated", "entry": updated.model_dump(by_alias=True, mode="json")})
    return 0


def cmd_delete(args: argparse.Namespace, _stdin: IO[str], stdout: IO[str], stderr: IO[str]) -> int:
    store = RegistryStore(_resolve_path(args.registry_path))
    try:
        removed = store.delete(args.name)
    except RepoNotFoundError as e:
        return _fail(stderr, "not_found", str(e))
    crg_unregister = crg_sync.sync_unregister(removed.name, skip=args.no_graph_sync)
    _emit(
        stdout,
        {
            "status": "deleted",
            "name": removed.name,
            "crg_unregister": crg_unregister,
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zen.registry.cli")
    p.add_argument("--registry-path", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("register", help="Read entry JSON from stdin and register a new repo.")
    pr.add_argument("--no-graph-sync", action="store_true")
    pr.add_argument("--no-graph-build", action="store_true")
    pr.set_defaults(handler=cmd_register)

    pl = sub.add_parser("list", help="List all registered repos.")
    pl.set_defaults(handler=cmd_list)

    pg = sub.add_parser("get", help="Print one entry by name.")
    pg.add_argument("name")
    pg.set_defaults(handler=cmd_get)

    pu = sub.add_parser("update", help="Read patch JSON from stdin and merge into an entry.")
    pu.add_argument("name")
    pu.set_defaults(handler=cmd_update)

    pd = sub.add_parser("delete", help="Remove an entry.")
    pd.add_argument("name")
    pd.add_argument("--no-graph-sync", action="store_true")
    pd.set_defaults(handler=cmd_delete)

    return p


def main(
    argv: list[str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(
        args,
        stdin or sys.stdin,
        stdout or sys.stdout,
        stderr or sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
