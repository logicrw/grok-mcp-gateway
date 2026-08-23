from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Dict

import xai_responses
from error_sanitizer import sanitize_text
from retrieve.metrics import (
    metrics_lines as orchestration_metrics_lines,
    record_error,
    record_retrieval_status,
    record_route,
    record_stage,
    record_timeout,
)
from retrieve.oembed import merge_oembed_posts, target_status_ids_needing_text
from retrieve.payload import (
    _request_metadata,
    add_target_citation_items,
    assemble_payload,
    finalize_payload,
    merge_raw_payload,
    merge_stage_payload,
    raw_decision,
    raw_expansion_query,
    target_fallback_query,
)
from retrieve.policy import (
    RequestBudget,
    RetrievalPlan,
    _smart_effort,
    evaluate_quality,
    get_routing_config,
    resolve_plan,
    resolve_target_fallback_plan,
    should_escalate_to_smart,
)
from retrieve.routing import build_retrieve_search_arguments
from retrieve.schema import BACKEND, RAW_MODEL, RETRIEVE_TOOL_NAME, SCHEMA_VERSION, SOURCE_LIMIT
from retrieve.stages import SearchCaller, run_search_stage
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
    plan = resolve_plan(metadata, explicit_model=metadata.get("explicit_model"))
    record_route(lane=plan.initial_lane, objective_mode=plan.objective_mode, escalated=False)

    if plan.target_strategy != "none":
        payload = await _run_target_pipeline(search_arguments, metadata, plan, search, budget)
    else:
        payload = await _run_general_pipeline(search_arguments, metadata, plan, search, budget)

    add_target_citation_items(payload, metadata)
    finalize_payload(payload, metadata)
    record_retrieval_status(str(payload["retrieval_status"]))
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": body}], "structuredContent": payload, "isError": False}


async def _run_target_pipeline(
    search_arguments: Dict[str, Any],
    metadata: Dict[str, Any],
    plan: RetrievalPlan,
    search: SearchCaller,
    budget: RequestBudget,
) -> Dict[str, Any]:
    payload = _empty_payload(metadata)

    # Step 1: Public oEmbed first for deterministic exact targets
    await _run_public_oembed(payload, metadata, budget)
    missing_ids = target_status_ids_needing_text(payload, metadata)

    if plan.target_strategy == "exact_only":
        if not missing_ids:
            _record_quality(payload, metadata)
            return payload

        # Missing targets fallback (Fast Lane first unless explicit model)
        fallback_plan = resolve_target_fallback_plan(smart=False, objective=plan.objective_mode)
        fallback_arguments = dict(search_arguments)
        explicit_model = metadata.get("explicit_model")
        model = explicit_model or fallback_plan.model
        fallback_arguments["model"] = model
        fallback_arguments["query"] = target_fallback_query(missing_ids, metadata)
        fallback_arguments["_max_turns"] = fallback_plan.max_turns
        fallback_arguments["_structured_output"] = True
        fallback_arguments.pop("from_date", None)
        fallback_arguments.pop("to_date", None)

        stage_name = "target_fallback" if explicit_model else "target_fast_fallback"
        try:
            fallback_result = await run_search_stage(
                search,
                fallback_arguments,
                stage=stage_name,
                budget=budget,
                reasoning_effort=fallback_plan.reasoning_effort,
                stage_seconds=fallback_plan.stage_timeout_seconds,
            )
        except Exception as exc:
            payload["warnings"].append(f"target fallback failed: {sanitize_text(exc)}")
            payload["retrieval_stages"].append(
                {
                    "name": stage_name,
                    "target_status_ids": missing_ids,
                    "model": fallback_arguments.get("model") or "unknown",
                    "status": "failed",
                }
            )

        else:
            fallback_payload = assemble_payload(fallback_result, metadata, stage_name=stage_name)
            fallback_payload["retrieval_stages"][0]["target_status_ids"] = missing_ids
            merge_stage_payload(payload, fallback_payload)

        # Quality check: if still missing and budget allows, escalate to Smart fallback
        still_missing = target_status_ids_needing_text(payload, metadata)
        quality = _record_quality(payload, metadata)
        if still_missing and should_escalate_to_smart(fallback_plan, quality, remaining_seconds=budget.remaining()):
            record_route(lane="smart", objective_mode=plan.objective_mode, escalated=True)
            smart_plan = resolve_target_fallback_plan(smart=True, objective=plan.objective_mode)
            smart_arguments = dict(search_arguments)
            smart_arguments["model"] = smart_plan.model
            smart_arguments["query"] = target_fallback_query(still_missing, metadata)
            smart_arguments["_max_turns"] = smart_plan.max_turns
            smart_arguments["_structured_output"] = True
            smart_arguments.pop("from_date", None)
            smart_arguments.pop("to_date", None)
            try:
                smart_result = await run_search_stage(
                    search,
                    smart_arguments,
                    stage="target_smart_fallback",
                    budget=budget,
                    reasoning_effort=smart_plan.reasoning_effort,
                    stage_seconds=smart_plan.stage_timeout_seconds,
                )
            except Exception as exc:
                payload["warnings"].append(f"smart target fallback failed: {sanitize_text(exc)}")
                payload["retrieval_stages"].append(
                    {
                        "name": "target_smart_fallback",
                        "target_status_ids": still_missing,
                        "model": smart_arguments.get("model") or "unknown",
                        "status": "failed",
                    }
                )
            else:
                smart_payload = assemble_payload(smart_result, metadata, stage_name="target_smart_fallback")
                smart_payload["retrieval_stages"][0]["target_status_ids"] = still_missing
                merge_stage_payload(payload, smart_payload)

        return payload

    # seed_then_research: Seed was fetched from oEmbed, now research with Smart model
    research_arguments = dict(search_arguments)
    research_arguments["model"] = plan.model or get_routing_config().smart_model
    research_arguments["_max_turns"] = plan.max_turns
    research_arguments["_structured_output"] = True
    if plan.reasoning_effort:
        research_arguments["_reasoning_effort"] = plan.reasoning_effort

    try:
        research_result = await run_search_stage(
            search,
            research_arguments,
            stage="smart_extract",
            budget=budget,
            reasoning_effort=plan.reasoning_effort,
            stage_seconds=plan.stage_timeout_seconds,
        )
    except Exception as exc:
        payload["warnings"].append(f"smart extract failed: {sanitize_text(exc)}")
        payload["retrieval_stages"].append(
            {
                "name": "smart_extract",
                "model": research_arguments.get("model") or "unknown",
                "status": "failed",
            }
        )
    else:
        research_payload = assemble_payload(research_result, metadata, stage_name="smart_extract")
        merge_stage_payload(payload, research_payload)

    _record_quality(payload, metadata)
    await _maybe_run_raw_expansion(payload, search_arguments, metadata, search, budget)
    return payload


async def _run_general_pipeline(
    search_arguments: Dict[str, Any],
    metadata: Dict[str, Any],
    plan: RetrievalPlan,
    search: SearchCaller,
    budget: RequestBudget,
) -> Dict[str, Any]:
    stage_name = (
        "fast_extract"
        if plan.initial_lane == "fast"
        else ("smart_extract" if plan.initial_lane == "smart" else "stable_extract")
    )
    stage_arguments = dict(search_arguments)
    stage_arguments["model"] = plan.model
    stage_arguments["_max_turns"] = plan.max_turns
    stage_arguments["_structured_output"] = True
    if plan.reasoning_effort:
        stage_arguments["_reasoning_effort"] = plan.reasoning_effort

    try:
        result = await run_search_stage(
            search,
            stage_arguments,
            stage=stage_name,
            budget=budget,
            reasoning_effort=plan.reasoning_effort,
            stage_seconds=plan.stage_timeout_seconds,
        )
    except Exception as exc:
        if plan.initial_lane == "fast" and budget.remaining() >= get_routing_config().smart_escalation_min_remaining_seconds:
            # Fast lane failed or timed out; escalate directly to Smart Lane
            record_route(lane="smart", objective_mode=plan.objective_mode, escalated=True)
            payload = _failed_stage_payload(metadata, stage_name=stage_name, model=str(plan.model), error_text=sanitize_text(exc))
            await _run_smart_escalation(payload, search_arguments, metadata, plan, search, budget)
            await _maybe_run_raw_expansion(payload, search_arguments, metadata, search, budget)
            return payload
        raise

    payload = assemble_payload(result, metadata, stage_name=stage_name)
    quality = _record_quality(payload, metadata)

    if not quality.passed and should_escalate_to_smart(plan, quality, remaining_seconds=budget.remaining()):
        record_route(lane="smart", objective_mode=plan.objective_mode, escalated=True)
        await _run_smart_escalation(payload, search_arguments, metadata, plan, search, budget)

    await _maybe_run_raw_expansion(payload, search_arguments, metadata, search, budget)
    return payload


async def _run_smart_escalation(
    payload: Dict[str, Any],
    search_arguments: Dict[str, Any],
    metadata: Dict[str, Any],
    plan: RetrievalPlan,
    search: SearchCaller,
    budget: RequestBudget,
) -> None:
    routing_config = get_routing_config()
    smart_effort = _smart_effort(plan.objective_mode)
    smart_arguments = dict(search_arguments)
    smart_arguments["model"] = routing_config.smart_model
    smart_arguments["_max_turns"] = routing_config.smart_max_turns
    smart_arguments["_structured_output"] = True
    smart_arguments["_reasoning_effort"] = smart_effort

    try:
        smart_result = await run_search_stage(
            search,
            smart_arguments,
            stage="smart_escalation",
            budget=budget,
            reasoning_effort=smart_effort,
            stage_seconds=routing_config.smart_stage_timeout_seconds,
        )
    except Exception as exc:
        payload["warnings"].append(f"smart escalation failed: {sanitize_text(exc)}")
        payload["retrieval_stages"].append(
            {
                "name": "smart_escalation",
                "model": smart_arguments.get("model") or "unknown",
                "status": "failed",
            }
        )
    else:
        smart_payload = assemble_payload(smart_result, metadata, stage_name="smart_escalation")
        merge_stage_payload(payload, smart_payload)


async def _maybe_run_raw_expansion(
    payload: Dict[str, Any],
    search_arguments: Dict[str, Any],
    metadata: Dict[str, Any],
    search: SearchCaller,
    budget: RequestBudget,
) -> None:
    run_raw, reason = raw_decision(payload, metadata)
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


async def _run_public_oembed(payload: Dict[str, Any], metadata: Dict[str, Any], budget: RequestBudget) -> None:
    status_ids = list(metadata.get("target_status_ids") or [])
    if not status_ids:
        return
    timeout = min(budget.stage_timeout(), OEMBED_TIMEOUT_SECONDS + 2.0)
    started = time.monotonic()
    if timeout <= 0:
        record_timeout("total")
        record_error(stage="public_oembed", kind="total_timeout")
        payload["warnings"].append("public oEmbed skipped: total retrieval budget exhausted")
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
        _append_oembed_stage(payload, status_ids, "timeout", 0)
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


def _record_quality(payload: Dict[str, Any], metadata: Dict[str, Any]):
    quality = evaluate_quality(payload, metadata)
    _quality_gate_counts["pass" if quality.passed else "fail"] += 1
    return quality


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


def _empty_payload(metadata: Dict[str, Any]) -> Dict[str, Any]:
    from retrieve.payload import _groups, _request_metadata

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": RETRIEVE_TOOL_NAME,
        "backend": BACKEND,
        "timeline_verified": False,
        "source_limit": SOURCE_LIMIT,
        "mode": metadata["mode"],
        "request": _request_metadata(metadata),
        "retrieval_stages": [],
        "models_used": [],
        "warnings": list(metadata.get("routing_warnings") or []),
        "filter_reliability": {"author": "unknown", "date": "unknown", "query": "unknown", "engagement": "unknown"},
        "sources": [],
        "source_extraction_status": "not_available",
        "posts": [],
        "items": [],
        "groups": _groups([]),
    }


def _failed_stage_payload(metadata: Dict[str, Any], *, stage_name: str, model: str, error_text: str) -> Dict[str, Any]:
    result = xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, model, degraded=True)
    payload = assemble_payload(result, metadata, stage_name=stage_name)
    payload["retrieval_stages"][0]["status"] = "failed"
    payload["warnings"].append(f"{stage_name} failed: {error_text}")
    return payload


def error_result(arguments: Dict[str, Any], error_text: str) -> Dict[str, Any]:
    metadata = _error_metadata(arguments)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": RETRIEVE_TOOL_NAME,
        "backend": BACKEND,
        "timeline_verified": False,
        "source_limit": SOURCE_LIMIT,
        "mode": metadata["mode"],
        "request": _request_metadata(metadata),
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


def _error_metadata(arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        _, metadata = build_retrieve_search_arguments(dict(arguments))
        return metadata
    except Exception:
        query = arguments.get("query") if isinstance(arguments.get("query"), str) else None
        raw_handles = arguments.get("handles")
        handles = (
            [str(handle).lstrip("@") for handle in raw_handles if isinstance(handle, str)]
            if isinstance(raw_handles, list)
            else []
        )
        intent = arguments.get("intent") if isinstance(arguments.get("intent"), str) else "auto"
        return {
            "mode": "semantic_research",
            "intent": intent or "auto",
            "handles": handles,
            "query": query,
            "excluded_handles": [],
            "target_status_ids": [],
        }
