from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Dict

import xai_responses
from error_sanitizer import sanitize_text
from retrieve_metrics import (
    metrics_lines as orchestration_metrics_lines,
    record_error,
    record_retrieval_status,
    record_stage,
    record_timeout,
)
from retrieve_oembed import merge_oembed_posts, target_status_ids_needing_text
from retrieve_payload import (
    add_target_citation_items,
    assemble_payload,
    finalize_payload,
    merge_raw_payload,
    merge_stage_payload,
    raw_decision,
    raw_expansion_query,
    target_fallback_query,
)
from retrieve_policy import RequestBudget, reasoning_effort_for
from retrieve_routing import build_retrieve_search_arguments
from retrieve_schema import BACKEND, RAW_MODEL, RETRIEVE_TOOL_NAME, SCHEMA_VERSION, SOURCE_LIMIT
from retrieve_stages import SearchCaller, run_search_stage
from x_oembed import OEMBED_TIMEOUT_SECONDS, fetch_oembed_posts

_quality_gate_counts: defaultdict[str, int] = defaultdict(int)
_raw_expansion_counts: defaultdict[tuple[str, str], int] = defaultdict(int)


def metrics_lines() -> list[str]:
    lines = [
        "# HELP mcp_x_retrieve_quality_gate_total MCP x_retrieve quality gate decisions",
        "# TYPE mcp_x_retrieve_quality_gate_total counter",
    ]
    for decision in ("pass", "fail"):
        lines.append(f'mcp_x_retrieve_quality_gate_total{{decision="{decision}"}} {_quality_gate_counts[decision]}')
    lines.extend(
        [
            "# HELP mcp_x_retrieve_raw_expansion_total MCP x_retrieve raw expansion calls by reason and status",
            "# TYPE mcp_x_retrieve_raw_expansion_total counter",
        ]
    )
    for (reason, status), count in sorted(_raw_expansion_counts.items()):
        lines.append(f'mcp_x_retrieve_raw_expansion_total{{reason="{reason}",status="{status}"}} {count}')
    lines.extend(orchestration_metrics_lines())
    return lines


async def call_retrieve(arguments: Dict[str, Any], *, search: SearchCaller) -> Dict[str, Any]:
    search_arguments, metadata = build_retrieve_search_arguments(arguments)
    budget = RequestBudget()
    effort = reasoning_effort_for(metadata)
    try:
        stable_result = await run_search_stage(
            search,
            search_arguments,
            stage="stable_extract",
            budget=budget,
            reasoning_effort=effort,
        )
    except Exception as exc:
        if not metadata.get("target_status_ids"):
            raise
        stable_payload = _failed_stable_payload(
            metadata,
            model=str(search_arguments.get("model") or "unknown"),
            error_text=sanitize_text(exc),
        )
    else:
        stable_payload = assemble_payload(stable_result, metadata, stage_name="stable_extract")

    if metadata.get("target_status_ids"):
        await _run_exact_target_lane(stable_payload, search_arguments, metadata, search, budget, effort)
    else:
        await _run_general_lane(stable_payload, search_arguments, metadata, search, budget)

    add_target_citation_items(stable_payload, metadata)
    finalize_payload(stable_payload, metadata)
    record_retrieval_status(str(stable_payload["retrieval_status"]))
    body = json.dumps(stable_payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": body}], "structuredContent": stable_payload, "isError": False}


async def _run_general_lane(
    payload: Dict[str, Any],
    search_arguments: Dict[str, Any],
    metadata: Dict[str, Any],
    search: SearchCaller,
    budget: RequestBudget,
) -> None:
    run_raw, reason = raw_decision(payload, metadata)
    _quality_gate_counts["fail" if run_raw else "pass"] += 1
    if not run_raw:
        _raw_expansion_counts[(reason, "skipped")] += 1
        payload["retrieval_stages"].append(
            {"name": "raw_expansion", "model": RAW_MODEL, "status": "skipped", "reason": reason}
        )
        return

    raw_arguments = dict(search_arguments)
    raw_arguments["model"] = RAW_MODEL
    raw_arguments["query"] = raw_expansion_query(str(raw_arguments["query"]))
    try:
        raw_result = await run_search_stage(
            search,
            raw_arguments,
            stage="raw_expansion",
            budget=budget,
            reasoning_effort=None,
        )
    except Exception as exc:
        _raw_expansion_counts[(reason, "failed")] += 1
        payload["warnings"].append(f"raw expansion failed: {sanitize_text(exc)}")
        payload["retrieval_stages"].append(
            {"name": "raw_expansion", "model": RAW_MODEL, "status": "failed", "reason": reason}
        )
    else:
        _raw_expansion_counts[(reason, "success")] += 1
        merge_raw_payload(payload, raw_result, metadata)


async def _run_exact_target_lane(
    payload: Dict[str, Any],
    search_arguments: Dict[str, Any],
    metadata: Dict[str, Any],
    search: SearchCaller,
    budget: RequestBudget,
    effort: str,
) -> None:
    _quality_gate_counts["pass"] += 1
    _raw_expansion_counts[("explicit_target_lane", "skipped")] += 1
    payload["retrieval_stages"].append(
        {"name": "raw_expansion", "model": RAW_MODEL, "status": "skipped", "reason": "explicit_target_lane"}
    )
    await _run_public_oembed(payload, metadata, budget)
    fallback_ids = target_status_ids_needing_text(payload, metadata)
    if not fallback_ids or metadata.get("model_policy") == "stable_only":
        return

    fallback_arguments = dict(search_arguments)
    fallback_arguments["query"] = target_fallback_query(fallback_ids, metadata)
    fallback_arguments.pop("from_date", None)
    fallback_arguments.pop("to_date", None)
    try:
        fallback_result = await run_search_stage(
            search,
            fallback_arguments,
            stage="target_fallback",
            budget=budget,
            reasoning_effort=effort,
        )
    except Exception as exc:
        payload["warnings"].append(f"target fallback failed: {sanitize_text(exc)}")
        payload["retrieval_stages"].append(
            {
                "name": "target_fallback",
                "target_status_ids": fallback_ids,
                "model": fallback_arguments.get("model") or "unknown",
                "status": "failed",
            }
        )
    else:
        fallback_payload = assemble_payload(fallback_result, metadata, stage_name="target_fallback")
        fallback_payload["retrieval_stages"][0]["target_status_ids"] = fallback_ids
        merge_stage_payload(payload, fallback_payload)


async def _run_public_oembed(payload: Dict[str, Any], metadata: Dict[str, Any], budget: RequestBudget) -> None:
    status_ids = target_status_ids_needing_text(payload, metadata)
    if not status_ids:
        return
    timeout = min(budget.stage_timeout(), OEMBED_TIMEOUT_SECONDS + 2.0)
    started = time.monotonic()
    if timeout <= 0:
        record_timeout("total")
        record_error(stage="public_oembed", kind="total_timeout")
        _append_oembed_stage(payload, status_ids, "skipped", 0)
        return
    try:
        result = await asyncio.wait_for(
            fetch_oembed_posts(status_ids, list(metadata.get("handles") or [])),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        boundary = "total" if budget.remaining() <= 0 else "stage"
        record_timeout(boundary)
        record_error(stage="public_oembed", kind=f"{boundary}_timeout")
        record_stage(
            stage="public_oembed",
            model="publish.twitter.com/oembed",
            status="timeout",
            reasoning_effort="none",
            duration_seconds=time.monotonic() - started,
        )
        payload["warnings"].append("public oEmbed stage timed out")
        _append_oembed_stage(payload, status_ids, "failed", 0)
        return

    merge_oembed_posts(payload, result.posts)
    payload["warnings"].extend(result.warnings)
    status = "success" if result.posts else "empty"
    record_stage(
        stage="public_oembed",
        model="publish.twitter.com/oembed",
        status=status,
        reasoning_effort="none",
        duration_seconds=time.monotonic() - started,
    )
    _append_oembed_stage(payload, status_ids, status, len(result.posts))


def _append_oembed_stage(payload: Dict[str, Any], status_ids: list[str], status: str, items: int) -> None:
    payload["retrieval_stages"].append(
        {
            "name": "public_oembed",
            "model": "publish.twitter.com/oembed",
            "status": status,
            "target_status_ids": status_ids,
            "items": items,
        }
    )


def _failed_stable_payload(metadata: Dict[str, Any], *, model: str, error_text: str) -> Dict[str, Any]:
    result = xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, model, degraded=True)
    payload = assemble_payload(result, metadata, stage_name="stable_extract")
    payload["retrieval_stages"][0]["status"] = "failed"
    payload["warnings"].append(f"stable extract failed: {error_text}")
    return payload


def error_result(arguments: Dict[str, Any], error_text: str) -> Dict[str, Any]:
    query = arguments.get("query") if isinstance(arguments.get("query"), str) else None
    raw_handles = arguments.get("handles")
    handles: list[Any] = raw_handles if isinstance(raw_handles, list) else []
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": RETRIEVE_TOOL_NAME,
        "backend": BACKEND,
        "timeline_verified": False,
        "source_limit": SOURCE_LIMIT,
        "mode": "semantic_research",
        "request": {
            "intent": arguments.get("intent") if isinstance(arguments.get("intent"), str) else "auto",
            "mode": "semantic_research",
            "handles": [str(handle).lstrip("@") for handle in handles if isinstance(handle, str)],
            "query": query,
        },
        "retrieval_stages": [{"name": "stable_extract", "model": arguments.get("model") or "unknown", "status": "failed"}],
        "retrieval_status": "error",
        "models_used": [],
        "warnings": [f"x_retrieve failed: {error_text}"],
        "filter_reliability": {"author": "unknown", "date": "unknown", "query": "unknown", "engagement": "unknown"},
        "sources": [],
        "source_extraction_status": "not_available",
        "posts": [],
        "items": [],
        "groups": {"primary": [], "supporting": [], "reactions": [], "rejected_candidates": []},
    }
    record_retrieval_status("error")
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": body}], "structuredContent": payload, "isError": True}
