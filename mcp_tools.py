"""Public MCP tool registration and dispatch for Grok MCP Gateway."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import config
import mcp_posts
from retrieve import pipeline
from retrieve import x_search
from retrieve.schema import RETRIEVE_MODEL_MAX_CHARS, retrieve_tool_definition

X_SEARCH_TOOL_NAME = "x_search"
POSTS_TOOL_NAME = mcp_posts.POSTS_TOOL_NAME
LATEST_POSTS_TOOL_NAME = mcp_posts.LATEST_POSTS_TOOL_NAME
RETRIEVE_TOOL_NAME = pipeline.RETRIEVE_TOOL_NAME
SERVER_VERSION = "0.1.0"
DEFAULT_MODEL = config.GROK_PROXY_RETRIEVE_MODEL
TOOL_NAMES = {RETRIEVE_TOOL_NAME}
REMOVED_TOOL_NAMES = {X_SEARCH_TOOL_NAME, POSTS_TOOL_NAME, LATEST_POSTS_TOOL_NAME}


def tool_enabled(tool_name: str) -> bool:
    return tool_name.lower() in config.GROK_GATEWAY_MCP_TOOL_ALLOWLIST


def tool_definitions() -> list[Dict[str, Any]]:
    definitions = [retrieve_tool_definition(DEFAULT_MODEL)]
    return [definition for definition in definitions if tool_enabled(str(definition["name"]))]


def tool_removed(tool_name: str) -> bool:
    return tool_name in REMOVED_TOOL_NAMES


def tool_error_result(tool_name: str, arguments: Dict[str, Any], error_text: str) -> Dict[str, Any]:
    if tool_name == RETRIEVE_TOOL_NAME:
        retrieve_arguments = dict(arguments)
        requested_model = retrieve_arguments.get("model")
        model = requested_model.strip() if isinstance(requested_model, str) else ""
        if model:
            retrieve_arguments["model"] = model[:RETRIEVE_MODEL_MAX_CHARS]
        else:
            retrieve_arguments.pop("model", None)
        return pipeline.error_result(retrieve_arguments, error_text)
    return {"content": [{"type": "text", "text": f"{tool_name} failed: {error_text}"}], "isError": True}


def metrics_lines() -> list[str]:
    return x_search.metrics_lines()


def _search_caller():
    """Use mcp_x_search._call_x_search_result when tests patch that module."""
    import sys

    facade = sys.modules.get("mcp_x_search")
    if facade is not None:
        return facade._call_x_search_result
    return x_search._call_x_search_result


async def call_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    start = time.monotonic()
    x_search._x_search_active += 1
    try:
        if tool_name != RETRIEVE_TOOL_NAME:
            raise ValueError(f"tool removed in vNext: {tool_name}. Use x_retrieve.")
        retrieve_arguments = dict(arguments)
        model_value = retrieve_arguments.get("model")
        if model_value is not None and not isinstance(model_value, str):
            raise ValueError("model must be a string")
        requested_model = model_value.strip() if isinstance(model_value, str) else ""
        if requested_model:
            retrieve_arguments["model"] = requested_model
        else:
            retrieve_arguments.pop("model", None)
        result = await pipeline.call_retrieve(retrieve_arguments, search=_search_caller())
        x_search._record_x_search("success", time.monotonic() - start)
        return result
    except Exception:
        x_search._record_x_search("error", time.monotonic() - start)
        raise
    finally:
        x_search._x_search_active -= 1


async def _handle(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import mcp_server

    return await mcp_server.handle(request)
