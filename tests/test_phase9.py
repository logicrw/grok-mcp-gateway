"""Phase-9 tests: hardening follow-ups adopted from the audit review session.

Covers the refresh negative cache, locked state_version persistence for
interactive writers, request-level admission across tier transitions,
structured-output capability linkage, and configurable x_search internal names.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Literal

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import mcp_tools
import token_manager
import xai_responses
from retrieve import pipeline, x_search
from retrieve.policy import RequestBudget, resolve_plan
from retrieve.stages import run_search_stage
from x_oembed import OEmbedResult


def _unsigned_jwt(payload):
    import base64

    header = {"alg": "none", "typ": "JWT"}

    def encode(part):
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


_FUTURE_EXP = 4_102_444_800
_EXPIRING_STATE = {
    "access_token": _unsigned_jwt({"exp": 1000, "client_id": "client-1"}),
    "refresh_token": "R0",
    "client_id": "client-1",
    "token_endpoint": "https://auth.x.ai/oauth2/token",
}


# ---------------------------------------------------------------------------
# Refresh negative cache
# ---------------------------------------------------------------------------
def _write_state(tmp_path, state):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def test_transient_refresh_failure_suppresses_immediate_retries(monkeypatch, tmp_path):
    state_path = _write_state(tmp_path, _EXPIRING_STATE)
    post_calls = []

    def fake_post(url, headers, data, timeout):
        post_calls.append(data.get("refresh_token"))
        request = httpx.Request("POST", url)
        return httpx.Response(503, request=request, text="try later")

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    with pytest.raises(token_manager.TokenRefreshUpstreamError):
        asyncio.run(token_manager.get_access_token(force_refresh=True))
    # Immediate retry within the suppression window must not hit the endpoint.
    with pytest.raises(RuntimeError, match="suppressed"):
        asyncio.run(token_manager.get_access_token(force_refresh=True))
    assert post_calls == ["R0"]


def test_suppression_expires_and_refresh_retries(monkeypatch, tmp_path):
    state_path = _write_state(tmp_path, _EXPIRING_STATE)
    post_calls = []

    def fake_post(url, headers, data, timeout):
        post_calls.append(data.get("refresh_token"))
        if len(post_calls) == 1:
            request = httpx.Request("POST", url)
            return httpx.Response(503, request=request, text="try later")
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"access_token": "fresh", "refresh_token": "R0", "token_type": "Bearer", "expires_in": 3600},
        )

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)
    monkeypatch.setattr(token_manager, "REFRESH_FAILURE_SUPPRESS_SECONDS", 0.05)

    with pytest.raises(token_manager.TokenRefreshUpstreamError):
        asyncio.run(token_manager.get_access_token(force_refresh=True))
    import time

    time.sleep(0.1)
    token = asyncio.run(token_manager.get_access_token(force_refresh=True))
    assert token == "fresh"
    assert post_calls == ["R0", "R0"]


def test_invalid_grant_is_never_suppressed(monkeypatch, tmp_path):
    state_path = _write_state(tmp_path, _EXPIRING_STATE)
    post_calls = []

    def fake_post(url, headers, data, timeout):
        post_calls.append(data.get("refresh_token"))
        request = httpx.Request("POST", url)
        return httpx.Response(400, request=request, json={"error": "invalid_grant"})

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    for _ in range(2):
        with pytest.raises(token_manager.AuthRequiredError):
            asyncio.run(token_manager.get_access_token(force_refresh=True))
    assert post_calls == ["R0", "R0"]


# ---------------------------------------------------------------------------
# Locked, versioned save_local_state for interactive writers
# ---------------------------------------------------------------------------
def test_save_local_state_bumps_state_version_under_lock(monkeypatch, tmp_path):
    state_path = _write_state(tmp_path, {"access_token": "old", "state_version": 2})
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    asyncio.run(token_manager.save_local_state({"access_token": "login-token", "refresh_token": "R-login"}))

    on_disk = json.loads(state_path.read_text())
    assert on_disk["access_token"] == "login-token"
    assert on_disk["state_version"] == 3


def test_save_local_state_keeps_higher_incoming_version(monkeypatch, tmp_path):
    state_path = _write_state(tmp_path, {"access_token": "old", "state_version": 2})
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    asyncio.run(token_manager.save_local_state({"access_token": "newer", "state_version": 9}))

    assert json.loads(state_path.read_text())["state_version"] == 9


# ---------------------------------------------------------------------------
# Request-level admission across tier transitions
# ---------------------------------------------------------------------------
class _CountingSemaphore(asyncio.Semaphore):
    def __init__(self, value: int = 1) -> None:
        super().__init__(value)
        self.acquires = 0

    async def acquire(self) -> Literal[True]:
        self.acquires += 1
        return await super().acquire()


def test_tier_transitions_reuse_one_admission_permit(monkeypatch):
    monkeypatch.setattr(x_search, "_x_search_semaphore", _CountingSemaphore(1))
    calls = []

    async def fake_search(arguments):
        calls.append(arguments.get("model"))
        posts = '{"posts":[]}' if len(calls) < 3 else '{"posts":[{"author":"xai","text":"raw save","url":"https://x.com/xai/status/2071385784154759468"}]}'
        return xai_responses.ResponsesResult(posts, {}, [], None, arguments["model"])

    async def empty_oembed(status_ids, handles):
        return OEmbedResult(posts=[], warnings=[])

    monkeypatch.setattr(x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(pipeline, "fetch_oembed_posts", empty_oembed)

    response = asyncio.run(
        mcp_tools._handle(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {"handles": ["@xai"]},
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    # Fast -> Smart escalation -> raw expansion all ran on one admission permit.
    assert len(calls) == 3
    assert x_search._x_search_semaphore.acquires == 1
    assert structured["retrieval_status"] in {"ok", "degraded"}
    assert any(item.get("text") == "raw save" for item in structured["items"])


# ---------------------------------------------------------------------------
# Structured-output capability linkage
# ---------------------------------------------------------------------------
def test_run_search_stage_drops_structured_output_for_unknown_model():
    captured = []

    async def fake_search(arguments):
        captured.append(dict(arguments))
        return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, arguments["model"])

    budget = RequestBudget(total_seconds=30)
    asyncio.run(
        run_search_stage(fake_search, {"model": "grok-custom-x", "_structured_output": True}, stage="custom_extract", budget=budget)
    )
    asyncio.run(
        run_search_stage(fake_search, {"model": "grok-4.6", "_structured_output": True}, stage="smart_extract", budget=budget)
    )

    assert "_structured_output" not in captured[0]
    assert captured[1]["_structured_output"] is True


def test_unknown_model_route_warning_mentions_structured_outputs():
    metadata = {
        "mode": "semantic_research",
        "intent": "research",
        "handles": [],
        "query": "deep research topic",
        "count": 10,
        "sort": "relevance",
    }
    plan = resolve_plan(metadata, explicit_model="grok-custom-unknown")
    assert plan.route_warning and "structured outputs" in plan.route_warning
    assert "reasoning_effort" in plan.route_warning


# ---------------------------------------------------------------------------
# Configurable x_search internal tool names
# ---------------------------------------------------------------------------
def test_auto_x_search_internal_names_are_configurable(monkeypatch):
    monkeypatch.setattr(main.config, "GROK_PROXY_X_SEARCH_INTERNAL_TOOL_NAMES", ["renamed_tool"])

    renamed = {"type": "custom_tool_call", "name": "renamed_tool"}
    legacy = {"type": "custom_tool_call", "name": "x_keyword_search"}
    assert main._is_auto_x_search_artifact(renamed) is True
    assert main._is_auto_x_search_artifact(legacy) is False  # no longer attributed after rename
