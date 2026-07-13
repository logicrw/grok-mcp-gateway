from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict

RETRIEVE_TOOL_NAME = "x_retrieve"
SCHEMA_VERSION = "x_retrieve.v1"
BACKEND = "xai_x_search_orchestrated"
SOURCE_LIMIT = "Generated retrieval via xAI x_search. Not official X API timeline."
RETRIEVE_QUERY_MAX_CHARS = 2000
RETRIEVE_MODEL_MAX_CHARS = 128
RAW_MODEL = (
    os.getenv("GROK_PROXY_RETRIEVE_RAW_MODEL")
    or os.getenv("GROK_PROXY_MCP_RAW_MODEL")
    or "grok-composer-2.5-fast"
).strip() or "grok-composer-2.5-fast"

RETRIEVE_ARGUMENT_KEYS = {
    "query",
    "intent",
    "handles",
    "excluded_handles",
    "time_range",
    "from_date",
    "to_date",
    "count",
    "sort",
    "lookback_days",
    "include_replies",
    "include_reposts",
    "best_effort_filters",
    "quality",
    "model_policy",
    "model",
}
INTENTS = {"auto", "research", "posts", "source_discovery", "reaction_tracking", "verify_claim"}
MODES = {"latest_by_handle", "structured_posts", "semantic_research", "source_discovery", "reaction_tracking"}


def retrieve_tool_definition(default_model: str) -> Dict[str, Any]:
    today = date.today().isoformat()
    return {
        "name": RETRIEVE_TOOL_NAME,
        "description": (
            "Default X retrieval tool for semantic X research, structured post retrieval, source discovery, "
            "reaction tracking, and latest-by-handle requests. Use this for normal X/Twitter retrieval. "
            "Pass explicit X status URLs or 15-20 digit status IDs in query to retrieve target posts. "
            f"Current local date: {today}. For screenshots, pass OCR-derived text in query; do not pass images."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": RETRIEVE_QUERY_MAX_CHARS,
                    "description": (
                        "Natural-language topic, claim, source clue, OCR-derived text, research request, "
                        "or explicit X status URL/status ID target."
                    ),
                },
                "intent": {"type": "string", "enum": sorted(INTENTS), "description": "Optional routing hint. Defaults to auto."},
                "handles": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": {"type": "string", "pattern": "^@?[A-Za-z0-9_]{1,15}$"},
                    "description": "Optional author handles. For latest-by-handle, combine with sort=latest.",
                },
                "excluded_handles": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "string", "pattern": "^@?[A-Za-z0-9_]{1,15}$"},
                    "description": "Optional handles to exclude when handles is not set.",
                },
                "time_range": {"type": "string", "description": "Optional natural-language time window."},
                "from_date": {"type": "string", "description": "Optional ISO8601 search start date."},
                "to_date": {
                    "type": "string",
                    "description": "Optional inclusive ISO8601 search end date. Date-only values are passed through unchanged.",
                },
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
                "sort": {"type": "string", "enum": ["latest", "relevance"]},
                "lookback_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "description": "Default rolling window for handles+sort=latest when explicit dates are absent.",
                },
                "include_replies": {"type": "boolean"},
                "include_reposts": {"type": "boolean"},
                "best_effort_filters": {
                    "type": "object",
                    "properties": {
                        "min_likes": {"type": "integer", "minimum": 0},
                        "min_reposts": {"type": "integer", "minimum": 0},
                        "min_replies": {"type": "integer", "minimum": 0},
                        "min_views": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "quality": {
                    "type": "object",
                    "properties": {
                        "min_items": {"type": "integer", "minimum": 1, "maximum": 20},
                        "require_status_url": {"type": "boolean"},
                        "require_original_text": {"type": "boolean"},
                        "allow_raw_expansion": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "model_policy": {"type": "string", "enum": ["auto", "stable_only", "raw_expanded"]},
                "model": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": RETRIEVE_MODEL_MAX_CHARS,
                    "description": f"Optional stable xAI model. Defaults to {default_model}.",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": _retrieve_output_schema(),
    }


def _retrieve_output_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "schema_version",
            "tool",
            "backend",
            "timeline_verified",
            "source_limit",
            "mode",
            "request",
            "retrieval_stages",
            "retrieval_status",
            "models_used",
            "warnings",
            "filter_reliability",
            "sources",
            "source_extraction_status",
            "posts",
            "items",
            "groups",
        ],
        "additionalProperties": True,
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "tool": {"const": RETRIEVE_TOOL_NAME},
            "backend": {"const": BACKEND},
            "timeline_verified": {"const": False},
            "source_limit": {"type": "string"},
            "mode": {"enum": sorted(MODES)},
            "request": {"type": "object"},
            "retrieval_stages": {"type": "array"},
            "retrieval_status": {"enum": ["degraded", "empty", "error", "no_match", "ok"]},
            "models_used": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "target_match": {
                "type": "object",
                "properties": {
                    "requested": {"type": "array", "items": {"type": "string"}},
                    "matched": {"type": "array", "items": {"type": "string"}},
                    "missing": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
            "filter_reliability": {"type": "object"},
            "sources": {"type": "array"},
            "source_extraction_status": {"enum": ["not_available", "extracted_unmapped", "citation_backed"]},
            "posts": {"type": "array"},
            "items": {"type": "array"},
            "groups": {"type": "object"},
        },
    }
