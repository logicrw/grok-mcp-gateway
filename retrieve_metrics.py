from __future__ import annotations

from collections import defaultdict

_final_status: defaultdict[str, int] = defaultdict(int)
_stage_count: defaultdict[tuple[str, str, str, str], int] = defaultdict(int)
_stage_duration: defaultdict[tuple[str, str, str, str], float] = defaultdict(float)
_timeouts: defaultdict[str, int] = defaultdict(int)
_errors: defaultdict[tuple[str, str], int] = defaultdict(int)
_reasoning_tokens: defaultdict[tuple[str, str], int] = defaultdict(int)
_x_search_calls: defaultdict[tuple[str, str], int] = defaultdict(int)
_cost_ticks: defaultdict[tuple[str, str], int] = defaultdict(int)
_route_count: defaultdict[tuple[str, str, str], int] = defaultdict(int)


def _label(value: object) -> str:
    escaped = str(value or "unknown").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "_")
    return escaped[:80].rstrip("\\")


def record_retrieval_status(status: str) -> None:
    _final_status[status] += 1


def record_stage(
    *,
    stage: str,
    model: str,
    status: str,
    reasoning_effort: str,
    duration_seconds: float,
) -> None:
    key = (stage, _model_role(stage, model), status, reasoning_effort)
    _stage_count[key] += 1
    _stage_duration[key] += max(0.0, duration_seconds)


def record_timeout(timeout_type: str) -> None:
    _timeouts[timeout_type] += 1


def record_error(*, stage: str, kind: str) -> None:
    _errors[(stage, kind)] += 1


def record_usage(
    *,
    stage: str,
    model: str,
    reasoning_tokens: int,
    x_search_calls: int,
    cost_ticks: int = 0,
) -> None:
    key = (stage, _model_role(stage, model))
    _reasoning_tokens[key] += max(0, reasoning_tokens)
    _x_search_calls[key] += max(0, x_search_calls)
    _cost_ticks[key] += max(0, cost_ticks)


def record_route(*, lane: str, objective_mode: str, escalated: bool = False) -> None:
    key = (lane, objective_mode, "true" if escalated else "false")
    _route_count[key] += 1


def metrics_lines() -> list[str]:
    lines = [
        "# HELP mcp_x_retrieve_final_status_total Final x_retrieve results by retrieval_status",
        "# TYPE mcp_x_retrieve_final_status_total counter",
    ]
    for status in ("ok", "empty", "no_match", "degraded", "error"):
        lines.append(f'mcp_x_retrieve_final_status_total{{status="{status}"}} {_final_status[status]}')
    lines.extend(
        [
            "# HELP mcp_x_retrieve_stage_total Retrieval stages by bounded labels",
            "# TYPE mcp_x_retrieve_stage_total counter",
            "# HELP mcp_x_retrieve_stage_duration_seconds_total Retrieval stage wall time",
            "# TYPE mcp_x_retrieve_stage_duration_seconds_total counter",
        ]
    )
    for key in sorted(_stage_count):
        stage, model_role, status, effort = (_label(value) for value in key)
        labels = f'stage="{stage}",model_role="{model_role}",status="{status}",reasoning_effort="{effort}"'
        lines.append(f"mcp_x_retrieve_stage_total{{{labels}}} {_stage_count[key]}")
        lines.append(f"mcp_x_retrieve_stage_duration_seconds_total{{{labels}}} {_stage_duration[key]}")
    lines.extend(
        [
            "# HELP mcp_x_retrieve_timeout_total Retrieval timeouts by boundary",
            "# TYPE mcp_x_retrieve_timeout_total counter",
        ]
    )
    for timeout_type, count in sorted(_timeouts.items()):
        lines.append(f'mcp_x_retrieve_timeout_total{{type="{_label(timeout_type)}"}} {count}')
    lines.extend(
        [
            "# HELP mcp_x_retrieve_error_total Retrieval errors by stage and bounded kind",
            "# TYPE mcp_x_retrieve_error_total counter",
        ]
    )
    for (stage, kind), count in sorted(_errors.items()):
        lines.append(
            f'mcp_x_retrieve_error_total{{stage="{_label(stage)}",kind="{_label(kind)}"}} {count}'
        )
    lines.extend(_usage_lines("reasoning_tokens", _reasoning_tokens))
    lines.extend(_usage_lines("x_search_calls", _x_search_calls))
    if any(_cost_ticks.values()):
        lines.extend(_usage_lines("cost_usd_ticks", _cost_ticks))
    return lines


def _usage_lines(name: str, values: defaultdict[tuple[str, str], int]) -> list[str]:
    lines = [
        f"# HELP mcp_x_retrieve_{name}_total Parsed xAI Responses usage",
        f"# TYPE mcp_x_retrieve_{name}_total counter",
    ]
    for (stage, model_role), count in sorted(values.items()):
        lines.append(
            f'mcp_x_retrieve_{name}_total{{stage="{_label(stage)}",model_role="{_label(model_role)}"}} {count}'
        )
    return lines


def _model_role(stage: str, model: str) -> str:
    if stage == "raw_expansion":
        return "raw"
    if stage == "public_oembed":
        return "public_oembed"
    if stage in {"fast_extract", "target_fast_fallback"}:
        return "fast"
    if stage in {"smart_extract", "smart_escalation", "target_smart_fallback"}:
        return "smart"
    if model == "unknown":
        return "unknown"
    return "stable"
