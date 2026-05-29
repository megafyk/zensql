"""Entry point for the Schema MCP server (stdio).

Normally Claude Code launches this as a subprocess per `.mcp.json`. Run
manually with: `uv run python scripts/run_schema_mcp.py`.
"""
from __future__ import annotations

from zen.schema_mcp.server import main

if __name__ == "__main__":
    main()
