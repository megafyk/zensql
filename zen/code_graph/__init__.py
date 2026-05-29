"""Code-review-graph integration.

This module talks to the `code-review-graph` CLI (and via .mcp.json, to its
MCP server). We do not implement our own graph; we adapt zensql's registry to
the upstream package and let it own indexing + semantic search.
"""
from __future__ import annotations

from zen.code_graph.crg_sync import (
    CrgSyncResult,
    sync_build,
    sync_register,
    sync_unregister,
)

__all__ = ["CrgSyncResult", "sync_build", "sync_register", "sync_unregister"]
