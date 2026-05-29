from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from zen.config.settings import Settings
from zen.sql_agent_server.agent_runner import FakeAgentRunner
from zen.sql_agent_server.app import create_app
from zen.sql_agent_server.deps import get_orchestrator_dep, get_settings_dep
from zen.sql_agent_server.orchestrator import Orchestrator

_TEST_TOKEN = "test-token-abc123"
_STUB_OUTPUT = "```sql\nSELECT 1 AS x;\n```\nReturns 1."


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        agent_api_token=SecretStr(_TEST_TOKEN),
        agent_timeout_s=5,
    )


@pytest.fixture
def runner() -> FakeAgentRunner:
    return FakeAgentRunner(stdout=_STUB_OUTPUT)


@pytest.fixture
def client(settings: Settings, runner: FakeAgentRunner) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_settings_dep] = lambda: settings
    app.dependency_overrides[get_orchestrator_dep] = lambda: Orchestrator(settings, runner)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _valid_body() -> dict[str, object]:
    return {
        "request_id": str(uuid4()),
        "source": "telegram",
        "user_id": "tg:42",
        "text": "new received orders this week",
    }


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_generate_happy_path(client: TestClient, runner: FakeAgentRunner) -> None:
    body = _valid_body()
    r = client.post(
        "/v1/sql/generate",
        json=body,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["request_id"] == body["request_id"]
    assert "AI-GENERATED SQL" in data["sql"]
    assert "SELECT 1 AS x;" in data["sql"]
    assert any("Review before executing" in w for w in data["warnings"])
    assert data["error_code"] is None
    assert len(runner.calls) == 1


def test_generate_missing_token(client: TestClient) -> None:
    r = client.post("/v1/sql/generate", json=_valid_body())
    assert r.status_code == 401


def test_generate_wrong_token(client: TestClient) -> None:
    r = client.post(
        "/v1/sql/generate",
        json=_valid_body(),
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_generate_non_bearer_scheme(client: TestClient) -> None:
    r = client.post(
        "/v1/sql/generate",
        json=_valid_body(),
        headers={"Authorization": f"Basic {_TEST_TOKEN}"},
    )
    assert r.status_code == 401


def test_generate_malformed_body(client: TestClient) -> None:
    r = client.post(
        "/v1/sql/generate",
        json={"text": "missing required fields"},
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 422


def test_generate_oversize_text(client: TestClient) -> None:
    body = _valid_body()
    body["text"] = "x" * 9000
    r = client.post(
        "/v1/sql/generate",
        json=body,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 422


def test_generate_extra_field_rejected(client: TestClient) -> None:
    body = _valid_body()
    body["evil"] = "extra"
    r = client.post(
        "/v1/sql/generate",
        json=body,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 422


def test_generate_empty_text_rejected(client: TestClient) -> None:
    body = _valid_body()
    body["text"] = ""
    r = client.post(
        "/v1/sql/generate",
        json=body,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 422


def test_generate_wrong_source_rejected(client: TestClient) -> None:
    body = _valid_body()
    body["source"] = "web"
    r = client.post(
        "/v1/sql/generate",
        json=body,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 422


def test_generate_unsafe_intent_returns_200_with_error_code(
    client: TestClient, runner: FakeAgentRunner
) -> None:
    body = _valid_body()
    body["text"] = "drop table customers"
    r = client.post(
        "/v1/sql/generate",
        json=body,
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["error_code"] == "UNSAFE_INTENT"
    assert data["sql"] is None
    assert runner.calls == []


def test_generate_returns_503_when_agent_token_unset() -> None:
    """Empty AGENT_API_TOKEN must reject `Bearer ` (no payload) — not silently
    authenticate against an empty expected token."""
    empty = Settings(_env_file=None, agent_api_token=SecretStr(""))
    app = create_app()
    app.dependency_overrides[get_settings_dep] = lambda: empty
    app.dependency_overrides[get_orchestrator_dep] = lambda: Orchestrator(
        empty, FakeAgentRunner(stdout=_STUB_OUTPUT)
    )
    try:
        with TestClient(app) as c:
            r1 = c.post(
                "/v1/sql/generate",
                json=_valid_body(),
                headers={"Authorization": "Bearer "},
            )
            assert r1.status_code == 503
            r2 = c.post(
                "/v1/sql/generate",
                json=_valid_body(),
                headers={"Authorization": "Bearer anything"},
            )
            assert r2.status_code == 503
    finally:
        app.dependency_overrides.clear()
