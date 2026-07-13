import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieve_policy import RequestBudget, model_supports_reasoning_effort, reasoning_effort_for
from retrieve_stages import StageTimeout, run_search_stage
from xai_responses import ResponsesResult


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"mode": "latest_by_handle", "intent": "auto"}, "low"),
        ({"mode": "structured_posts", "intent": "posts"}, "low"),
        ({"mode": "semantic_research", "intent": "research"}, "medium"),
        ({"mode": "source_discovery", "intent": "verify_claim"}, "high"),
        ({"mode": "source_discovery", "target_status_ids": ["2071385784154759468"]}, "low"),
    ],
)
def test_reasoning_effort_for_route(metadata, expected):
    assert reasoning_effort_for(metadata) == expected


def test_reasoning_effort_is_only_enabled_for_grok_4_5():
    assert model_supports_reasoning_effort("grok-4.5") is True
    assert model_supports_reasoning_effort("grok-composer-2.5-fast") is False
    assert model_supports_reasoning_effort("custom-model") is False


def test_stage_timeout_wraps_entire_search_call():
    async def slow_search(arguments):
        await asyncio.sleep(0.05)
        return ResponsesResult("ok", {}, [], None, str(arguments["model"]))

    async def run():
        with pytest.raises(StageTimeout):
            await run_search_stage(
                slow_search,
                {"model": "grok-4.5"},
                stage="stable_extract",
                budget=RequestBudget(total_seconds=0.01),
                reasoning_effort="low",
            )

    asyncio.run(run())


def test_unsupported_model_does_not_receive_reasoning_effort():
    seen = {}

    async def fake_search(arguments):
        seen.update(arguments)
        return ResponsesResult("ok", {}, [], None, str(arguments["model"]))

    asyncio.run(
        run_search_stage(
            fake_search,
            {"model": "grok-composer-2.5-fast"},
            stage="raw_expansion",
            budget=RequestBudget(total_seconds=1),
            reasoning_effort="high",
        )
    )

    assert "_reasoning_effort" not in seen
