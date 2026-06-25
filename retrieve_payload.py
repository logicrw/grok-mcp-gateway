from __future__ import annotations

import re
from typing import Any, Dict, Optional

import mcp_posts
import xai_responses
from retrieve_schema import BACKEND, RETRIEVE_TOOL_NAME, SCHEMA_VERSION, SOURCE_LIMIT

STATUS_RE = re.compile(r"/status/(\d+)")


def assemble_payload(result: xai_responses.ResponsesResult, metadata: Dict[str, Any], *, stage_name: str) -> Dict[str, Any]:
    text = result.text or result.raw_json()
    parsed = mcp_posts.parse_json_object(text)
    posts_payload = mcp_posts.normalize_posts_payload(
        RETRIEVE_TOOL_NAME,
        parsed,
        metadata,
        raw_text=text,
        sources=result.citations,
    )
    items = [_post_to_item(post, str(metadata["mode"])) for post in posts_payload["posts"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": RETRIEVE_TOOL_NAME,
        "backend": BACKEND,
        "timeline_verified": False,
        "source_limit": SOURCE_LIMIT,
        "mode": metadata["mode"],
        "request": _request_metadata(metadata),
        "retrieval_stages": [{"name": stage_name, "model": result.model, "status": "success"}],
        "models_used": [result.model],
        "warnings": list(posts_payload["warnings"]),
        "filter_reliability": posts_payload["filter_reliability"],
        "sources": posts_payload["sources"],
        "source_extraction_status": posts_payload["source_extraction_status"],
        "posts": posts_payload["posts"],
        "items": items,
        "groups": _groups(items),
    }


def raw_decision(payload: Dict[str, Any], metadata: Dict[str, Any]) -> tuple[bool, str]:
    quality = metadata.get("quality") or {}
    if metadata.get("model_policy") == "stable_only" or quality.get("allow_raw_expansion") is False:
        return False, "policy_disabled"
    if metadata.get("mode") == "latest_by_handle":
        return False, "latest_by_handle"
    if metadata.get("model_policy") == "raw_expanded":
        return True, "policy_forced"
    min_items = int(quality.get("min_items") or 1)
    if len(payload["items"]) < min_items:
        return True, "min_items"
    if quality.get("require_status_url") and not any(item.get("url") for item in payload["items"]):
        return True, "missing_status_url"
    if quality.get("require_original_text") and not any(str(item.get("text") or "").strip() for item in payload["items"]):
        return True, "missing_original_text"
    if metadata.get("mode") in {"source_discovery", "reaction_tracking"} and not any(item.get("url") for item in payload["items"]):
        return True, "mode_requires_status_url"
    return False, "quality_gate_passed"


def should_run_raw(payload: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    run_raw, _reason = raw_decision(payload, metadata)
    return run_raw


def raw_expansion_query(query: str) -> str:
    return (
        query
        + "\n\nExpand raw candidate X posts. Return compact JSON with posts containing text, author, created_at, url, metrics, and confidence. "
        "Do not include reasoning or search narration."
    )


def merge_raw_payload(payload: Dict[str, Any], result: xai_responses.ResponsesResult, metadata: Dict[str, Any]) -> None:
    raw_payload = assemble_payload(result, metadata, stage_name="raw_expansion")
    seen = {_item_key(item) for item in payload["items"]}
    for item in raw_payload["items"]:
        key = _item_key(item)
        if key not in seen:
            seen.add(key)
            payload["items"].append(item)
    payload["posts"].extend(raw_payload["posts"])
    payload["groups"] = _groups(payload["items"])
    payload["sources"].extend(raw_payload["sources"])
    payload["warnings"].extend(raw_payload["warnings"])
    payload["retrieval_stages"].append({"name": "raw_expansion", "model": result.model, "status": "success"})
    if result.model not in payload["models_used"]:
        payload["models_used"].append(result.model)


def _post_to_item(post: Dict[str, Any], mode: str) -> Dict[str, Any]:
    url = post.get("url") if isinstance(post.get("url"), str) else None
    return {
        "id": _status_id(url),
        "url": url,
        "author": post.get("author"),
        "created_at": post.get("created_at"),
        "text": post.get("text") or "",
        "metrics": post.get("metrics") or {},
        "relation": "reaction" if mode == "reaction_tracking" else "primary",
        "confidence": post.get("confidence", "unknown"),
        "warnings": post.get("warnings") or [],
        "citation_backed": bool(post.get("citation_backed", False)),
    }


def _status_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = STATUS_RE.search(url)
    return match.group(1) if match else None


def _groups(items: list[Dict[str, Any]]) -> Dict[str, list[Dict[str, Any]]]:
    primary = [item for item in items if item["relation"] == "primary"]
    reactions = [item for item in items if item["relation"] == "reaction"]
    return {"primary": primary, "supporting": [], "reactions": reactions, "rejected_candidates": []}


def _request_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "intent": metadata.get("intent"),
        "mode": metadata.get("mode"),
        "handles": metadata.get("handles") or [],
        "excluded_handles": metadata.get("excluded_handles") or [],
        "query": metadata.get("query"),
        "compiled_time_range": metadata.get("compiled_time_range"),
        "count": metadata.get("count"),
        "sort": metadata.get("sort"),
        "lookback_days": metadata.get("lookback_days"),
        "model_policy": metadata.get("model_policy"),
    }


def _item_key(item: Dict[str, Any]) -> str:
    return str(item.get("url") or f"{item.get('author')}::{item.get('text')}")
