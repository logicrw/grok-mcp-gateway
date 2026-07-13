import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_x_search
import xai_responses
from x_oembed import OEmbedResult


async def _empty_oembed(status_ids, handles):
    return OEmbedResult(posts=[], warnings=[])


def _call(arguments):
    return asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": arguments},
            }
        )
    )


def test_exact_targets_use_one_batched_model_fallback(monkeypatch):
    first = "2071385784154759468"
    second = "2071323738201837785"
    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        if len(calls) == 1:
            return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, "grok-4.5")
        return xai_responses.ResponsesResult(
            '{"posts":['
            f'{{"text":"first","url":"https://x.com/xai/status/{first}"}},'
            f'{{"text":"second","url":"https://x.com/xai/status/{second}"}}]}}',
            {},
            [],
            None,
            "grok-4.5",
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(mcp_x_search.mcp_retrieve, "fetch_oembed_posts", _empty_oembed)

    response = _call({"query": f"{first} {second}"})
    structured = response["result"]["structuredContent"]

    assert len(calls) == 2
    assert first in calls[1]["query"] and second in calls[1]["query"]
    assert calls[0]["_reasoning_effort"] == "low"
    assert calls[1]["_reasoning_effort"] == "low"
    assert structured["target_match"]["missing"] == []
    assert [stage["name"] for stage in structured["retrieval_stages"]] == [
        "stable_extract",
        "raw_expansion",
        "public_oembed",
        "target_fallback",
    ]


def test_target_extraction_cap_is_visible_in_result(monkeypatch):
    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, "grok-4.5")

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(mcp_x_search.mcp_retrieve, "fetch_oembed_posts", _empty_oembed)
    query = " ".join(str(2071385784154759460 + index) for index in range(7))

    response = _call({"query": query, "model_policy": "stable_only"})
    structured = response["result"]["structuredContent"]

    assert len(structured["request"]["target_status_ids"]) == 5
    assert "target status extraction capped at 5 IDs" in structured["warnings"]
    assert len(calls) == 1


def test_x_search_payload_only_adds_explicit_reasoning_effort():
    with_reasoning = mcp_x_search._x_search_payload(
        {"query": "latest @xai", "model": "grok-4.5", "_reasoning_effort": "low"}
    )
    without_reasoning = mcp_x_search._x_search_payload(
        {"query": "raw candidates", "model": "grok-composer-2.5-fast", "_reasoning_effort": "high"}
    )

    assert with_reasoning["reasoning"] == {"effort": "low"}
    assert "reasoning" not in without_reasoning
