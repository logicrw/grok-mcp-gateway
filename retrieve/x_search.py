"""xAI x_search request construction and bounded upstream calls."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Dict

import config
import mcp_posts
import xai_responses
from retrieve.policy import build_xai_responses_payload, resolve_store_flag
from retrieve.schema import RETRIEVE_MODEL_MAX_CHARS
from retrieve.stages import StageOverloaded

TOOL_NAME = "x_search"
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
    "_reasoning_effort",
    "_max_turns",
    "_store",
    "_structured_output",
}

# Env alias kept for callers and /metrics. Not renamed in this package split.
_x_search_semaphore = asyncio.Semaphore(config.GROK_PROXY_MCP_X_SEARCH_CONCURRENCY)
_x_search_counts: defaultdict[str, int] = defaultdict(int)
_x_search_total_duration: float = 0.0
_x_search_total_count: int = 0
_x_search_active: int = 0

_clean_handle_list = mcp_posts.clean_handle_list
_clean_iso8601_date = mcp_posts.clean_iso8601_date
_validate_date_order = mcp_posts.validate_date_order


def default_model() -> str:
    return config.GROK_PROXY_RETRIEVE_MODEL


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
    from retrieve import pipeline

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
    lines.extend(pipeline.metrics_lines())
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

    model = str(arguments.get("model") or default_model()).strip() or default_model()
    if len(model) > RETRIEVE_MODEL_MAX_CHARS:
        raise ValueError(f"model must be at most {RETRIEVE_MODEL_MAX_CHARS} characters")

    max_turns = arguments.get("_max_turns")
    if not (isinstance(max_turns, int) and max_turns > 0):
        max_turns = None

    store_arg = arguments.get("_store")
    store = resolve_store_flag(bool(store_arg) if store_arg is not None else None)

    reasoning_effort = arguments.get("_reasoning_effort")
    if not isinstance(reasoning_effort, str):
        reasoning_effort = None

    return build_xai_responses_payload(
        query=query,
        x_search_tool=_build_x_search_tool(arguments),
        model=model,
        max_turns=max_turns,
        store=store,
        structured_output=bool(arguments.get("_structured_output")),
        reasoning_effort=reasoning_effort,
    )


async def _call_x_search_result(arguments: Dict[str, Any]) -> xai_responses.ResponsesResult:
    # Admission wait is bounded separately from the stage timeout so queueing is
    # reported as overload, never misread as a model-quality stage failure.
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            _x_search_semaphore.acquire(),
            timeout=config.GROK_PROXY_MCP_X_SEARCH_QUEUE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise StageOverloaded(time.monotonic() - started) from exc
    try:
        return await xai_responses.post(_x_search_payload(arguments))
    finally:
        _x_search_semaphore.release()
