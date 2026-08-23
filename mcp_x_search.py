"""Compatibility facade over mcp_tools and retrieve.x_search.

New code should import mcp_tools. This module keeps the historical module
path, stdio entrypoint, and test monkeypatch surface.
"""

from __future__ import annotations

import asyncio

import config
import mcp_posts
import mcp_tools
from retrieve import pipeline as mcp_retrieve
from retrieve import x_search
from retrieve.x_search import (
    X_SEARCH_ARGUMENT_KEYS,
    X_SEARCH_INPUT_MAX_CHARS,
    _build_x_search_tool,
    _call_x_search_result,
    _extract_output_text,
    _record_x_search,
    _x_search_payload,
)

__all__ = [
    "DEFAULT_MODEL",
    "X_SEARCH_ARGUMENT_KEYS",
    "X_SEARCH_INPUT_MAX_CHARS",
    "X_SEARCH_TOOL_NAME",
    "_build_x_search_tool",
    "_call_x_search_result",
    "_extract_output_text",
    "_record_x_search",
    "_x_search_payload",
    "call_tool",
    "config",
    "mcp_posts",
    "mcp_retrieve",
    "metrics_lines",
    "tool_definitions",
]

X_SEARCH_TOOL_NAME = mcp_tools.X_SEARCH_TOOL_NAME
POSTS_TOOL_NAME = mcp_tools.POSTS_TOOL_NAME
LATEST_POSTS_TOOL_NAME = mcp_tools.LATEST_POSTS_TOOL_NAME
RETRIEVE_TOOL_NAME = mcp_tools.RETRIEVE_TOOL_NAME
TOOL_NAME = x_search.TOOL_NAME
SERVER_VERSION = mcp_tools.SERVER_VERSION
DEFAULT_MODEL = mcp_tools.DEFAULT_MODEL
TOOL_NAMES = mcp_tools.TOOL_NAMES
REMOVED_TOOL_NAMES = mcp_tools.REMOVED_TOOL_NAMES

tool_enabled = mcp_tools.tool_enabled
_tool_enabled = mcp_tools.tool_enabled
tool_definitions = mcp_tools.tool_definitions
_tool_definitions = mcp_tools.tool_definitions
tool_removed = mcp_tools.tool_removed
tool_error_result = mcp_tools.tool_error_result
metrics_lines = mcp_tools.metrics_lines
call_tool = mcp_tools.call_tool
_handle = mcp_tools._handle

_compile_time_range = mcp_posts.compile_time_range
_build_posts_search_arguments = mcp_posts.build_posts_search_arguments
_build_latest_posts_search_arguments = mcp_posts.build_latest_posts_search_arguments


def __getattr__(name: str) -> object:
    if name in {"_x_search_total_count", "_x_search_total_duration", "_x_search_active", "_x_search_counts"}:
        return getattr(x_search, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def _main() -> None:
    import mcp_server

    await mcp_server.stdio_main()


if __name__ == "__main__":
    asyncio.run(_main())
