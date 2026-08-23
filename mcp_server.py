"""Minimal MCP JSON-RPC protocol layer for Grok MCP Gateway."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from typing import Any, Dict, Optional

import xai_responses
from error_sanitizer import sanitize_text
import mcp_tools

SERVER_NAME = "grok-mcp-gateway"
SERVER_VERSION = mcp_tools.SERVER_VERSION
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05")

# Upper bound for one stdio JSON-RPC message; larger frames are discarded.
MAX_MESSAGE_BYTES = 1024 * 1024


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _valid_request_id(value: Any) -> bool:
    if value is None or isinstance(value, str):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


async def handle(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(request, dict):
        return _error(None, -32600, "invalid request: request must be an object")

    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0":
        return _error(
            request_id if _valid_request_id(request_id) else None,
            -32600,
            'invalid request: jsonrpc must be "2.0"',
        )
    method = request.get("method")
    if not isinstance(method, str) or not method:
        return _error(
            request_id if _valid_request_id(request_id) else None,
            -32600,
            "invalid request: method must be a non-empty string",
        )
    if not _valid_request_id(request_id):
        return _error(None, -32600, "invalid request: id must be a string, number, or null")
    if "id" not in request:
        # JSON-RPC notification: never emit a response and run no side effects.
        return None

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
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": mcp_tools.tool_definitions()})
    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "invalid params")
        tool_name = params.get("name")
        if isinstance(tool_name, str) and mcp_tools.tool_removed(tool_name):
            return _error(request_id, -32602, f"tool removed in vNext: {tool_name}. Use x_retrieve.")
        if not isinstance(tool_name, str) or tool_name not in mcp_tools.TOOL_NAMES:
            return _error(request_id, -32602, "unknown tool")
        if not mcp_tools.tool_enabled(tool_name):
            return _error(request_id, -32602, f"tool disabled by GROK_GATEWAY_MCP_TOOL_ALLOWLIST: {tool_name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            result = await mcp_tools.call_tool(tool_name, arguments)
            return _result(request_id, result)
        except Exception as exc:
            return _result(request_id, mcp_tools.tool_error_result(tool_name, arguments, sanitize_text(exc)))

    return _error(request_id, -32601, f"method not found: {method}")


def _write_response(out: Any, response: Dict[str, Any]) -> None:
    out.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    flush = getattr(out, "flush", None)
    if callable(flush):
        flush()


async def stdio_main(
    reader: Optional[asyncio.StreamReader] = None,
    writer: Optional[Any] = None,
) -> None:
    if reader is None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES)
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    out = writer if writer is not None else sys.stdout
    oversized_streak = 0
    try:
        while True:
            try:
                raw = await reader.readline()
                oversized_streak = 0
            except ValueError:
                # Frame exceeded the stream limit. StreamReader.readline already
                # discarded the oversized frame, so keep serving; a bounded streak
                # guards against a pathological stream that never makes progress.
                oversized_streak += 1
                _write_response(out, _error(None, -32700, "parse error: oversized frame discarded"))
                if oversized_streak >= 8:
                    break
                continue
            if not raw:
                break
            try:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                if len(raw) > MAX_MESSAGE_BYTES + 1024:
                    _write_response(out, _error(None, -32600, "invalid request: message too large"))
                    continue
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                response = await handle(request)
            except UnicodeDecodeError:
                response = _error(None, -32700, "parse error: invalid utf-8")
            except json.JSONDecodeError:
                response = _error(None, -32700, "parse error")
            except ValueError:
                response = _error(None, -32600, "invalid request")
            except Exception as exc:
                response = _error(None, -32603, sanitize_text(exc))
            if response is not None:
                _write_response(out, response)
    finally:
        await xai_responses.aclose_client()


if __name__ == "__main__":
    asyncio.run(stdio_main())
