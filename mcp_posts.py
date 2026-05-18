"""Structured post-extraction helpers for the MCP X Search surface."""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

POSTS_TOOL_NAME = "x_posts"
LATEST_POSTS_TOOL_NAME = "x_latest_posts"
SOURCE_LIMIT = "xAI x_search generated extraction, not official X API timeline"


def posts_tool_definition(default_model: str) -> Dict[str, Any]:
    today = date.today().isoformat()
    return {
        "name": POSTS_TOOL_NAME,
        "description": (
            "Extract X posts through xAI's x_search backend using structured filters. Use this when the user wants "
            "posts by handle, topic, flexible time range, or best-effort engagement filters. "
            f"Current local date: {today}. This is not an official X API timeline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "handles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional author handles, with or without @. Supports up to 10 handles.",
                },
                "query": {"type": "string", "description": "Optional topic or keyword filter."},
                "time_range": {
                    "type": "string",
                    "description": "Optional natural-language time window. Defaults to the last 30 days.",
                },
                "from_date": {"type": "string", "description": "Optional ISO8601 search start date."},
                "to_date": {"type": "string", "description": "Optional inclusive ISO8601 search end date."},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
                "sort": {"type": "string", "enum": ["latest", "relevance", "popular"]},
                "include_replies": {"type": "boolean"},
                "include_reposts": {"type": "boolean"},
                "engagement_filter": {
                    "type": "object",
                    "properties": {
                        "min_likes": {"type": "integer", "minimum": 0},
                        "min_reposts": {"type": "integer", "minimum": 0},
                        "min_replies": {"type": "integer", "minimum": 0},
                        "min_views": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "model": {"type": "string", "description": f"Optional xAI model. Defaults to {default_model}."},
            },
            "additionalProperties": False,
        },
    }


def latest_posts_tool_definition(default_model: str) -> Dict[str, Any]:
    today = date.today().isoformat()
    return {
        "name": LATEST_POSTS_TOOL_NAME,
        "description": (
            "Shortcut for x_posts when the user asks for latest posts from a single account. "
            f"Current local date: {today}. Returns extraction-oriented text, not a verified X API timeline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "Single X handle, with or without @."},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
                "lookback_days": {"type": "integer", "minimum": 1, "maximum": 365},
                "from_date": {"type": "string", "description": "Optional ISO8601 search start date."},
                "to_date": {"type": "string", "description": "Optional inclusive ISO8601 search end date."},
                "include_replies": {"type": "boolean"},
                "model": {"type": "string", "description": f"Optional xAI model. Defaults to {default_model}."},
            },
            "required": ["handle"],
            "additionalProperties": False,
        },
    }


def clean_handle_list(arguments: Dict[str, Any], key: str) -> Optional[list[str]]:
    handles = arguments.get(key)
    if handles is None:
        return None
    if not isinstance(handles, list) or not all(isinstance(handle, str) for handle in handles):
        raise ValueError(f"{key} must be an array of strings")
    cleaned = [handle.strip().lstrip("@") for handle in handles if handle.strip()]
    if len(cleaned) > 10:
        raise ValueError(f"{key} supports at most 10 handles")
    return cleaned or None


def clean_single_handle(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    handle = value.strip().lstrip("@")
    if not handle:
        raise ValueError(f"{key} is required")
    if "," in handle or "/" in handle or " " in handle:
        raise ValueError(f"{key} must be a single X handle, for example '0xlogicrw'")
    return handle


def clean_int(arguments: Dict[str, Any], key: str, default: int, *, minimum: int, maximum: int) -> int:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def clean_iso8601_date(arguments: Dict[str, Any], key: str, *, inclusive_end: bool = False) -> Optional[str]:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be an ISO8601 date string")
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        if "T" in cleaned:
            datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        else:
            parsed_date = date.fromisoformat(cleaned)
            if inclusive_end:
                return (parsed_date + timedelta(days=1)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO8601 date string, for example '2026-05-18'") from exc
    return cleaned


def _clean_bool(arguments: Dict[str, Any], key: str, default: bool) -> bool:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _clean_sort(arguments: Dict[str, Any]) -> str:
    sort = str(arguments.get("sort") or "latest").strip().lower()
    if sort not in {"latest", "relevance", "popular"}:
        raise ValueError("sort must be one of latest, relevance, popular")
    return sort


def _clean_query(arguments: Dict[str, Any]) -> Optional[str]:
    value = arguments.get("query")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("query must be a string")
    return value.strip() or None


def _clean_engagement_filter(arguments: Dict[str, Any]) -> Dict[str, int]:
    value = arguments.get("engagement_filter")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("engagement_filter must be an object")
    allowed = {"min_likes", "min_reposts", "min_replies", "min_views"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unsupported engagement_filter keys: {', '.join(sorted(unknown))}")
    cleaned: Dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise ValueError(f"engagement_filter.{key} must be a non-negative integer")
        cleaned[key] = raw
    return cleaned


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def _parse_iso_date_fragment(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def compile_time_range(arguments: Dict[str, Any], *, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    explicit_from = clean_iso8601_date(arguments, "from_date")
    explicit_to = clean_iso8601_date(arguments, "to_date")
    raw_value = arguments.get("time_range")
    if raw_value is not None and not isinstance(raw_value, str):
        raise ValueError("time_range must be a string")
    original = raw_value.strip() if isinstance(raw_value, str) else None

    def compiled(from_value: Optional[date], to_value: Optional[date], *, source: str, assumption: str) -> Dict[str, Any]:
        return {
            "input": original,
            "from_date": explicit_from or (from_value.isoformat() if from_value else None),
            "to_date": explicit_to or (to_value.isoformat() if to_value else None),
            "compiled": True,
            "source": source,
            "assumption": assumption,
        }

    if explicit_from or explicit_to:
        return compiled(
            _parse_iso_date_fragment(explicit_from),
            _parse_iso_date_fragment(explicit_to),
            source="explicit_dates",
            assumption="Explicit from_date/to_date override time_range.",
        )
    if not original:
        return compiled(today - timedelta(days=30), today, source="default", assumption="Defaulted to the last 30 days.")

    compact = re.sub(r"\s+", "", original.lower())
    if compact in {"不限", "不限制", "全部", "任意时间", "any", "all", "unlimited", "nolimit"}:
        return compiled(None, None, source="natural_language", assumption="User requested no time filter.")

    iso_range = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:到|至|~|—|-|through|to)\s*(\d{4}-\d{2}-\d{2})", original.lower())
    if iso_range:
        start = _parse_iso_date_fragment(iso_range.group(1))
        end = _parse_iso_date_fragment(iso_range.group(2))
        if start and end:
            return compiled(start, end, source="natural_language", assumption="Parsed explicit ISO date range.")

    month_day = re.search(r"(\d{1,2})月(\d{1,2})日?\s*(?:到|至|~|—|-)\s*(\d{1,2})月(\d{1,2})日?", compact)
    if month_day:
        start = date(today.year, int(month_day.group(1)), int(month_day.group(2)))
        end = date(today.year, int(month_day.group(3)), int(month_day.group(4)))
        return compiled(start, end, source="natural_language", assumption=f"Parsed month/day range using local year {today.year}.")

    year_month = re.search(r"(\d{4})年(\d{1,2})月", compact)
    if year_month:
        start, end = _month_bounds(int(year_month.group(1)), int(year_month.group(2)))
        return compiled(start, end, source="natural_language", assumption="Parsed Chinese year-month range.")

    iso_month = re.fullmatch(r"(\d{4})-(\d{1,2})", compact)
    if iso_month:
        start, end = _month_bounds(int(iso_month.group(1)), int(iso_month.group(2)))
        return compiled(start, end, source="natural_language", assumption="Parsed ISO year-month range.")

    plain_month = re.fullmatch(r"(\d{1,2})月", compact)
    if plain_month:
        start, end = _month_bounds(today.year, int(plain_month.group(1)))
        return compiled(start, end, source="natural_language", assumption=f"Parsed month using local year {today.year}.")

    n_days = re.search(r"(?:最近|过去|近|last|past)(\d{1,3})(?:天|days?)", compact)
    if n_days:
        days = int(n_days.group(1))
        if days < 1 or days > 365:
            raise ValueError("time_range day window must be between 1 and 365 days")
        return compiled(today - timedelta(days=days), today, source="natural_language", assumption="Parsed rolling day window.")

    week_start = today - timedelta(days=today.weekday())
    ranges = {
        ("今天", "今日", "today"): (today, today, "Parsed today using local date."),
        ("昨天", "昨日", "yesterday"): (today - timedelta(days=1), today - timedelta(days=1), "Parsed yesterday using local date."),
        ("本周", "这周", "thisweek"): (week_start, today, "Weeks start on Monday; current week ends today."),
        ("上周", "lastweek"): (week_start - timedelta(days=7), week_start - timedelta(days=1), "Weeks start on Monday."),
        ("上上周", "前两周", "weekbeforelast"): (week_start - timedelta(days=14), week_start - timedelta(days=8), "Weeks start on Monday."),
    }
    for names, (start, end, assumption) in ranges.items():
        if compact in names:
            return compiled(start, end, source="natural_language", assumption=assumption)
    if compact in {"本月", "这个月", "thismonth"}:
        start, _ = _month_bounds(today.year, today.month)
        return compiled(start, today, source="natural_language", assumption="Current month ends today.")
    if compact in {"上个月", "lastmonth"}:
        last_prev_month = date(today.year, today.month, 1) - timedelta(days=1)
        start, end = _month_bounds(last_prev_month.year, last_prev_month.month)
        return compiled(start, end, source="natural_language", assumption="Parsed previous calendar month.")

    return {
        "input": original,
        "from_date": None,
        "to_date": None,
        "compiled": False,
        "source": "unparsed_natural_language",
        "assumption": "Could not compile time_range; passed the original time phrase into the xAI search prompt.",
    }


def _engagement_filter_text(engagement_filter: Dict[str, int]) -> str:
    if not engagement_filter:
        return "No engagement threshold requested."
    labels = {"min_likes": "likes", "min_reposts": "reposts", "min_replies": "replies", "min_views": "views"}
    requirements = [f"{labels[key]} >= {value}" for key, value in engagement_filter.items()]
    return "Best-effort engagement filter: " + ", ".join(requirements) + ". Only trust visible metrics."


def build_posts_search_arguments(arguments: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    handles = clean_handle_list(arguments, "handles")
    query_filter = _clean_query(arguments)
    if not handles and not query_filter:
        raise ValueError("x_posts requires at least one handle or a query")

    count = clean_int(arguments, "count", 10, minimum=1, maximum=20)
    sort = _clean_sort(arguments)
    include_replies = _clean_bool(arguments, "include_replies", True)
    include_reposts = _clean_bool(arguments, "include_reposts", True)
    engagement_filter = _clean_engagement_filter(arguments)
    compiled_range = compile_time_range(arguments)

    handle_text = (
        "Search only posts authored by " + ", ".join(f"@{handle}" for handle in handles) + "."
        if handles
        else "Search posts from any author that match the requested topic."
    )
    topic_text = f"Topic/keyword filter: {query_filter}." if query_filter else "No topic filter requested."
    time_text = _time_constraint_text(compiled_range)
    reply_rule = "Include replies when authored by matching handles." if include_replies else "Exclude replies."
    repost_rule = "Include reposts when xAI can distinguish them." if include_reposts else "Exclude reposts when xAI can distinguish them."
    sort_text = {
        "latest": "Sort results in reverse chronological order.",
        "relevance": "Sort results by relevance to the query while preserving timestamps.",
        "popular": "Sort results by visible engagement when possible; otherwise fall back to relevance.",
    }[sort]

    query = _posts_prompt(
        handle_text=handle_text,
        topic_text=topic_text,
        time_text=time_text,
        count=count,
        sort_text=sort_text,
        reply_rule=reply_rule,
        repost_rule=repost_rule,
        engagement_text=_engagement_filter_text(engagement_filter),
        handles=handles or [],
        query_filter=query_filter,
        compiled_range=compiled_range,
        sort=sort,
        include_replies=include_replies,
        include_reposts=include_reposts,
        engagement_filter=engagement_filter,
    )

    search_arguments: Dict[str, Any] = {"query": query, "model": arguments.get("model")}
    if handles:
        search_arguments["allowed_x_handles"] = handles
    if compiled_range["from_date"]:
        search_arguments["from_date"] = compiled_range["from_date"]
    if compiled_range["to_date"]:
        search_arguments["to_date"] = compiled_range["to_date"]

    return search_arguments, {
        "compiled_time_range": compiled_range,
        "handles": handles or [],
        "query": query_filter,
        "count": count,
        "sort": sort,
        "engagement_filter": engagement_filter,
    }


def build_latest_posts_search_arguments(arguments: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    handle = clean_single_handle(arguments, "handle")
    count = clean_int(arguments, "count", 10, minimum=1, maximum=20)
    lookback_days = clean_int(arguments, "lookback_days", 30, minimum=1, maximum=365)
    posts_arguments: Dict[str, Any] = {
        "handles": [handle],
        "count": count,
        "sort": "latest",
        "include_replies": arguments.get("include_replies", True),
        "include_reposts": True,
        "model": arguments.get("model"),
    }
    if arguments.get("from_date") is not None:
        posts_arguments["from_date"] = arguments.get("from_date")
    if arguments.get("to_date") is not None:
        posts_arguments["to_date"] = arguments.get("to_date")
    if arguments.get("from_date") is None and arguments.get("to_date") is None:
        posts_arguments["time_range"] = f"最近{lookback_days}天"
    return build_posts_search_arguments(posts_arguments)


def _time_constraint_text(compiled_range: Dict[str, Any]) -> str:
    if compiled_range["from_date"] and compiled_range["to_date"]:
        return f"Search window: {compiled_range['from_date']} through {compiled_range['to_date']}, inclusive."
    if compiled_range["from_date"]:
        return f"Search window starts at {compiled_range['from_date']}."
    if compiled_range["to_date"]:
        return f"Search window ends at {compiled_range['to_date']}, inclusive."
    if compiled_range["input"]:
        return f"Time range could not be compiled. Use this user-provided time phrase: {compiled_range['input']}."
    return "No time filter requested."


def _posts_prompt(**values: Any) -> str:
    return f"""
Extract X posts with the following filters.

Hard constraints:
- {values['handle_text']}
- {values['topic_text']}
- {values['time_text']}
- Return up to {values['count']} posts.
- {values['sort_text']}
- {values['reply_rule']}
- {values['repost_rule']}
- {values['engagement_text']}
- Preserve each post's text exactly as available. Do not translate, summarize, retitle, or infer topics.
- Do not invent metrics, links, dates, authors, or engagement numbers not present in the post text.
- If exact text, timestamp, URL, author, or metrics are unavailable, use null or truncated instead of guessing.

Return only compact JSON with this shape:
{{
  "query_compiled": {{
    "handles": {json.dumps(values['handles'], ensure_ascii=False)},
    "query": {json.dumps(values['query_filter'], ensure_ascii=False)},
    "time_range": {json.dumps(values['compiled_range'], ensure_ascii=False)},
    "count": {values['count']},
    "sort": "{values['sort']}",
    "include_replies": {str(values['include_replies']).lower()},
    "include_reposts": {str(values['include_reposts']).lower()},
    "engagement_filter": {json.dumps(values['engagement_filter'], ensure_ascii=False)}
  }},
  "source_limit": "{SOURCE_LIMIT}",
  "filter_reliability": {{
    "handles": "{'strong' if values['handles'] else 'none'}",
    "time_range": "{'medium' if values['compiled_range']['compiled'] else 'best_effort'}",
    "query": "{'medium' if values['query_filter'] else 'none'}",
    "engagement": "{'best_effort' if values['engagement_filter'] else 'none'}"
  }},
  "posts": [
    {{
      "created_at": "ISO8601 timestamp or null",
      "author": "handle or null",
      "text": "exact post text as available",
      "url": "post URL or null",
      "metrics": {{"views": null, "likes": null, "reposts": null, "replies": null}},
      "truncated": true,
      "warnings": []
    }}
  ]
}}
""".strip()


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    elif not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def posts_result(tool_name: str, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    parsed = parse_json_object(text)
    if parsed is None:
        structured = {
            "source_limit": SOURCE_LIMIT,
            "parse_warning": "xAI did not return valid JSON; read raw_text as best-effort extraction.",
            "compiled_time_range": metadata["compiled_time_range"],
            "raw_text": text,
        }
    else:
        structured = parsed
        structured.setdefault("source_limit", SOURCE_LIMIT)
        structured.setdefault("compiled_time_range", metadata["compiled_time_range"])

    body = json.dumps(structured, ensure_ascii=False, indent=2)
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Tool: {tool_name}\n"
                    "Do not summarize or rewrite this tool result. Treat missing, null, warning, or truncated fields as missing data.\n\n"
                    f"{body}"
                ),
            }
        ],
        "structuredContent": structured,
        "isError": False,
    }
