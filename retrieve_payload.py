from __future__ import annotations

from typing import Any, Dict, Optional

import mcp_posts
import xai_responses
from retrieve_schema import BACKEND, RETRIEVE_TOOL_NAME, SCHEMA_VERSION, SOURCE_LIMIT
from retrieve_text_parser import parse_raw_posts_from_text, status_id_from_url


def assemble_payload(result: xai_responses.ResponsesResult, metadata: Dict[str, Any], *, stage_name: str) -> Dict[str, Any]:
    text = result.text or result.raw_json()
    parsed = mcp_posts.parse_json_object(text) or {}
    if stage_name == "raw_expansion" and not parsed.get("posts"):
        raw_posts = parse_raw_posts_from_text(text, metadata)
        if raw_posts:
            parsed = {"posts": raw_posts}
    posts_payload = mcp_posts.normalize_posts_payload(
        RETRIEVE_TOOL_NAME,
        parsed,
        metadata,
        raw_text=text,
        sources=result.citations,
    )
    items = [_post_to_item(post, str(metadata["mode"])) for post in posts_payload["posts"]]
    items = [item for item in items if _is_usable_item(item)]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": RETRIEVE_TOOL_NAME,
        "backend": BACKEND,
        "timeline_verified": False,
        "source_limit": SOURCE_LIMIT,
        "mode": metadata["mode"],
        "request": _request_metadata(metadata),
        "retrieval_stages": [{"name": stage_name, "model": result.model, "status": "success"}],
        "models_used": [result.model],
        "warnings": list(metadata.get("routing_warnings") or []) + list(posts_payload["warnings"]),
        "filter_reliability": posts_payload["filter_reliability"],
        "sources": posts_payload["sources"],
        "source_extraction_status": posts_payload["source_extraction_status"],
        "posts": posts_payload["posts"],
        "items": items,
        "groups": _groups(items),
    }
    if posts_payload.get("raw_text"):
        payload["raw_text"] = posts_payload["raw_text"]
    return payload


def raw_decision(payload: Dict[str, Any], metadata: Dict[str, Any]) -> tuple[bool, str]:
    quality = metadata.get("quality") or {}
    if metadata.get("model_policy") == "stable_only" or quality.get("allow_raw_expansion") is False:
        return False, "policy_disabled"
    if metadata.get("target_strategy") == "exact_only":
        return False, "exact_only_disallows_raw"
    if metadata.get("model_policy") == "raw_expanded":
        return True, "policy_forced"
    min_items = int(quality.get("min_items") or 1)
    if metadata.get("mode") == "latest_by_handle" and len(payload["items"]) >= min_items:
        return False, "latest_by_handle"
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
    merge_stage_payload(payload, raw_payload)


def merge_stage_payload(payload: Dict[str, Any], stage_payload: Dict[str, Any]) -> None:
    seen = {_item_key(item) for item in payload["items"]}
    for item in stage_payload["items"]:
        key = _item_key(item)
        if key not in seen:
            seen.add(key)
            payload["items"].append(item)
    seen_posts = {_post_key(post) for post in payload["posts"]}
    for post in stage_payload["posts"]:
        key = _post_key(post)
        if key in seen_posts:
            continue
        seen_posts.add(key)
        payload["posts"].append(post)
    payload["groups"] = _groups(payload["items"])
    seen_sources = {_source_key(source) for source in payload["sources"]}
    for source in stage_payload["sources"]:
        key = _source_key(source)
        if key in seen_sources:
            continue
        seen_sources.add(key)
        payload["sources"].append(source)
    payload["warnings"].extend(stage_payload["warnings"])
    payload["retrieval_stages"].extend(stage_payload["retrieval_stages"])
    for model in stage_payload["models_used"]:
        if model not in payload["models_used"]:
            payload["models_used"].append(model)
    _preserve_stage_raw_text(payload, stage_payload)


def missing_target_status_ids(payload: Dict[str, Any], metadata: Dict[str, Any]) -> list[str]:
    target_status_ids = list(metadata.get("target_status_ids") or [])
    if not target_status_ids:
        return []
    target_match = _target_match(payload["items"], target_status_ids)
    return target_match["missing"]


def add_target_citation_items(payload: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    missing_status_ids = missing_target_status_ids(payload, metadata)
    if not missing_status_ids:
        return
    source_by_status_id = {
        status_id: source
        for source in payload.get("sources", [])
        if isinstance(source, dict)
        for status_id in [_status_id(str(source.get("url") or source.get("title") or ""))]
        if status_id
    }
    handles = metadata.get("handles") or []
    author = handles[0] if handles else None
    added = False
    for status_id in missing_status_ids:
        source = source_by_status_id.get(status_id)
        if not source:
            continue
        url = str(source.get("url") or f"https://x.com/i/status/{status_id}")
        payload["items"].append(
            {
                "id": status_id,
                "url": url,
                "author": author,
                "created_at": None,
                "text": "",
                "metrics": {},
                "relation": "primary",
                "confidence": "low",
                "warnings": ["target status URL was citation-backed but text was not extracted"],
                "citation_backed": True,
            }
        )
        added = True
    if added:
        payload["groups"] = _groups(payload["items"])
        payload["warnings"].append("some target status URLs were citation-backed but text was not extracted")


def target_fallback_query(status_ids: list[str], metadata: Dict[str, Any]) -> str:
    handles = metadata.get("handles") or []
    handle_hint = ", ".join("@" + str(handle).lstrip("@") for handle in handles) if handles else "the author shown on X"
    targets = "\n".join(
        f"- Target status ID: {status_id}; URL: https://x.com/i/status/{status_id}" for status_id in status_ids
    )
    return (
        "Fetch exactly these X/Twitter status posts in one search and return only compact JSON in the requested posts schema.\n"
        f"{targets}\n"
        f"Expected author hint: {handle_hint}\n"
        "Do not return nearby posts, search-result narration, or a summary. "
        "Omit unavailable targets and never substitute nearby posts."
    )


def finalize_payload(payload: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    target_status_ids = list(metadata.get("target_status_ids") or [])
    if target_status_ids:
        _retain_exact_targets(payload, set(target_status_ids))
    target_match = _target_match(payload["items"], target_status_ids)
    if target_status_ids:
        payload["target_match"] = target_match
    payload["retrieval_status"] = _retrieval_status(payload, target_match)


def _preserve_stage_raw_text(payload: Dict[str, Any], stage_payload: Dict[str, Any]) -> None:
    raw_text = str(stage_payload.get("raw_text") or "").strip()
    if not raw_text or stage_payload["items"]:
        return
    stage = stage_payload["retrieval_stages"][0] if stage_payload.get("retrieval_stages") else {"name": "unknown"}
    payload.setdefault("stage_diagnostics", []).append(
        {
            "stage": stage.get("name"),
            "model": stage.get("model"),
            "raw_text_preview": raw_text[:1000],
        }
    )


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
    return status_id_from_url(url)


def _retain_exact_targets(payload: Dict[str, Any], target_status_ids: set[str]) -> None:
    payload["items"] = [item for item in payload["items"] if str(item.get("id") or "") in target_status_ids]
    payload["posts"] = [
        post
        for post in payload["posts"]
        if isinstance(post, dict) and status_id_from_url(post.get("url")) in target_status_ids
    ]
    payload["sources"] = [
        source
        for source in payload["sources"]
        if isinstance(source, dict)
        and status_id_from_url(str(source.get("url") or source.get("title") or "")) in target_status_ids
    ]
    payload.pop("raw_text", None)
    payload.pop("stage_diagnostics", None)
    payload["groups"] = _groups(payload["items"])


def _is_usable_item(item: Dict[str, Any]) -> bool:
    return bool(item.get("url") or str(item.get("text") or "").strip())


def _groups(items: list[Dict[str, Any]]) -> Dict[str, list[Dict[str, Any]]]:
    primary = [item for item in items if item["relation"] == "primary"]
    reactions = [item for item in items if item["relation"] == "reaction"]
    return {"primary": primary, "supporting": [], "reactions": reactions, "rejected_candidates": []}


def _target_match(items: list[Dict[str, Any]], target_status_ids: list[str]) -> Dict[str, list[str]]:
    item_ids = {str(item.get("id")) for item in items if item.get("id")}
    matched = [status_id for status_id in target_status_ids if status_id in item_ids]
    missing = [status_id for status_id in target_status_ids if status_id not in item_ids]
    return {"requested": target_status_ids, "matched": matched, "missing": missing}


def _retrieval_status(payload: Dict[str, Any], target_match: Dict[str, list[str]]) -> str:
    items = payload["items"]
    if target_match["requested"] and not target_match["matched"]:
        return "no_match"
    if target_match["missing"]:
        return "degraded"
    if not items:
        return "empty"
    if payload["warnings"] or any(stage.get("status") == "failed" for stage in payload["retrieval_stages"]):
        return "degraded"
    return "ok"


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
        "target_status_ids": metadata.get("target_status_ids") or [],
    }


def _item_key(item: Dict[str, Any]) -> str:
    return str(item.get("url") or f"{item.get('author')}::{item.get('text')}")


def _post_key(post: Any) -> str:
    if not isinstance(post, dict):
        return f"non-dict:{id(post)}"
    url = post.get("url") if isinstance(post.get("url"), str) else None
    status_id = status_id_from_url(url)
    if status_id:
        return f"status:{status_id}"
    if url and url.strip():
        return f"url:{url.strip()}"
    return f"text:{post.get('author')}::{post.get('text')}"


def _source_key(source: Any) -> str:
    if not isinstance(source, dict):
        return f"non-dict:{id(source)}"
    url = source.get("url") if isinstance(source.get("url"), str) else None
    status_id = status_id_from_url(url) or status_id_from_url(
        str(source.get("title") or "") if source.get("title") is not None else None
    )
    if status_id:
        return f"status:{status_id}"
    if url and url.strip():
        return f"url:{url.strip()}"
    return f"title:{source.get('title')}"
