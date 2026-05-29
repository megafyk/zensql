"""Entry point for the SQL agent server.

Run with: `uv run python scripts/run_server.py`
"""
from __future__ import annotations

import uvicorn

from zen.config.settings import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "zen.sql_agent_server.app:app",
        host=s.agent_api_host,
        port=s.agent_api_port,
        log_level=s.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
