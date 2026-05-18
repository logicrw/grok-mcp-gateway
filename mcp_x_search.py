"""Minimal MCP server for xAI X Search through the Grok MCP Gateway."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Optional

import httpx

import config
import mcp_posts
import token_manager

X_SEARCH_TOOL_NAME = "x_search"
POSTS_TOOL_NAME = mcp_posts.POSTS_TOOL_NAME
LATEST_POSTS_TOOL_NAME = mcp_posts.LATEST_POSTS_TOOL_NAME
TOOL_NAME = X_SEARCH_TOOL_NAME
SERVER_NAME = "grok-mcp-gateway-x-search"
SERVER_VERSION = "0.1.0"
DEFAULT_MODEL = os.getenv("GROK_PROXY_MCP_MODEL", "grok-4.3").strip() or "grok-4.3"
XAI_RESPONSES_URL = f"{token_manager.XAI_API_BASE}/v1/responses"
_x_search_semaphore = asyncio.Semaphore(config.GROK_PROXY_MCP_X_SEARCH_CONCURRENCY)
_x_search_counts: defaultdict[str, int] = defaultdict(int)
_x_search_total_duration: float = 0.0
_x_search_total_count: int = 0
_x_search_active: int = 0

_compile_time_range = mcp_posts.compile_time_range
_build_posts_search_arguments = mcp_posts.build_posts_search_arguments
_build_latest_posts_search_arguments = mcp_posts.build_latest_posts_search_arguments


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_enabled(tool_name: str) -> bool:
    return tool_name.lower() in config.GROK_GATEWAY_MCP_TOOL_ALLOWLIST


def _x_search_tool_definition() -> Dict[str, Any]:
    today = date.today().isoformat()
    return {
        "name": X_SEARCH_TOOL_NAME,
        "description": (
            "Search X posts through xAI's x_search tool using the local Hermes OAuth session. "
            f"Current local date: {today}. For latest, today, this week, or other time-sensitive "
            "requests, pass from_date/to_date explicitly instead of relying only on natural language."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search request. Include handles, topic, time window, and desired output.",
                },
                "allowed_x_handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional handle allowlist, for example ['elonmusk', 'xai'].",
                },
                "excluded_x_handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional handle blocklist. Cannot be used with allowed_x_handles.",
                },
                "from_date": {
                    "type": "string",
                    "description": "Optional ISO8601 search start date, for example '2026-05-18'.",
                },
                "to_date": {
                    "type": "string",
                    "description": (
                        "Optional inclusive ISO8601 search end date, for example '2026-05-18'. "
                        "Date-only values are normalized by the proxy for xAI's current date-bound behavior."
                    ),
                },
                "enable_image_understanding": {"type": "boolean"},
                "enable_video_understanding": {"type": "boolean"},
                "model": {"type": "string", "description": f"Optional xAI model. Defaults to {DEFAULT_MODEL}."},
                "raw": {"type": "boolean", "description": "Return the compact raw xAI response JSON."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def _tool_definitions() -> list[Dict[str, Any]]:
    definitions = [
        _x_search_tool_definition(),
        mcp_posts.posts_tool_definition(DEFAULT_MODEL),
        mcp_posts.latest_posts_tool_definition(DEFAULT_MODEL),
    ]
    return [definition for definition in definitions if _tool_enabled(str(definition["name"]))]


_clean_handle_list = mcp_posts.clean_handle_list
_clean_iso8601_date = mcp_posts.clean_iso8601_date

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
    to_date = _clean_iso8601_date(arguments, "to_date", inclusive_end=True)
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
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def _compact_response(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: response.get(key)
        for key in ("id", "model", "status", "usage", "output")
        if response.get(key) is not None
    }


def _record_x_search(status: str, duration: float) -> None:
    global _x_search_total_count, _x_search_total_duration
    _x_search_counts[status] += 1
    _x_search_total_count += 1
    _x_search_total_duration += duration


def metrics_lines() -> list[str]:
    lines = [
        "# HELP mcp_x_search_requests_total Total MCP x_search tool calls by status",
        "# TYPE mcp_x_search_requests_total counter",
    ]
    for status in ("success", "error"):
        lines.append(f'mcp_x_search_requests_total{{status="{status}"}} {_x_search_counts[status]}')
    lines.extend(
        [
            "# HELP mcp_x_search_request_duration_seconds_total Total MCP x_search call duration",
            "# TYPE mcp_x_search_request_duration_seconds_total counter",
            f"mcp_x_search_request_duration_seconds_total {_x_search_total_duration}",
            "# HELP mcp_x_search_request_count_total Total MCP x_search call count",
            "# TYPE mcp_x_search_request_count_total counter",
            f"mcp_x_search_request_count_total {_x_search_total_count}",
            "# HELP mcp_x_search_active_requests Active MCP x_search calls",
            "# TYPE mcp_x_search_active_requests gauge",
            f"mcp_x_search_active_requests {_x_search_active}",
            "# HELP mcp_x_search_concurrency_limit Configured MCP x_search concurrency limit",
            "# TYPE mcp_x_search_concurrency_limit gauge",
            f"mcp_x_search_concurrency_limit {config.GROK_PROXY_MCP_X_SEARCH_CONCURRENCY}",
        ]
    )
    return lines


async def _call_x_search(arguments: Dict[str, Any]) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")

    model = str(arguments.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    payload = {
        "model": model,
        "input": query,
        "tools": [_build_x_search_tool(arguments)],
        "temperature": 0,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **await token_manager.get_auth_headers(),
    }

    async with _x_search_semaphore:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            response = await client.post(XAI_RESPONSES_URL, headers=headers, json=payload)

    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:500]
        raise RuntimeError(f"xAI x_search request failed ({response.status_code}): {detail}")

    data = response.json()
    if arguments.get("raw") is True:
        return json.dumps(_compact_response(data), ensure_ascii=False, separators=(",", ":"))

    text = _extract_output_text(data)
    return text or json.dumps(_compact_response(data), ensure_ascii=False, separators=(",", ":"))


async def _call_posts_result(arguments: Dict[str, Any], *, tool_name: str) -> Dict[str, Any]:
    if tool_name == LATEST_POSTS_TOOL_NAME:
        search_arguments, metadata = _build_latest_posts_search_arguments(arguments)
    else:
        search_arguments, metadata = _build_posts_search_arguments(arguments)
    text = await _call_x_search(search_arguments)
    return mcp_posts.posts_result(tool_name, text, metadata)


async def _handle(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    method = request.get("method")

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        params = request.get("params") or {}
        tool_name = params.get("name")
        if tool_name not in {X_SEARCH_TOOL_NAME, POSTS_TOOL_NAME, LATEST_POSTS_TOOL_NAME}:
            return _error(request_id, -32602, "unknown tool")
        if not _tool_enabled(str(tool_name)):
            return _error(request_id, -32602, f"tool disabled by GROK_GATEWAY_MCP_TOOL_ALLOWLIST: {tool_name}")
        start = time.monotonic()
        global _x_search_active
        _x_search_active += 1
        try:
            arguments = params.get("arguments") or {}
            if tool_name in {POSTS_TOOL_NAME, LATEST_POSTS_TOOL_NAME}:
                result = await _call_posts_result(arguments, tool_name=str(tool_name))
                _record_x_search("success", time.monotonic() - start)
                return _result(request_id, result)
            else:
                text = await _call_x_search(arguments)
            _record_x_search("success", time.monotonic() - start)
            return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:
            _record_x_search("error", time.monotonic() - start)
            return _result(
                request_id,
                {"content": [{"type": "text", "text": f"x_search failed: {exc}"}], "isError": True},
            )
        finally:
            _x_search_active -= 1

    return _error(request_id, -32601, f"method not found: {method}")


async def _main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = await _handle(request)
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        except Exception as exc:
            response = _error(None, -32603, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(_main())
