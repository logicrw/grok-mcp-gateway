"""Minimal MCP server for xAI X Search through the Grok MCP Gateway."""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Optional

import config
import mcp_posts
import mcp_retrieve
import xai_responses

X_SEARCH_TOOL_NAME = "x_search"
POSTS_TOOL_NAME = mcp_posts.POSTS_TOOL_NAME
LATEST_POSTS_TOOL_NAME = mcp_posts.LATEST_POSTS_TOOL_NAME
RETRIEVE_TOOL_NAME = mcp_retrieve.RETRIEVE_TOOL_NAME
TOOL_NAME = X_SEARCH_TOOL_NAME
SERVER_VERSION = "0.1.0"
DEFAULT_MODEL = (os.getenv("GROK_PROXY_RETRIEVE_MODEL") or os.getenv("GROK_PROXY_MCP_MODEL") or "grok-4.3").strip() or "grok-4.3"
TOOL_NAMES = {RETRIEVE_TOOL_NAME}
REMOVED_TOOL_NAMES = {X_SEARCH_TOOL_NAME, POSTS_TOOL_NAME, LATEST_POSTS_TOOL_NAME}
X_SEARCH_INPUT_MAX_CHARS = 8000
X_SEARCH_ARGUMENT_KEYS = {
    "query",
    "allowed_x_handles",
    "excluded_x_handles",
    "from_date",
    "to_date",
    "enable_image_understanding",
    "enable_video_understanding",
    "model",
    "raw",
}
_x_search_semaphore = asyncio.Semaphore(config.GROK_PROXY_MCP_X_SEARCH_CONCURRENCY)
_x_search_counts: defaultdict[str, int] = defaultdict(int)
_x_search_total_duration: float = 0.0
_x_search_total_count: int = 0
_x_search_active: int = 0

_compile_time_range = mcp_posts.compile_time_range
_build_posts_search_arguments = mcp_posts.build_posts_search_arguments
_build_latest_posts_search_arguments = mcp_posts.build_latest_posts_search_arguments


def tool_enabled(tool_name: str) -> bool:
    return tool_name.lower() in config.GROK_GATEWAY_MCP_TOOL_ALLOWLIST


_tool_enabled = tool_enabled


def tool_definitions() -> list[Dict[str, Any]]:
    definitions = [mcp_retrieve.retrieve_tool_definition(DEFAULT_MODEL)]
    return [definition for definition in definitions if tool_enabled(str(definition["name"]))]


_tool_definitions = tool_definitions


_clean_handle_list = mcp_posts.clean_handle_list
_clean_iso8601_date = mcp_posts.clean_iso8601_date
_validate_date_order = mcp_posts.validate_date_order


def _build_x_search_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
    tool: Dict[str, Any] = {"type": TOOL_NAME}

    allowed_handles = _clean_handle_list(arguments, "allowed_x_handles")
    excluded_handles = _clean_handle_list(arguments, "excluded_x_handles")
    if allowed_handles and excluded_handles:
        raise ValueError("allowed_x_handles and excluded_x_handles cannot be used together")
    if allowed_handles:
        tool["allowed_x_handles"] = allowed_handles
    if excluded_handles:
        tool["excluded_x_handles"] = excluded_handles

    from_date = _clean_iso8601_date(arguments, "from_date")
    to_date = _clean_iso8601_date(arguments, "to_date")
    _validate_date_order(from_date, to_date)
    if from_date:
        tool["from_date"] = from_date
    if to_date:
        tool["to_date"] = to_date

    if arguments.get("enable_image_understanding") is True:
        tool["enable_image_understanding"] = True
    if arguments.get("enable_video_understanding") is True:
        tool["enable_video_understanding"] = True

    return tool


def _extract_output_text(response: Dict[str, Any]) -> str:
    return xai_responses._extract_output_text(response)


def _record_x_search(status: str, duration: float) -> None:
    global _x_search_total_count, _x_search_total_duration
    _x_search_counts[status] += 1
    _x_search_total_count += 1
    _x_search_total_duration += duration


def metrics_lines() -> list[str]:
    lines = [
        "# HELP mcp_x_retrieve_requests_total Total MCP x_retrieve tool calls by status",
        "# TYPE mcp_x_retrieve_requests_total counter",
    ]
    for status in ("success", "error"):
        lines.append(f'mcp_x_retrieve_requests_total{{status="{status}"}} {_x_search_counts[status]}')
    lines.extend(
        [
            "# HELP mcp_x_retrieve_request_duration_seconds_total Total MCP x_retrieve call duration",
            "# TYPE mcp_x_retrieve_request_duration_seconds_total counter",
            f"mcp_x_retrieve_request_duration_seconds_total {_x_search_total_duration}",
            "# HELP mcp_x_retrieve_request_count_total Total MCP x_retrieve call count",
            "# TYPE mcp_x_retrieve_request_count_total counter",
            f"mcp_x_retrieve_request_count_total {_x_search_total_count}",
            "# HELP mcp_x_retrieve_active_requests Active MCP x_retrieve calls",
            "# TYPE mcp_x_retrieve_active_requests gauge",
            f"mcp_x_retrieve_active_requests {_x_search_active}",
            "# HELP mcp_x_retrieve_concurrency_limit Configured MCP x_retrieve concurrency limit",
            "# TYPE mcp_x_retrieve_concurrency_limit gauge",
            f"mcp_x_retrieve_concurrency_limit {config.GROK_PROXY_MCP_X_SEARCH_CONCURRENCY}",
        ]
    )
    lines.extend(mcp_retrieve.metrics_lines())
    return lines


def _x_search_payload(arguments: Dict[str, Any]) -> Dict[str, Any]:
    unknown = set(arguments) - X_SEARCH_ARGUMENT_KEYS
    if unknown:
        raise ValueError(f"unsupported argument keys: {', '.join(sorted(unknown))}")
    query_value = arguments.get("query")
    if not isinstance(query_value, str):
        raise ValueError("query must be a string")
    query = query_value.strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > X_SEARCH_INPUT_MAX_CHARS:
        raise ValueError(f"query must be at most {X_SEARCH_INPUT_MAX_CHARS} characters")

    model = str(arguments.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return {
        "model": model,
        "input": query,
        "tools": [_build_x_search_tool(arguments)],
        "temperature": 0,
    }


async def _call_x_search_result(arguments: Dict[str, Any]) -> xai_responses.ResponsesResult:
    async with _x_search_semaphore:
        return await xai_responses.post(_x_search_payload(arguments))


def tool_removed(tool_name: str) -> bool:
    return tool_name in REMOVED_TOOL_NAMES


async def call_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    start = time.monotonic()
    global _x_search_active
    _x_search_active += 1
    try:
        if tool_name != RETRIEVE_TOOL_NAME:
            raise ValueError(f"tool removed in vNext: {tool_name}. Use x_retrieve.")
        result = await mcp_retrieve.call_retrieve(arguments, search=_call_x_search_result)
        _record_x_search("success", time.monotonic() - start)
        return result
    except Exception:
        _record_x_search("error", time.monotonic() - start)
        raise
    finally:
        _x_search_active -= 1


async def _handle(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import mcp_server

    return await mcp_server.handle(request)


async def _main() -> None:
    import mcp_server

    await mcp_server.stdio_main()


if __name__ == "__main__":
    asyncio.run(_main())
