"""Typed errors raised by MCP tools.

The `code` attribute is what gets surfaced through the MCP boundary so the
calling agent can react programmatically instead of pattern-matching on
free-form text.
"""
from __future__ import annotations


class McpToolError(Exception):
    code: str = "TOOL_ERROR"

    def __init__(self, message: str = "") -> None:
        super().__init__(f"{self.code}: {message}" if message else self.code)
        self.message = message


class DatabaseNotAllowedError(McpToolError):
    code = "DATABASE_NOT_ALLOWED"


class MetabaseAuthFailedError(McpToolError):
    code = "METABASE_AUTH_FAILED"


class TableNotFoundError(McpToolError):
    code = "TABLE_NOT_FOUND"


class UpstreamTimeoutError(McpToolError):
    code = "UPSTREAM_TIMEOUT"


class WriteAttemptError(McpToolError):
    code = "WRITE_NOT_ALLOWED"


class PathNotAllowedError(McpToolError):
    code = "PATH_NOT_ALLOWED"


class GraphBuildFailedError(McpToolError):
    code = "GRAPH_BUILD_FAILED"


class DuplicateRepoError(McpToolError):
    code = "DUPLICATE_REPO"
