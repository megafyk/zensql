"""FastAPI application factory for the SQL agent server."""
from __future__ import annotations

from fastapi import FastAPI

from zen.sql_agent_server.routes_generate import router as generate_router
from zen.sql_agent_server.routes_health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="zensql agent server", version="0.1.0")
    app.include_router(health_router)
    app.include_router(generate_router, prefix="/v1")
    return app


app = create_app()
