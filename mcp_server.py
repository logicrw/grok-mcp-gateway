"""Minimal MCP JSON-RPC protocol layer for Grok MCP Gateway."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, Optional

from error_sanitizer import sanitize_text
import mcp_x_search

SERVER_NAME = "grok-mcp-gateway"
SERVER_VERSION = mcp_x_search.SERVER_VERSION
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05")


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def handle(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    method = request.get("method")

    if method == "initialize":
        params = request.get("params") or {}
        client_version = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol_version = client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": mcp_x_search.tool_definitions()})
    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "invalid params")
        tool_name = params.get("name")
        if isinstance(tool_name, str) and mcp_x_search.tool_removed(tool_name):
            return _error(request_id, -32602, f"tool removed in vNext: {tool_name}. Use x_retrieve.")
        if not isinstance(tool_name, str) or tool_name not in mcp_x_search.TOOL_NAMES:
            return _error(request_id, -32602, "unknown tool")
        if not mcp_x_search.tool_enabled(tool_name):
            return _error(request_id, -32602, f"tool disabled by GROK_GATEWAY_MCP_TOOL_ALLOWLIST: {tool_name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            result = await mcp_x_search.call_tool(tool_name, arguments)
            return _result(request_id, result)
        except Exception as exc:
            return _result(
                request_id,
                {"content": [{"type": "text", "text": f"{tool_name} failed: {sanitize_text(exc)}"}], "isError": True},
            )

    return _error(request_id, -32601, f"method not found: {method}")


async def stdio_main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = await handle(request)
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        except Exception as exc:
            response = _error(None, -32603, sanitize_text(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(stdio_main())
