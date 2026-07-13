from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional

import httpx

import xai_responses
from retrieve_metrics import record_error, record_stage, record_timeout, record_usage
from retrieve_policy import RequestBudget, model_supports_reasoning_effort

SearchCaller = Callable[[Dict[str, Any]], Awaitable[xai_responses.ResponsesResult]]


class StageTimeout(asyncio.TimeoutError):
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        super().__init__(f"{boundary} timeout")


async def run_search_stage(
    search: SearchCaller,
    arguments: Dict[str, Any],
    *,
    stage: str,
    budget: RequestBudget,
    reasoning_effort: Optional[str],
) -> xai_responses.ResponsesResult:
    stage_arguments = dict(arguments)
    model = str(stage_arguments.get("model") or "unknown")
    effective_effort = reasoning_effort if reasoning_effort and model_supports_reasoning_effort(model) else "none"
    if effective_effort == "none":
        stage_arguments.pop("_reasoning_effort", None)
    else:
        stage_arguments["_reasoning_effort"] = effective_effort

    timeout = budget.stage_timeout()
    if timeout <= 0:
        record_timeout("total")
        record_error(stage=stage, kind="total_timeout")
        record_stage(
            stage=stage,
            model=model,
            status="timeout",
            reasoning_effort=effective_effort,
            duration_seconds=0.0,
        )
        raise StageTimeout("total")

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(search(stage_arguments), timeout=timeout)
    except asyncio.TimeoutError as exc:
        boundary = "total" if budget.remaining() <= 0 else "stage"
        _record_stage_failure(stage, model, effective_effort, started, boundary)
        raise StageTimeout(boundary) from exc
    except httpx.TimeoutException as exc:
        _record_stage_failure(stage, model, effective_effort, started, "upstream")
        raise StageTimeout("upstream") from exc
    except Exception as exc:
        record_error(stage=stage, kind=_error_kind(exc))
        record_stage(
            stage=stage,
            model=model,
            status="error",
            reasoning_effort=effective_effort,
            duration_seconds=time.monotonic() - started,
        )
        raise

    record_stage(
        stage=stage,
        model=result.model,
        status="success",
        reasoning_effort=effective_effort,
        duration_seconds=time.monotonic() - started,
    )
    reasoning_tokens, x_search_calls = xai_responses.parse_usage_metrics(result.usage)
    record_usage(
        stage=stage,
        model=result.model,
        reasoning_tokens=reasoning_tokens,
        x_search_calls=x_search_calls,
    )
    return result


def _record_stage_failure(stage: str, model: str, effort: str, started: float, boundary: str) -> None:
    record_timeout(boundary)
    record_error(stage=stage, kind=f"{boundary}_timeout")
    record_stage(
        stage=stage,
        model=model,
        status="timeout",
        reasoning_effort=effort,
        duration_seconds=time.monotonic() - started,
    )


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, xai_responses.ResponsesAPIError):
        return f"upstream_{exc.status_code}"
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", exc.__class__.__name__).lower()
    return name[:40] or "unknown"
