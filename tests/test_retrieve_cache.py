"""Phase-10 tests: response cache, coalescing, freshness, diff, and privacy."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
import sys
import threading
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import mcp_tools
import xai_responses
from retrieve import cache, pipeline, x_search
from x_oembed import OEmbedResult


TARGET_ID = "2071385784154759468"
OTHER_ID = "2071323738201837785"


def _handle_call(monkeypatch, fake_search, fake_oembed=None):
    calls = []

    async def wrapped(arguments):
        calls.append(dict(arguments))
        return await fake_search(arguments)

    async def empty_oembed(status_ids, handles):
        return OEmbedResult(posts=[], warnings=[])

    monkeypatch.setattr(x_search, "_call_x_search_result", wrapped)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", fake_oembed or empty_oembed)
    return calls


def _call(arguments):
    return asyncio.run(
        mcp_tools._handle(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": arguments},
            }
        )
    )["result"]["structuredContent"]


def _target_post(id_, text="target text"):
    return xai_responses.ResponsesResult(
        json.dumps({"posts": [{"author": "xai", "text": text, "url": f"https://x.com/xai/status/{id_}"}]}),
        {},
        [],
        {"cost_in_usd_ticks": 7},
        "grok-4.6",
    )


# ---------------------------------------------------------------------------
# Cache policy
# ---------------------------------------------------------------------------
def test_cache_directive_covers_only_deterministic_queries():
    exact = cache.cache_directive(
        {"target_strategy": "exact_only", "mode": "structured_posts", "target_status_ids": [TARGET_ID]}
    )
    assert exact is not None and exact.policy == "exact_only"

    latest = cache.cache_directive({"target_strategy": "none", "mode": "latest_by_handle", "handles": ["xai"]})
    assert latest is not None and latest.policy == "latest_by_handle"

    research = cache.cache_directive({"target_strategy": "none", "mode": "semantic_research"})
    assert research is None

    seed = cache.cache_directive({"target_strategy": "seed_then_research", "mode": "claim_verification"})
    assert seed is None


def test_cache_key_normalizes_handle_order_and_query_case():
    base = {"target_strategy": "none", "mode": "latest_by_handle", "handles": ["xai", "grok"]}
    variant = {"target_strategy": "none", "mode": "latest_by_handle", "handles": ["Grok", "@xai"]}
    assert cache.cache_directive(base).key == cache.cache_directive(variant).key


# ---------------------------------------------------------------------------
# Hit / miss / bypass / expiry
# ---------------------------------------------------------------------------
def test_exact_target_second_call_hits_cache_without_upstream(monkeypatch):
    calls = _handle_call(
        monkeypatch,
        lambda arguments: _async_value(_target_post(TARGET_ID)),
    )

    first = _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"})
    second = _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"})

    assert len(calls) == 1  # only the first retrieval reached the fake upstream
    assert first["cache"] == {"hit": False, "policy": "exact_only"}
    assert second["cache"]["hit"] is True
    assert second["cache"]["policy"] == "exact_only"
    assert second["cache"]["age_seconds"] >= 0
    assert second["cache"]["saved_cost_in_usd_ticks"] == first["usage_cost_ticks"]
    assert second["target_match"]["matched"] == [TARGET_ID]


def test_force_refresh_bypasses_read_but_refreshes_cache(monkeypatch):
    calls = _handle_call(
        monkeypatch,
        lambda arguments: _async_value(_target_post(TARGET_ID)),
    )

    _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"})
    refreshed = _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts", "force_refresh": True})

    assert len(calls) == 2
    assert refreshed["cache"] == {"hit": False, "policy": "exact_only"}


def test_max_age_seconds_forces_expiry_of_fresh_entry(monkeypatch):
    calls = _handle_call(
        monkeypatch,
        lambda arguments: _async_value(_target_post(TARGET_ID)),
    )

    _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"})
    _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts", "max_age_seconds": 0})

    assert len(calls) == 2  # zero max age rejects the stored entry


def test_disabled_cache_never_touches_disk(monkeypatch):
    calls = _handle_call(
        monkeypatch,
        lambda arguments: _async_value(_target_post(TARGET_ID)),
    )
    monkeypatch.setattr(config, "GROK_PROXY_RETRIEVE_CACHE", False)

    query = {"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"}
    _call(query)
    _call(query)

    assert len(calls) == 2
    # Fully disabled: neither responses nor ID-only history touch disk.
    cache_file = config.GROK_PROXY_RETRIEVE_CACHE_PATH
    assert cache_file
    assert not Path(cache_file).exists()


def test_degraded_results_are_not_cached(monkeypatch):
    calls = _handle_call(
        monkeypatch,
        lambda arguments: _async_value(xai_responses.ResponsesResult("not json at all", {}, [], None, "grok-4.6")),
    )

    query = {"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts", "model_policy": "stable_only"}
    _call(query)
    after_first = len(calls)
    _call(query)

    assert len(calls) > after_first  # degraded/no-match payloads never hit the cache


async def _async_value(value):
    return value


# ---------------------------------------------------------------------------
# In-flight coalescing
# ---------------------------------------------------------------------------
def test_concurrent_identical_requests_share_one_upstream_run(monkeypatch):
    calls = []
    release = threading.Event()

    async def slow_search(arguments):
        calls.append(arguments["model"])
        # Block in a worker thread so the event loop stays responsive for the
        # second caller to join the in-flight request.
        await asyncio.to_thread(release.wait, 5)
        return _target_post(TARGET_ID)

    _handle_call(monkeypatch, slow_search)

    async def scenario():
        async def one():
            return await pipeline.call_retrieve(
                {"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"},
                search=x_search._call_x_search_result,
            )

        first = asyncio.create_task(one())
        await asyncio.sleep(0.05)  # first request is now blocked in-flight upstream
        second = asyncio.create_task(one())
        release.set()
        return await asyncio.gather(first, second)

    result_a, result_b = asyncio.run(scenario())

    assert len(calls) == 1  # one upstream run shared by both callers
    payload_a = result_a["structuredContent"]
    payload_b = result_b["structuredContent"]
    assert payload_a["target_match"]["matched"] == [TARGET_ID]
    assert payload_b["target_match"]["matched"] == [TARGET_ID]
    assert payload_a is payload_b or payload_a == payload_b


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------
def test_lru_eviction_keeps_cache_within_entry_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES", 3)

    async def run():
        for index in range(5):
            directive = cache.cache_directive(
                {"target_strategy": "exact_only", "mode": "structured_posts", "target_status_ids": [str(int(TARGET_ID) + index)]}
            )
            await cache.put(directive.key, directive.policy, {"marker": index})

    asyncio.run(run())

    cache_file = config.GROK_PROXY_RETRIEVE_CACHE_PATH
    assert cache_file
    conn = sqlite3.connect(cache_file)
    try:
        count = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
    finally:
        conn.close()
    assert count == 3


# ---------------------------------------------------------------------------
# new_since_last_fetch diff (ID-only history)
# ---------------------------------------------------------------------------
def test_new_since_last_fetch_marks_only_unseen_posts(monkeypatch):
    def search_returning(ids):
        async def fake_search(arguments):
            posts = [
                {"author": "xai", "text": f"post {id_}", "url": f"https://x.com/xai/status/{id_}"} for id_ in ids
            ]
            return xai_responses.ResponsesResult(json.dumps({"posts": posts}), {}, [], None, "grok-4.20-0309-non-reasoning")

        return fake_search

    _handle_call(monkeypatch, search_returning([TARGET_ID]))

    first = _call({"handles": ["@xai"]})
    assert all(item["new_since_last_fetch"] is True for item in first["items"])

    _handle_call(monkeypatch, search_returning([TARGET_ID, OTHER_ID]))
    second = _call({"handles": ["@xai"], "force_refresh": True, "count": 2})

    marks = {item["id"]: item["new_since_last_fetch"] for item in second["items"]}
    assert marks[TARGET_ID] is False  # seen in the previous fetch
    assert marks[OTHER_ID] is True  # genuinely new

    # History stores IDs and hashes only, never post text.
    cache_file = config.GROK_PROXY_RETRIEVE_CACHE_PATH
    assert cache_file
    conn = sqlite3.connect(cache_file)
    try:
        rows = conn.execute("SELECT handle, status_id, content_hash FROM fetch_history").fetchall()
    finally:
        conn.close()
    blob = json.dumps(rows)
    assert "post 2071" not in blob  # no raw post text persisted in history


# ---------------------------------------------------------------------------
# Privacy: file permissions
# ---------------------------------------------------------------------------
def test_cache_file_is_private_0600():
    directive = cache.cache_directive(
        {"target_strategy": "exact_only", "mode": "structured_posts", "target_status_ids": [TARGET_ID]}
    )
    asyncio.run(cache.put(directive.key, directive.policy, {"x": 1}))

    cache_file = config.GROK_PROXY_RETRIEVE_CACHE_PATH
    assert cache_file
    path = Path(cache_file)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


# ---------------------------------------------------------------------------
# Cost transparency
# ---------------------------------------------------------------------------
def test_usage_cost_ticks_summarized_in_payload(monkeypatch):
    _handle_call(
        monkeypatch,
        lambda arguments: _async_value(_target_post(TARGET_ID)),
    )
    structured = _call({"query": f"https://x.com/xai/status/{TARGET_ID}", "intent": "posts"})
    assert structured["usage_cost_ticks"] == 7
    assert any(stage.get("usage_cost_ticks") == 7 for stage in structured["retrieval_stages"])
    # Free public stages stay unlabeled.
    assert "usage_cost_ticks" not in structured["retrieval_stages"][0]
