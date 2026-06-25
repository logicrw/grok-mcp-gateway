from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict

from error_sanitizer import sanitize_text
import xai_responses
from retrieve_payload import assemble_payload, merge_raw_payload, raw_decision, raw_expansion_query
from retrieve_routing import build_retrieve_search_arguments
from retrieve_schema import RAW_MODEL, RETRIEVE_TOOL_NAME, retrieve_tool_definition

SearchCaller = Callable[[Dict[str, Any]], Awaitable[xai_responses.ResponsesResult]]
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
    return lines


async def call_retrieve(arguments: Dict[str, Any], *, search: SearchCaller) -> Dict[str, Any]:
    search_arguments, metadata = build_retrieve_search_arguments(arguments)
    stable_result = await search(search_arguments)
    stable_payload = assemble_payload(stable_result, metadata, stage_name="stable_extract")

    run_raw, raw_reason = raw_decision(stable_payload, metadata)
    _quality_gate_counts["fail" if run_raw else "pass"] += 1

    if run_raw:
        raw_arguments = dict(search_arguments)
        raw_arguments["model"] = RAW_MODEL
        raw_arguments["query"] = raw_expansion_query(str(raw_arguments["query"]))
        try:
            raw_result = await search(raw_arguments)
        except Exception as exc:
            _raw_expansion_counts[(raw_reason, "failed")] += 1
            stable_payload["warnings"].append(f"raw expansion failed: {sanitize_text(exc)}")
            stable_payload["retrieval_stages"].append({"name": "raw_expansion", "model": RAW_MODEL, "status": "failed"})
        else:
            _raw_expansion_counts[(raw_reason, "success")] += 1
            merge_raw_payload(stable_payload, raw_result, metadata)
    else:
        _raw_expansion_counts[(raw_reason, "skipped")] += 1
        stable_payload["retrieval_stages"].append(
            {"name": "raw_expansion", "model": RAW_MODEL, "status": "skipped", "reason": raw_reason}
        )

    body = json.dumps(stable_payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": body}], "structuredContent": stable_payload, "isError": False}
