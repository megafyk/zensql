from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from zen.code_graph import crg_sync


@pytest.fixture
def fake_cli_present() -> object:
    with patch.object(crg_sync, "_which", return_value="/usr/bin/code-review-graph") as p:
        yield p


@pytest.fixture
def fake_cli_absent() -> object:
    with patch.object(crg_sync, "_which", return_value=None) as p:
        yield p


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_sync_register_invokes_correct_command(fake_cli_present: object) -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="registered\n", stderr=""
    )
    with patch("subprocess.run", return_value=completed) as run:
        result = crg_sync.sync_register("orders-service", "/srv/repos/orders-service")
    assert result["ran"] is True
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["stdout"] == "registered"
    args, kwargs = run.call_args
    assert args[0] == [
        "/usr/bin/code-review-graph",
        "register",
        "/srv/repos/orders-service",
        "--alias",
        "orders-service",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == 30


def test_sync_unregister_invokes_correct_command(fake_cli_present: object) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=completed) as run:
        result = crg_sync.sync_unregister("orders-service")
    assert result["ok"] is True
    assert run.call_args[0][0] == [
        "/usr/bin/code-review-graph",
        "unregister",
        "orders-service",
    ]


def test_sync_build_invokes_correct_command(fake_cli_present: object) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=completed) as run:
        result = crg_sync.sync_build("/srv/repos/orders-service", timeout=42)
    assert result["ok"] is True
    args, kwargs = run.call_args
    assert args[0] == [
        "/usr/bin/code-review-graph",
        "build",
        "--repo",
        "/srv/repos/orders-service",
    ]
    assert kwargs["timeout"] == 42


# ---------------------------------------------------------------------------
# Skip + missing CLI
# ---------------------------------------------------------------------------


def test_sync_register_skipped_when_requested(fake_cli_present: object) -> None:
    with patch("subprocess.run") as run:
        result = crg_sync.sync_register("x", "/x", skip=True)
    assert result["ran"] is False
    assert result["skipped_reason"] == "--no-graph-sync"
    run.assert_not_called()


def test_sync_register_skipped_when_cli_missing(fake_cli_absent: object) -> None:
    with patch("subprocess.run") as run:
        result = crg_sync.sync_register("x", "/x")
    assert result["ran"] is False
    assert "not found on PATH" in result["skipped_reason"]
    run.assert_not_called()


def test_sync_unregister_skipped_when_cli_missing(fake_cli_absent: object) -> None:
    with patch("subprocess.run") as run:
        result = crg_sync.sync_unregister("x")
    assert result["ran"] is False
    assert "not found on PATH" in result["skipped_reason"]
    run.assert_not_called()


def test_sync_build_skipped_when_cli_missing(fake_cli_absent: object) -> None:
    with patch("subprocess.run") as run:
        result = crg_sync.sync_build("/x")
    assert result["ran"] is False
    run.assert_not_called()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_sync_register_surfaces_nonzero_exit(fake_cli_present: object) -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=2, stdout="", stderr="alias already exists\n"
    )
    with patch("subprocess.run", return_value=completed):
        result = crg_sync.sync_register("x", "/x")
    assert result["ran"] is True
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "alias already exists" in result["stderr"]


def test_sync_register_handles_timeout(fake_cli_present: object) -> None:
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30),
    ):
        result = crg_sync.sync_register("x", "/x")
    assert result["ran"] is True
    assert result["ok"] is False
    assert "did not finish" in result["error"]


def test_sync_register_handles_os_error(fake_cli_present: object) -> None:
    with patch("subprocess.run", side_effect=OSError("permission denied")):
        result = crg_sync.sync_register("x", "/x")
    assert result["ran"] is True
    assert result["ok"] is False
    assert "permission denied" in result["error"]


def test_sync_build_uses_default_timeout(fake_cli_present: object) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=completed) as run:
        crg_sync.sync_build("/x")
    assert run.call_args.kwargs["timeout"] == 600
