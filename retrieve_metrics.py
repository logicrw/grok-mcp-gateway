from __future__ import annotations

from collections import defaultdict

_final_status: defaultdict[str, int] = defaultdict(int)
_stage_count: defaultdict[tuple[str, str, str, str], int] = defaultdict(int)
_stage_duration: defaultdict[tuple[str, str, str, str], float] = defaultdict(float)
_timeouts: defaultdict[str, int] = defaultdict(int)
_errors: defaultdict[tuple[str, str], int] = defaultdict(int)
_reasoning_tokens: defaultdict[tuple[str, str], int] = defaultdict(int)
_x_search_calls: defaultdict[tuple[str, str], int] = defaultdict(int)


def _label(value: object) -> str:
    return str(value or "unknown")[:80].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "_")


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
    key = (stage, model, status, reasoning_effort)
    _stage_count[key] += 1
    _stage_duration[key] += max(0.0, duration_seconds)


def record_timeout(timeout_type: str) -> None:
    _timeouts[timeout_type] += 1


def record_error(*, stage: str, kind: str) -> None:
    _errors[(stage, kind)] += 1


def record_usage(*, stage: str, model: str, reasoning_tokens: int, x_search_calls: int) -> None:
    key = (stage, model)
    _reasoning_tokens[key] += max(0, reasoning_tokens)
    _x_search_calls[key] += max(0, x_search_calls)


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
        stage, model, status, effort = (_label(value) for value in key)
        labels = f'stage="{stage}",model="{model}",status="{status}",reasoning_effort="{effort}"'
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
    return lines


def _usage_lines(name: str, values: defaultdict[tuple[str, str], int]) -> list[str]:
    lines = [
        f"# HELP mcp_x_retrieve_{name}_total Parsed xAI Responses usage",
        f"# TYPE mcp_x_retrieve_{name}_total counter",
    ]
    for (stage, model), count in sorted(values.items()):
        lines.append(
            f'mcp_x_retrieve_{name}_total{{stage="{_label(stage)}",model="{_label(model)}"}} {count}'
        )
    return lines
