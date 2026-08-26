import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_tools
from retrieve import x_search
from retrieve import pipeline
import xai_responses
from retrieve.policy import RequestBudget
from x_oembed import OEmbedResult


async def _empty_oembed(status_ids, handles):
    return OEmbedResult(posts=[], warnings=[])


def _call(arguments):
    return asyncio.run(
        mcp_tools._handle(
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
        return xai_responses.ResponsesResult(
            '{"posts":['
            f'{{"text":"first","url":"https://x.com/xai/status/{first}"}},'
            f'{{"text":"second","url":"https://x.com/xai/status/{second}"}}]}}',
            {},
            [],
            None,
            arguments["model"],
        )

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    response = _call(
        {
            "query": f"{first} {second}",
            "model_policy": "stable_only",
            "model": "custom-stable-model",
        }
    )
    structured = response["result"]["structuredContent"]

    assert len(calls) == 1
    assert first in calls[0]["query"] and second in calls[0]["query"]
    assert calls[0]["model"] == "custom-stable-model"
    assert all("_reasoning_effort" not in call for call in calls)
    assert structured["target_match"]["missing"] == []
    assert [stage["name"] for stage in structured["retrieval_stages"]] == [
        "public_oembed",
        "target_fallback",
    ]


def test_target_extraction_cap_is_visible_in_result(monkeypatch):
    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, "grok-4.5")

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)
    query = " ".join(str(2071385784154759460 + index) for index in range(7))

    response = _call({"query": query, "model_policy": "stable_only"})
    structured = response["result"]["structuredContent"]

    assert len(structured["request"]["target_status_ids"]) == 5
    assert "target status extraction capped at 5 IDs" in structured["warnings"]
    assert len(calls) == 2




def test_exact_target_drops_nearby_posts_from_result(monkeypatch):
    target = "2071385784154759468"
    nearby = "9999999999999999999"

    async def fake_search(arguments):
        return xai_responses.ResponsesResult(
            f'{{"posts":[{{"text":"nearby","url":"https://x.com/xai/status/{nearby}"}}]}}',
            {},
            [],
            None,
            arguments["model"],
        )

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    structured = _call({"query": target})["result"]["structuredContent"]

    assert structured["retrieval_status"] == "no_match"
    assert structured["items"] == []
    assert structured["posts"] == []
    assert structured["target_match"]["missing"] == [target]


def test_target_pipeline_records_quality_gate_decision(monkeypatch):
    target = "2071385784154759468"
    from retrieve.pipeline import _quality_gate_counts

    before = _quality_gate_counts["pass"] + _quality_gate_counts["fail"]

    async def fake_search(arguments):
        return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, arguments["model"])

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    _call({"query": target, "model_policy": "stable_only"})

    assert _quality_gate_counts["pass"] + _quality_gate_counts["fail"] == before + 1


def test_exact_target_accepts_trusted_media_suffix(monkeypatch):
    target = "2071385784154759468"
    media_url = f"https://x.com/xai/status/{target}/photo/1"
    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        return xai_responses.ResponsesResult(
            f'{{"posts":[{{"text":"target media post","url":"{media_url}"}}]}}',
            {},
            [],
            None,
            arguments["model"],
        )

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    structured = _call({"query": media_url})["result"]["structuredContent"]

    assert len(calls) == 1
    assert structured["retrieval_status"] == "ok"
    assert structured["target_match"]["matched"] == [target]
    assert structured["items"][0]["url"] == media_url


def test_exact_target_accepts_i_web_status_url(monkeypatch):
    target = "2071385784154759468"
    history_url = f"https://twitter.com/i/web/status/{target}"

    async def fake_search(arguments):
        return xai_responses.ResponsesResult(
            f'{{"posts":[{{"text":"historical target","url":"{history_url}"}}]}}',
            {},
            [],
            None,
            arguments["model"],
        )

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    structured = _call({"query": history_url})["result"]["structuredContent"]

    assert structured["retrieval_status"] == "ok"
    assert structured["target_match"]["matched"] == [target]
    assert structured["items"][0]["url"] == history_url


def test_model_override_has_a_hard_length_limit():
    response = _call({"query": "latest xAI posts", "model": "m" * 129})

    assert response["result"]["isError"] is True
    assert "model must be at most 128 characters" in response["result"]["structuredContent"]["warnings"][0]
    assert response["result"]["structuredContent"]["mode"] == "semantic_research"
    assert response["result"]["structuredContent"]["retrieval_stages"][0]["name"] == "validation"


def test_error_result_uses_parsed_latest_by_handle_mode():
    result = pipeline.error_result(
        {"handles": ["xai"], "sort": "latest", "count": 5},
        "upstream failed",
    )
    payload = result["structuredContent"]

    assert result["isError"] is True
    assert payload["mode"] == "latest_by_handle"
    assert payload["request"]["mode"] == "latest_by_handle"
    assert payload["request"]["handles"] == ["xai"]
    assert payload["request"]["count"] == 5
    assert payload["retrieval_status"] == "error"
    assert payload["retrieval_stages"][0]["name"] == "fast_extract"


def test_latest_by_handle_empty_result_does_not_fan_out_to_smart_or_raw(monkeypatch):
    import config

    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        return xai_responses.ResponsesResult(
            '{"posts":[]}', {}, [], None, arguments["model"]
        )

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)

    response = _call({"handles": ["xai"], "sort": "latest", "count": 5})
    structured = response["result"]["structuredContent"]

    assert response["result"]["isError"] is False
    assert structured["retrieval_status"] == "empty"
    assert [call["model"] for call in calls] == [config.GROK_PROXY_FAST_MODEL]
    assert [stage["name"] for stage in structured["retrieval_stages"]] == [
        "fast_extract",
        "raw_expansion",
    ]
    assert structured["retrieval_stages"][1]["status"] == "skipped"
    assert structured["retrieval_stages"][1]["reason"] == "latest_by_handle"


def test_exhausted_oembed_budget_is_visible_as_warning():
    status_id = "2071385784154759468"
    payload = {
        "items": [
            {
                "id": status_id,
                "url": f"https://x.com/xai/status/{status_id}",
                "text": "",
            }
        ],
        "warnings": [],
        "retrieval_stages": [],
    }
    metadata = {"target_status_ids": [status_id], "handles": []}

    asyncio.run(
        pipeline._run_public_oembed(
            payload,
            metadata,
            RequestBudget(total_seconds=0),
        )
    )

    assert payload["warnings"] == ["public oEmbed skipped: total retrieval budget exhausted"]
    assert payload["retrieval_stages"][0]["status"] == "skipped"


def test_x_search_payload_only_adds_explicit_reasoning_effort():
    with_reasoning = x_search._x_search_payload(
        {"query": "latest @xai", "model": "grok-4.5", "_reasoning_effort": "low"}
    )
    without_reasoning = x_search._x_search_payload(
        {"query": "raw candidates", "model": "grok-composer-2.5-fast", "_reasoning_effort": "high"}
    )

    assert with_reasoning["reasoning"] == {"effort": "low"}
    assert "reasoning" not in without_reasoning


def test_smart_lane_failure_rescued_by_raw_expansion(monkeypatch):
    async def fallback_search(arguments):
        if arguments.get("model") == "grok-composer-2.5-fast":
            return xai_responses.ResponsesResult(
                '{"posts":[{"text":"rescued by raw","url":"https://x.com/user/status/999"}]}',
                {},
                [],
                None,
                "grok-composer-2.5-fast",
            )
        raise RuntimeError("xAI 502 Bad Gateway")

    monkeypatch.setattr(x_search, "_call_x_search_result", fallback_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    response = _call(
        {
            "query": "deep research topic",
            "intent": "research",
        }
    )

    structured = response["result"]["structuredContent"]
    assert response["result"]["isError"] is False
    assert structured["retrieval_status"] == "degraded"
    assert structured["items"][0]["text"] == "rescued by raw"
    assert any(s["name"] == "smart_extract" and s["status"] == "failed" for s in structured["retrieval_stages"])
    assert any(s["name"] == "raw_expansion" and s["status"] == "success" for s in structured["retrieval_stages"])


def test_all_search_stages_failure_returns_graceful_empty_payload(monkeypatch):
    async def failing_search(arguments):
        from retrieve.stages import StageTimeout

        raise StageTimeout("stage")

    monkeypatch.setattr(x_search, "_call_x_search_result", failing_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    response = _call({"query": "failing query", "intent": "research"})

    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["retrieval_status"] == "empty"
    assert structured["items"] == []
    assert any("smart_extract failed" in w for w in structured["warnings"])


def test_network_disconnect_returns_graceful_empty_payload(monkeypatch):
    import httpx

    async def disconnected_search(arguments):
        raise httpx.ConnectError("Network unreachable")

    monkeypatch.setattr(x_search, "_call_x_search_result", disconnected_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", _empty_oembed)

    response = _call({"query": "disconnected query", "intent": "research"})

    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["retrieval_status"] == "empty"
    assert structured["items"] == []
    assert any("Network unreachable" in w for w in structured["warnings"])


