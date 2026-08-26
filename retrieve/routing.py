from __future__ import annotations

from typing import Any, Dict, Optional

import config
import mcp_posts
from retrieve.schema import INTENTS, RETRIEVE_ARGUMENT_KEYS, RETRIEVE_MODEL_MAX_CHARS
from retrieve.text_parser import extract_status_targets


def build_retrieve_search_arguments(arguments: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    mcp_posts.reject_unknown_arguments(arguments, RETRIEVE_ARGUMENT_KEYS)
    query = _clean_retrieve_query(arguments)
    handles = mcp_posts.clean_handle_list(arguments, "handles") or []
    excluded_handles = mcp_posts.clean_handle_list(arguments, "excluded_handles") or []
    if handles and excluded_handles:
        raise ValueError("handles and excluded_handles cannot be used together")
    if not query and not handles:
        raise ValueError("x_retrieve requires query or handles")

    intent = _clean_intent(arguments)
    sort = _clean_sort(arguments, default="latest" if handles and not query else "relevance")
    target_status_ids, routing_warnings = extract_status_targets(
        query,
        limit=config.GROK_PROXY_RETRIEVE_MAX_TARGETS,
    )
    mode = _detect_mode(intent, query, handles, sort, target_status_ids)
    target_strategy = _detect_target_strategy(mode, target_status_ids)
    count = mcp_posts.clean_int(arguments, "count", 10, minimum=1, maximum=20)
    include_replies = _clean_bool(arguments, "include_replies", True)
    include_reposts = _clean_bool(arguments, "include_reposts", True)
    explicit_model = _clean_model(arguments)
    force_refresh = _clean_bool(arguments, "force_refresh", False)
    max_age_seconds = _clean_max_age_seconds(arguments)

    posts_arguments: Dict[str, Any] = {
        "count": count,
        "sort": sort,
        "include_replies": include_replies,
        "include_reposts": include_reposts,
        "model": explicit_model,
    }
    if handles:
        posts_arguments["handles"] = handles
    if query:
        posts_arguments["query"] = query
    if arguments.get("best_effort_filters") is not None:
        posts_arguments["best_effort_filters"] = arguments.get("best_effort_filters")
    _copy_optional(arguments, posts_arguments, "from_date")
    _copy_optional(arguments, posts_arguments, "to_date")
    lookback_days = arguments.get("lookback_days")
    if arguments.get("time_range") is not None:
        posts_arguments["time_range"] = arguments.get("time_range")
    elif mode == "latest_by_handle" and arguments.get("from_date") is None and arguments.get("to_date") is None:
        lookback_days = mcp_posts.clean_int(arguments, "lookback_days", 30, minimum=1, maximum=365)
        posts_arguments["time_range"] = f"最近{lookback_days}天"

    search_arguments, metadata = mcp_posts.build_posts_search_arguments(posts_arguments)
    if excluded_handles:
        search_arguments["excluded_x_handles"] = excluded_handles
    metadata.update(
        {
            "mode": mode,
            "intent": intent,
            "target_strategy": target_strategy,
            "explicit_model": explicit_model,
            "excluded_handles": excluded_handles,
            "model_policy": _clean_model_policy(arguments),
            "quality": _clean_quality(arguments),
            "lookback_days": lookback_days,
            "target_status_ids": target_status_ids,
            "routing_warnings": routing_warnings,
            "force_refresh": force_refresh,
            "max_age_seconds": max_age_seconds,
            "reasoning_effort": _clean_reasoning_effort(arguments),
        }
    )
    return search_arguments, metadata


def _clean_max_age_seconds(arguments: Dict[str, Any]) -> Optional[int]:
    value = arguments.get("max_age_seconds")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("max_age_seconds must be a non-negative integer")
    return min(value, 2_592_000)


def _clean_reasoning_effort(arguments: Dict[str, Any]) -> Optional[str]:
    value = arguments.get("reasoning_effort")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reasoning_effort must be a string")
    cleaned = value.strip().lower()
    if cleaned not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("reasoning_effort must be one of low, medium, high, xhigh")
    return cleaned


def _copy_optional(source: Dict[str, Any], target: Dict[str, Any], key: str) -> None:
    if source.get(key) is not None:
        target[key] = source.get(key)


def _clean_retrieve_query(arguments: Dict[str, Any]) -> Optional[str]:
    value = arguments.get("query")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("query must be a string")
    cleaned = value.strip()
    if len(cleaned) > 2000:
        raise ValueError("query must be at most 2000 characters")
    return cleaned or None


def _clean_intent(arguments: Dict[str, Any]) -> str:
    intent = str(arguments.get("intent") or "auto").strip()
    if intent not in INTENTS:
        raise ValueError("intent must be one of auto, research, posts, source_discovery, reaction_tracking, verify_claim")
    return intent


def _clean_model_policy(arguments: Dict[str, Any]) -> str:
    policy = str(arguments.get("model_policy") or "auto").strip()
    if policy not in {"auto", "stable_only", "raw_expanded"}:
        raise ValueError("model_policy must be one of auto, stable_only, raw_expanded")
    return policy


def _clean_model(arguments: Dict[str, Any]) -> Optional[str]:
    value = arguments.get("model")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("model must be a string")
    model = value.strip()
    if not model:
        return None
    if len(model) > RETRIEVE_MODEL_MAX_CHARS:
        raise ValueError(f"model must be at most {RETRIEVE_MODEL_MAX_CHARS} characters")
    return model


def _clean_sort(arguments: Dict[str, Any], *, default: str) -> str:
    sort = str(arguments.get("sort") or default).strip().lower()
    if sort not in {"latest", "relevance"}:
        raise ValueError("sort must be one of latest, relevance")
    return sort


def _clean_bool(arguments: Dict[str, Any], key: str, default: bool) -> bool:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _clean_quality(arguments: Dict[str, Any]) -> Dict[str, Any]:
    value = arguments.get("quality")
    if value is None:
        return {"min_items": 1, "require_status_url": False, "require_original_text": False, "allow_raw_expansion": True}
    if not isinstance(value, dict):
        raise ValueError("quality must be an object")
    unknown = set(value) - {"min_items", "require_status_url", "require_original_text", "allow_raw_expansion"}
    if unknown:
        raise ValueError(f"unsupported quality keys: {', '.join(sorted(unknown))}")
    return {
        "min_items": mcp_posts.clean_int(value, "min_items", 1, minimum=1, maximum=20),
        "require_status_url": _clean_bool(value, "require_status_url", False),
        "require_original_text": _clean_bool(value, "require_original_text", False),
        "allow_raw_expansion": _clean_bool(value, "allow_raw_expansion", True),
    }


def _query_is_primarily_status_targets(query: Optional[str], target_status_ids: list[str]) -> bool:
    if not query or not target_status_ids:
        return False
    import re
    cleaned = re.sub(r"https?://[^\s]+", "", query)
    cleaned = re.sub(r"\b\d{15,20}\b", "", cleaned)
    cleaned = re.sub(r"\b(or|and)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,;|\s]+", "", cleaned)
    return len(cleaned) < 15



def _detect_mode(intent: str, query: Optional[str], handles: list[str], sort: str, target_status_ids: list[str]) -> str:
    query_text = (query or "").lower()
    if intent == "verify_claim":
        return "claim_verification"
    if intent == "reaction_tracking":
        return "reaction_tracking"
    if intent == "source_discovery":
        return "source_discovery"
    if intent == "posts":
        return "structured_posts"
    if intent == "research":
        return "semantic_research"
    if any(marker in query_text for marker in ("证实", "核查", "真假", "verify", "fact-check")):
        return "claim_verification"
    if any(marker in query_text for marker in ("原帖", "信源", "official", "source", "original")):
        return "source_discovery"
    if any(marker in query_text for marker in ("反应", "争议", "reaction", "quote")):
        return "reaction_tracking"
    if target_status_ids and _query_is_primarily_status_targets(query, target_status_ids):
        return "structured_posts"
    if handles and sort == "latest" and intent == "auto":
        return "latest_by_handle"
    return "semantic_research" if query_text else "structured_posts"



def _detect_target_strategy(mode: str, target_status_ids: list[str]) -> str:
    if not target_status_ids:
        return "none"
    if mode in {"claim_verification", "reaction_tracking", "semantic_research", "source_discovery"}:
        return "seed_then_research"
    return "exact_only"

