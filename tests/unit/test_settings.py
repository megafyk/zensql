from __future__ import annotations

import pytest
from pydantic import ValidationError

from zen.config.settings import Settings, StatementFamily


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_MAX_INPUT_CHARS",
        "AGENT_API_HOST",
        "AGENT_API_PORT",
        "AGENT_API_TOKEN",
        "AGENT_TIMEOUT_S",
        "ALLOWED_STATEMENT_FAMILIES",
        "STRICT_IDENTIFIER_CHECK",
        "METABASE_BASE_URL",
        "METABASE_USERNAME",
        "METABASE_PASSWORD",
        "METABASE_API_KEY",
        "METABASE_ALLOWED_DATABASE_IDS",
        "METABASE_QUERY_TIMEOUT_S",
        "REGISTRY_PATH",
        "CODE_GRAPH_REGISTRY_PATH",
        "CODE_GRAPH_ALLOWED_ROOTS",
        "CODE_GRAPH_SNIPPET_MAX",
        "AUDIT_LOG_DIR",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    s = Settings(_env_file=None)
    assert s.telegram_max_input_chars == 1000
    assert s.agent_api_port == 8080
    assert s.agent_timeout_s == 300
    assert s.allowed_statement_families == [StatementFamily.SELECT]
    assert s.metabase_allowed_database_ids == []
    assert s.code_graph_allowed_roots == []
    assert s.strict_identifier_check is True
    assert s.registry_path.endswith("registry.json")


def test_parses_comma_separated_db_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("METABASE_ALLOWED_DATABASE_IDS", "312, 401, 7")
    s = Settings(_env_file=None)
    assert s.metabase_allowed_database_ids == [312, 401, 7]


def test_parses_statement_families(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("ALLOWED_STATEMENT_FAMILIES", "SELECT")
    s = Settings(_env_file=None)
    assert s.allowed_statement_families == [StatementFamily.SELECT]


def test_rejects_unknown_statement_family(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("ALLOWED_STATEMENT_FAMILIES", "DROP")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_parses_allowed_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("CODE_GRAPH_ALLOWED_ROOTS", "/srv/a,/srv/b")
    s = Settings(_env_file=None)
    assert s.code_graph_allowed_roots == ["/srv/a", "/srv/b"]


def test_port_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("AGENT_API_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_repr_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("AGENT_API_TOKEN", "super-secret-token-value")
    s = Settings(_env_file=None)
    assert "super-secret-token-value" not in repr(s)
    assert s.agent_api_token.get_secret_value() == "super-secret-token-value"
