from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from zen.registry import cli


def _orders_entry_json() -> dict[str, Any]:
    return {
        "name": "orders-service",
        "description": "Orders service",
        "path": "/srv/repos/orders-service",
        "tags": ["orders"],
        "connection": [
            {
                "environment": "production",
                "sources": [
                    {
                        "name": "metabase",
                        "metadata": {
                            "database": "prod",
                            "database_id": 312,
                            "database_type": "mariadb",
                            "schema": "cdcn_log_central",
                            "tables": ["orders"],
                        },
                    }
                ],
            }
        ],
    }


def _run(
    args: list[str],
    *,
    stdin_text: str = "",
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    rc = cli.main(args, io.StringIO(stdin_text), stdout, stderr)
    return rc, stdout.getvalue(), stderr.getvalue()


@pytest.fixture(autouse=True)
def _stub_crg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.crg_sync,
        "sync_register",
        lambda *a, **kw: {"ran": False, "skipped_reason": "test"},
    )
    monkeypatch.setattr(
        cli.crg_sync,
        "sync_build",
        lambda *a, **kw: {"ran": False, "skipped_reason": "test"},
    )
    monkeypatch.setattr(
        cli.crg_sync,
        "sync_unregister",
        lambda *a, **kw: {"ran": False, "skipped_reason": "test"},
    )


def test_register_happy_path(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    body = json.dumps(_orders_entry_json())
    rc, out, err = _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=body,
    )
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["status"] == "registered"
    assert payload["name"] == "orders-service"
    on_disk = json.loads(registry.read_text())
    assert on_disk["repos"][0]["name"] == "orders-service"


def test_register_invokes_crg_sync_with_correct_args(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    body = json.dumps(_orders_entry_json())
    with (
        patch.object(cli.crg_sync, "sync_register", return_value={"ran": True, "ok": True}) as sr,
        patch.object(cli.crg_sync, "sync_build", return_value={"ran": True, "ok": True}) as sb,
    ):
        rc, out, _ = _run(
            ["--registry-path", str(registry), "register"],
            stdin_text=body,
        )
    assert rc == 0
    sr.assert_called_once_with(
        "orders-service", "/srv/repos/orders-service", skip=False
    )
    sb.assert_called_once_with("/srv/repos/orders-service", skip=False)


def test_register_no_graph_sync_skips_both(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    body = json.dumps(_orders_entry_json())
    with (
        patch.object(cli.crg_sync, "sync_register") as sr,
        patch.object(cli.crg_sync, "sync_build") as sb,
    ):
        sr.return_value = {"ran": False, "skipped_reason": "--no-graph-sync"}
        sb.return_value = {"ran": False, "skipped_reason": "--no-graph-sync"}
        rc, _, _ = _run(
            ["--registry-path", str(registry), "register", "--no-graph-sync"],
            stdin_text=body,
        )
    assert rc == 0
    sr.assert_called_once()
    assert sr.call_args.kwargs["skip"] is True
    sb.assert_called_once()
    assert sb.call_args.kwargs["skip"] is True


def test_register_no_graph_build_only_skips_build(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    body = json.dumps(_orders_entry_json())
    with (
        patch.object(cli.crg_sync, "sync_register") as sr,
        patch.object(cli.crg_sync, "sync_build") as sb,
    ):
        sr.return_value = {"ran": True, "ok": True}
        sb.return_value = {"ran": False, "skipped_reason": "--no-graph-build"}
        _run(
            ["--registry-path", str(registry), "register", "--no-graph-build"],
            stdin_text=body,
        )
    assert sr.call_args.kwargs["skip"] is False
    assert sb.call_args.kwargs["skip"] is True


def test_register_invalid_json(tmp_path: Path) -> None:
    rc, _, err = _run(
        ["--registry-path", str(tmp_path / "r.json"), "register"],
        stdin_text="not json",
    )
    assert rc == 1
    assert "invalid_json" in err


def test_register_schema_error(tmp_path: Path) -> None:
    bad = _orders_entry_json()
    bad["name"] = "BAD NAME"  # space + uppercase
    rc, _, err = _run(
        ["--registry-path", str(tmp_path / "r.json"), "register"],
        stdin_text=json.dumps(bad),
    )
    assert rc == 1
    assert "schema_error" in err


def test_register_duplicate(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    body = json.dumps(_orders_entry_json())
    _run(["--registry-path", str(registry), "register"], stdin_text=body)
    rc, _, err = _run(
        ["--registry-path", str(registry), "register"], stdin_text=body
    )
    assert rc == 1
    assert "duplicate" in err


def test_list(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=json.dumps(_orders_entry_json()),
    )
    rc, out, _ = _run(["--registry-path", str(registry), "list"])
    assert rc == 0
    payload = json.loads(out)
    assert [r["name"] for r in payload["repos"]] == ["orders-service"]


def test_get(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=json.dumps(_orders_entry_json()),
    )
    rc, out, _ = _run(["--registry-path", str(registry), "get", "orders-service"])
    assert rc == 0
    payload = json.loads(out)
    assert payload["name"] == "orders-service"


def test_get_missing(tmp_path: Path) -> None:
    rc, _, err = _run(
        ["--registry-path", str(tmp_path / "r.json"), "get", "ghost"]
    )
    assert rc == 1
    assert "not_found" in err


def test_update_merges(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=json.dumps(_orders_entry_json()),
    )
    rc, out, _ = _run(
        ["--registry-path", str(registry), "update", "orders-service"],
        stdin_text=json.dumps({"description": "Updated!"}),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["entry"]["description"] == "Updated!"


def test_update_invalid_patch_type(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=json.dumps(_orders_entry_json()),
    )
    rc, _, err = _run(
        ["--registry-path", str(registry), "update", "orders-service"],
        stdin_text=json.dumps(["array", "not", "object"]),
    )
    assert rc == 1
    assert "invalid_patch" in err


def test_delete(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=json.dumps(_orders_entry_json()),
    )
    rc, out, _ = _run(
        ["--registry-path", str(registry), "delete", "orders-service"]
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["status"] == "deleted"
    assert payload["name"] == "orders-service"
    # list now empty
    _, out2, _ = _run(["--registry-path", str(registry), "list"])
    assert json.loads(out2)["repos"] == []


def test_delete_invokes_crg_unregister(tmp_path: Path) -> None:
    registry = tmp_path / "r.json"
    _run(
        ["--registry-path", str(registry), "register"],
        stdin_text=json.dumps(_orders_entry_json()),
    )
    with patch.object(cli.crg_sync, "sync_unregister") as su:
        su.return_value = {"ran": True, "ok": True}
        _run(["--registry-path", str(registry), "delete", "orders-service"])
    su.assert_called_once_with("orders-service", skip=False)
