import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieve import x_search
from retrieve.policy import (
    RequestBudget,
    build_xai_responses_payload,
    model_supports_reasoning_effort,
    resolve_plan,
)
from retrieve.stages import StageTimeout, run_search_stage
from xai_responses import ResponsesResult


def test_reasoning_effort_is_only_enabled_for_known_reasoning_models():
    assert model_supports_reasoning_effort("grok-4.5") is True
    assert model_supports_reasoning_effort("grok-4.6") is True
    assert model_supports_reasoning_effort("grok-4.6-latest") is True
    assert model_supports_reasoning_effort("grok-composer-2.5-fast") is False
    assert model_supports_reasoning_effort("custom-model") is False


def test_xhigh_is_sent_on_grok_46_when_explicit():
    payload = build_xai_responses_payload(
        query="claim",
        x_search_tool={"type": "x_search"},
        model="grok-4.6",
        max_turns=3,
        store=False,
        structured_output=True,
        reasoning_effort="xhigh",
    )
    assert payload["reasoning"] == {"effort": "xhigh"}

    production = x_search._x_search_payload(
        {
            "query": "claim",
            "model": "grok-4.6",
            "_structured_output": True,
            "_reasoning_effort": "xhigh",
        }
    )
    assert production["reasoning"] == {"effort": "xhigh"}

    dropped_on_45 = build_xai_responses_payload(
        query="claim",
        x_search_tool={"type": "x_search"},
        model="grok-4.5",
        reasoning_effort="xhigh",
    )
    assert "reasoning" not in dropped_on_45


def test_smart_defaults_are_medium_and_verify_claim_is_high():
    research = resolve_plan(
        {
            "intent": "research",
            "mode": "semantic_research",
            "query": "Grok 4.6 architecture",
        }
    )
    assert research.reasoning_effort == "medium"

    verify = resolve_plan(
        {
            "intent": "verify_claim",
            "mode": "claim_verification",
            "query": "Did xAI announce Grok 4.6?",
        }
    )
    assert verify.reasoning_effort == "high"


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
