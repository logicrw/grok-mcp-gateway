"""Phase-8 regression tests for the external adversarial architecture audit.

Each test reproduces a boundary condition from the 2026-08-23 audit:
DNS-rebinding boundary, cross-process refresh transactions, refresh-task
cancellation, seed_then_research budget/evidence, search overload admission,
invalid_grant self-healing, stdio framing robustness, JSON-RPC strictness,
auto x_search attribution, and payload URL canonicalization.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import mcp_server
import mcp_x_search
import token_manager
import xai_responses
from retrieve import x_search
from retrieve.payload import _canonical_url_key, _source_key, merge_stage_payload
from retrieve.pipeline import error_result
from retrieve.policy import RequestBudget, resolve_plan
from retrieve.stages import StageOverloaded, run_search_stage
from x_oembed import OEmbedPost, OEmbedResult


def _unsigned_jwt(payload):
    header = {"alg": "none", "typ": "JWT"}

    def encode(part):
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


_FUTURE_EXP = 4_102_444_800
_VALID_STATE: dict = {
    "access_token": _unsigned_jwt({"exp": _FUTURE_EXP, "client_id": "client-1"}),
    "refresh_token": "refresh-base",
    "client_id": "client-1",
    "token_endpoint": "https://auth.x.ai/oauth2/token",
}


# ---------------------------------------------------------------------------
# P0-1: loopback DNS-rebinding boundary
# ---------------------------------------------------------------------------
def _loopback_client(monkeypatch, state=None):
    async def fake_read_local_state():
        return state or _VALID_STATE

    monkeypatch.setattr(main.config, "PROXY_API_KEY", None)
    monkeypatch.setattr(main.token_manager, "read_local_state", fake_read_local_state)
    return TestClient(main.app, base_url="http://127.0.0.1")


def test_host_header_allowlist_unit():
    assert main._host_header_allowed("127.0.0.1")
    assert main._host_header_allowed("127.0.0.1:9996")
    assert main._host_header_allowed("localhost:9996")
    assert main._host_header_allowed("[::1]:9996")
    assert not main._host_header_allowed("attacker.example")
    assert not main._host_header_allowed("attacker.example:9996")
    assert not main._host_header_allowed("")
    assert not main._host_header_allowed("127.0.0.1.evil.example")


def test_loopback_proxy_rejects_rebound_host_header(monkeypatch):
    client = _loopback_client(monkeypatch)
    with client:
        response = client.get("/health", headers={"Host": "attacker.example:9996"})
    assert response.status_code == 421
    assert "Misdirected" in response.text


def test_loopback_proxy_rejects_cross_site_browser_origin(monkeypatch):
    client = _loopback_client(monkeypatch)
    with client:
        response = client.post(
            "/v1/responses",
            json={"model": "grok-4.6", "input": "hi"},
            headers={"Origin": "http://evil.example"},
        )
    assert response.status_code == 403
    assert "origin" in response.text.lower()


def test_loopback_proxy_allows_configured_browser_origin(monkeypatch):
    client = _loopback_client(monkeypatch)

    async def fake_get_auth_headers():
        raise RuntimeError("should not be reached")  # pragma: no cover - boundary passes, route 503s later

    monkeypatch.setattr(main.config, "GROK_PROXY_ALLOWED_ORIGINS", ["http://localhost:3000"])
    monkeypatch.setattr(main.token_manager, "get_auth_headers", fake_get_auth_headers)
    with client:
        response = client.post(
            "/v1/responses",
            json={"model": "grok-4.6", "input": "hi"},
            headers={"Origin": "http://localhost:3000"},
        )
    assert response.status_code == 503  # passed the boundary, failed on token resolution


def test_non_browser_local_client_without_origin_still_works(monkeypatch):
    client = _loopback_client(monkeypatch)
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# P1-1 / P2-2: cross-process refresh transaction + state_version CAS
# ---------------------------------------------------------------------------
def test_refresh_adopts_newer_state_written_by_another_process(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    newer = dict(_VALID_STATE)
    newer["access_token"] = _unsigned_jwt({"exp": _FUTURE_EXP, "client_id": "client-1"})
    newer["refresh_token"] = "R1-from-process-a"
    newer["state_version"] = 5
    state_path.write_text(json.dumps(newer), encoding="utf-8")

    def must_not_refresh(*args, **kwargs):
        raise AssertionError("must not refresh when another process already rotated")

    monkeypatch.setattr(token_manager.httpx, "post", must_not_refresh)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    stale = dict(_VALID_STATE)
    stale["access_token"] = _unsigned_jwt({"exp": 1000, "client_id": "client-1"})
    updated = asyncio.run(token_manager.refresh_access_token(stale))

    assert updated["refresh_token"] == "R1-from-process-a"
    assert updated["state_version"] == 5
    assert json.loads(state_path.read_text())["refresh_token"] == "R1-from-process-a"


def test_refresh_failure_never_rolls_back_newer_concurrent_state(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    expiring_access = _unsigned_jwt({"exp": 1000, "client_id": "client-1"})
    stale = dict(_VALID_STATE)
    stale["access_token"] = expiring_access
    stale["refresh_token"] = "R0"
    state_path.write_text(json.dumps(stale), encoding="utf-8")

    newer = dict(_VALID_STATE)
    newer["refresh_token"] = "R1-written-during-flight"
    newer["state_version"] = 7

    def fake_post(url, headers, data, timeout):
        # Simulate another process persisting a rotated credential while our
        # refresh request with R0 is in flight, then the server rejecting R0.
        state_path.write_text(json.dumps(newer), encoding="utf-8")
        request = httpx.Request("POST", url)
        return httpx.Response(400, request=request, json={"error": "invalid_grant"})

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    with pytest.raises(token_manager.AuthRequiredError):
        asyncio.run(token_manager.refresh_access_token(stale))

    on_disk = json.loads(state_path.read_text())
    assert on_disk["refresh_token"] == "R1-written-during-flight"
    assert on_disk["state_version"] == 7
    assert not on_disk.get("reauth_required")


def test_refresh_success_increments_state_version(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    base = dict(_VALID_STATE)
    base["state_version"] = 3
    state_path.write_text(json.dumps(base), encoding="utf-8")

    def fake_post(url, headers, data, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"access_token": "new-access", "refresh_token": "R1", "token_type": "Bearer", "expires_in": 3600},
        )

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    updated = asyncio.run(token_manager.refresh_access_token(base))

    assert updated["state_version"] == 4
    assert json.loads(state_path.read_text())["state_version"] == 4


# ---------------------------------------------------------------------------
# P1-2: cancellation must not lose a rotated refresh token
# ---------------------------------------------------------------------------
def test_caller_cancellation_still_persists_rotated_token(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    state = dict(_VALID_STATE)
    state["access_token"] = _unsigned_jwt({"exp": 1000, "client_id": "client-1"})  # expiring
    state["refresh_token"] = "R0"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    refresh_calls = []
    release_refresh = threading.Event()

    def fake_refresh_sync(refresh_token, token_endpoint, client_id):
        refresh_calls.append(refresh_token)
        assert refresh_token == "R0"
        release_refresh.wait(timeout=5)
        return {
            "access_token": "rotated-access",
            "refresh_token": "R1-rotated",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)
    monkeypatch.setattr(token_manager, "_refresh_sync", fake_refresh_sync)
    monkeypatch.setattr(token_manager, "_refresh_task", None)

    async def scenario():
        caller = asyncio.create_task(token_manager.get_access_token())
        refresh_task = None
        for _ in range(500):
            await asyncio.sleep(0.01)
            task = token_manager._refresh_task
            if task is not None and not task.done():
                refresh_task = task
                break
        assert refresh_task is not None, "refresh task never started"
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        assert not refresh_task.done()  # shielded task survives cancellation
        release_refresh.set()
        await asyncio.wait_for(refresh_task, timeout=5)
        # A later caller gets the rotated token without consuming R0 again.
        token = await token_manager.get_access_token()
        return token

    token = asyncio.run(scenario())

    assert refresh_calls == ["R0"]
    assert token == "rotated-access"
    on_disk = json.loads(state_path.read_text())
    assert on_disk["refresh_token"] == "R1-rotated"
    assert on_disk["access_token"] == "rotated-access"
    monkeypatch.setattr(token_manager, "_refresh_task", None)


def test_concurrent_401_storm_refreshes_once(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    state = dict(_VALID_STATE)
    state["access_token"] = _unsigned_jwt({"exp": 1000, "client_id": "client-1"})
    state["refresh_token"] = "R0"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    refresh_calls = []

    def fake_refresh_sync(refresh_token, token_endpoint, client_id):
        refresh_calls.append(refresh_token)
        return {
            "access_token": "fresh-access",
            "refresh_token": "R0",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)
    monkeypatch.setattr(token_manager, "_refresh_sync", fake_refresh_sync)
    monkeypatch.setattr(token_manager, "_refresh_task", None)

    async def storm():
        tasks = [
            asyncio.create_task(
                token_manager.get_access_token(force_refresh=True, stale_access_token=state["access_token"])
            )
            for _ in range(20)
        ]
        return await asyncio.gather(*tasks)

    tokens = asyncio.run(storm())

    assert refresh_calls == ["R0"]
    assert tokens == ["fresh-access"] * 20
    monkeypatch.setattr(token_manager, "_refresh_task", None)


# ---------------------------------------------------------------------------
# P1-6: invalid_grant -> AuthRequiredError with login command passthrough
# ---------------------------------------------------------------------------
def test_invalid_grant_maps_to_auth_required_error(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text(json.dumps(_VALID_STATE), encoding="utf-8")

    def fake_post(url, headers, data, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(400, request=request, json={"error": "invalid_grant"})

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    with pytest.raises(token_manager.AuthRequiredError) as exc_info:
        asyncio.run(token_manager.refresh_access_token(dict(_VALID_STATE)))

    assert "invalid_grant" in str(exc_info.value)
    on_disk = json.loads(state_path.read_text())
    assert on_disk["reauth_required"] is True
    assert on_disk["last_refresh_error_class"] == "AuthRequiredError"


def test_transient_refresh_failure_is_retryable_and_not_reauth(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text(json.dumps(_VALID_STATE), encoding="utf-8")

    def fake_post(url, headers, data, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(503, request=request, text="try later")

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    with pytest.raises(token_manager.TokenRefreshUpstreamError):
        asyncio.run(token_manager.refresh_access_token(dict(_VALID_STATE)))

    on_disk = json.loads(state_path.read_text())
    assert on_disk["last_refresh_status"] == "failure"
    assert on_disk["reauth_required"] is False


def test_tools_call_auth_error_carries_login_command_and_stage(monkeypatch):
    async def failing_search(arguments):
        raise token_manager.AuthRequiredError("xAI rejected the OAuth refresh credential ('invalid_grant').")

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", failing_search)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": {"query": "latest grok news"}},
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert response["result"]["isError"] is True
    assert structured["auth_login_command"] == token_manager.login_command()
    assert structured["retrieval_stages"][0]["name"] == "auth_refresh"
    assert structured["auth_error"] == {"code": "AUTH_REQUIRED", "retryable": False}
    assert any("AUTH_REQUIRED" in warning for warning in structured["warnings"])


def test_error_result_stage_for_non_auth_errors_is_untouched():
    payload = error_result({"query": "latest xAI posts"}, "xAI Responses request failed with upstream status 429")
    structured = payload["structuredContent"]
    assert "auth_login_command" not in structured
    assert structured["retrieval_stages"][0]["name"] != "auth_refresh"


# ---------------------------------------------------------------------------
# P1-3 / P1-4: seed_then_research budget and evidence retention
# ---------------------------------------------------------------------------
TARGET_ID = "2071385784154759468"
EVIDENCE_A = "2071323738201837785"
EVIDENCE_B = "2071300000000000001"


def _seed_research_setup(monkeypatch, smart_posts):
    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        posts_json = json.dumps({"posts": smart_posts})
        return xai_responses.ResponsesResult(posts_json, {}, [], None, arguments["model"])

    async def fake_oembed(status_ids, handles):
        return OEmbedResult(
            posts=[
                OEmbedPost(
                    status_id=TARGET_ID,
                    url=f"https://x.com/i/status/{TARGET_ID}",
                    author="xai",
                    text="target seed text",
                )
            ],
            warnings=[],
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(mcp_x_search.mcp_retrieve, "fetch_oembed_posts", fake_oembed)
    return calls


def test_seed_then_research_runs_smart_stage_and_keeps_evidence(monkeypatch):
    calls = _seed_research_setup(
        monkeypatch,
        [
            {"author": "xai", "text": "target claim", "url": f"https://x.com/xai/status/{TARGET_ID}"},
            {"author": "witness", "text": "corroborating report", "url": f"https://x.com/witness/status/{EVIDENCE_A}"},
            {"author": "critic", "text": "contradicting thread", "url": f"https://x.com/critic/status/{EVIDENCE_B}"},
        ],
    )

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {
                        "query": f"https://x.com/xai/status/{TARGET_ID} did xAI really ship this?",
                        "intent": "research",
                        "model_policy": "stable_only",
                    },
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    # P1-3: the research stage actually executed with real lane values.
    assert len(calls) == 1
    assert calls[0]["model"] == "grok-4.6"
    assert calls[0]["_max_turns"] >= 1
    assert not any("smart extract failed" in warning for warning in structured["warnings"])
    stage_names = [stage["name"] for stage in structured["retrieval_stages"]]
    assert "smart_extract" in stage_names
    assert structured["retrieval_stages"][-1]["status"] in {"success", "skipped"}
    # P1-4: evidence survived finalization.
    item_ids = {item["id"] for item in structured["items"]}
    assert TARGET_ID in item_ids
    assert EVIDENCE_A in item_ids
    assert EVIDENCE_B in item_ids
    assert structured["target_match"]["matched"] == [TARGET_ID]
    assert structured["retrieval_status"] in {"ok", "degraded"}


def test_exact_only_still_filters_unrelated_posts(monkeypatch):
    calls = []

    async def fake_search(arguments):
        calls.append(dict(arguments))
        posts_json = json.dumps(
            {
                "posts": [
                    {"author": "xai", "text": "target text", "url": f"https://x.com/xai/status/{TARGET_ID}"},
                    {"author": "nearby", "text": "unrelated nearby post", "url": f"https://x.com/nearby/status/{EVIDENCE_A}"},
                ]
            }
        )
        return xai_responses.ResponsesResult(posts_json, {}, [], None, arguments["model"])

    async def empty_oembed(status_ids, handles):
        return OEmbedResult(posts=[], warnings=[])

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(mcp_x_search.mcp_retrieve, "fetch_oembed_posts", empty_oembed)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {
                        "query": f"https://x.com/xai/status/{TARGET_ID}",
                        "intent": "posts",
                        "model_policy": "stable_only",
                    },
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert calls  # fallback ran because oEmbed returned nothing
    item_ids = {item["id"] for item in structured["items"]}
    assert item_ids == {TARGET_ID}


# ---------------------------------------------------------------------------
# P1-5: search admission overload must not escalate
# ---------------------------------------------------------------------------
def test_queue_timeout_reports_overloaded_without_escalation(monkeypatch):
    post_calls = []

    async def fake_post(payload):
        post_calls.append(payload)
        raise AssertionError("upstream must not be called when admission never granted")

    monkeypatch.setattr(xai_responses, "post", fake_post)
    monkeypatch.setattr(x_search.config, "GROK_PROXY_MCP_X_SEARCH_QUEUE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(x_search, "_x_search_semaphore", asyncio.Semaphore(1))

    async def scenario():
        saturated = x_search._x_search_semaphore
        await saturated.acquire()  # exhaust capacity so the request can only queue
        try:
            return await mcp_x_search._handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "x_retrieve",
                        "arguments": {"handles": ["@xai"], "query": "latest posts", "model_policy": "stable_only"},
                    },
                }
            )
        finally:
            saturated.release()

    response = asyncio.run(scenario())

    structured = response["result"]["structuredContent"]
    assert post_calls == []
    stage_statuses = {stage["name"]: stage["status"] for stage in structured["retrieval_stages"]}
    overloaded = [stage for stage in structured["retrieval_stages"] if stage["status"] == "overloaded"]
    assert overloaded, stage_statuses
    assert "smart_escalation" not in stage_statuses  # no tier escalation under overload
    assert "raw_expansion" not in stage_statuses  # and no raw expansion either
    assert any("overloaded" in warning for warning in structured["warnings"])
    assert structured["retrieval_status"] in {"empty", "degraded"}


def test_run_search_stage_reraises_stage_overloaded():
    async def overloaded_search(arguments):
        raise StageOverloaded(0.05)

    with pytest.raises(StageOverloaded):
        asyncio.run(
            run_search_stage(
                overloaded_search,
                {"model": "grok-4.6"},
                stage="fast_extract",
                budget=RequestBudget(total_seconds=30),
            )
        )


# ---------------------------------------------------------------------------
# P1-7 / P2-3 / P2-7: stdio framing, JSON-RPC strictness, client cleanup
# ---------------------------------------------------------------------------
def test_stdio_survives_bad_utf8_oversized_and_then_serves_ping():
    async def run():
        reader = asyncio.StreamReader()
        writer = _ListWriter()
        reader.feed_data(b"\xff\xfe not utf8\n")
        reader.feed_data(b"x" * 70000 + b"\n")  # exceeds the 64 KiB default reader limit
        reader.feed_data(b"\n")
        reader.feed_data(b'{"jsonrpc":"2.0","id":9,"method":"ping"}\n')
        reader.feed_eof()
        await mcp_server.stdio_main(reader, writer)
        return writer.lines

    lines = asyncio.run(run())
    payloads = [json.loads(line) for line in lines]
    # bad utf-8 -> parse error, oversized -> drained parse error, blank skipped, ping answered
    assert payloads[0]["error"]["code"] == -32700
    assert payloads[1]["error"]["code"] == -32700
    assert payloads[-1] == {"jsonrpc": "2.0", "id": 9, "result": {}}


def test_stdio_scalar_and_array_json_return_invalid_request():
    async def run():
        reader = asyncio.StreamReader()
        writer = _ListWriter()
        reader.feed_data(b"42\n")
        reader.feed_data(b"[1,2]\n")
        reader.feed_eof()
        await mcp_server.stdio_main(reader, writer)
        return writer.lines

    payloads = [json.loads(line) for line in asyncio.run(run())]
    assert [payload["error"]["code"] for payload in payloads] == [-32600, -32600]


def test_stdio_main_closes_shared_responses_client(monkeypatch):
    closed = []

    async def fake_aclose():
        closed.append(True)

    monkeypatch.setattr(mcp_server.xai_responses, "aclose_client", fake_aclose)

    async def run():
        reader = asyncio.StreamReader()
        writer = _ListWriter()
        reader.feed_data(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        reader.feed_eof()
        await mcp_server.stdio_main(reader, writer)

    asyncio.run(run())
    assert closed == [True]


class _ListWriter:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(data)

    def flush(self):
        return None


def test_handle_enforces_jsonrpc_2_envelope():
    bad_version = asyncio.run(mcp_server.handle({"jsonrpc": "1.0", "id": 1, "method": "ping"}))
    assert bad_version is not None and bad_version["error"]["code"] == -32600

    bad_id = asyncio.run(mcp_server.handle({"jsonrpc": "2.0", "id": True, "method": "ping"}))
    assert bad_id is not None and bad_id["error"]["code"] == -32600

    empty_method = asyncio.run(mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": ""}))
    assert empty_method is not None and empty_method["error"]["code"] == -32600


def test_notifications_of_any_method_receive_no_response():
    async def run():
        reader = asyncio.StreamReader()
        writer = _ListWriter()
        reader.feed_data(b'{"jsonrpc":"2.0","method":"ping"}\n')
        reader.feed_data(b'{"jsonrpc":"2.0","method":"tools/list"}\n')
        reader.feed_eof()
        await mcp_server.stdio_main(reader, writer)
        return writer.lines

    assert asyncio.run(run()) == []


# ---------------------------------------------------------------------------
# P1-8: auto x_search attribution + SSE framing
# ---------------------------------------------------------------------------
def test_auto_x_search_filter_keeps_client_custom_tool_calls():
    block = (
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"tools":[{"type":"x_search"}],'
        '"output":[{"type":"custom_tool_call","name":"client_weather_tool"},'
        '{"type":"custom_tool_call","name":"x_keyword_search"},'
        '{"type":"message","content":[]}]}}'
    )

    sanitized = main._sanitize_auto_x_search_sse_event(block)
    assert sanitized is not None
    payload = json.loads(sanitized.split("data: ", 1)[1])

    output_names = [item.get("name") for item in payload["response"]["output"]]
    assert "client_weather_tool" in output_names
    assert "x_keyword_search" not in output_names
    assert payload["response"]["tools"] == []


def test_auto_x_search_filter_attributes_input_events_by_item_id():
    stream_filter = main._AutoXSearchSSEFilter()

    added = (
        "event: response.output_item.added\n"
        'data: {"type":"response.output_item.added","item":{"type":"custom_tool_call","name":"x_keyword_search","id":"item_x"}}'
    )
    assert stream_filter.sanitize_event(added) is None

    injected_delta = (
        "event: response.custom_tool_call_input.delta\n"
        'data: {"type":"response.custom_tool_call_input.delta","item_id":"item_x","delta":"x"}'
    )
    assert stream_filter.sanitize_event(injected_delta) is None

    client_delta = (
        "event: response.custom_tool_call_input.delta\n"
        'data: {"type":"response.custom_tool_call_input.delta","item_id":"item_client","delta":"c"}'
    )
    assert stream_filter.sanitize_event(client_delta) is not None


class _FakeSSEUpstream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def _collect_sse(upstream):
    async def run():
        return [chunk async for chunk in main._iter_auto_x_search_compatible_sse(upstream)]

    return asyncio.run(run())


def test_auto_x_search_sse_splits_crlf_events_incrementally():
    upstream = _FakeSSEUpstream(
        [
            "event: response.output_text.delta\r",
            "\n",
            'data: {"type":"response.output_text.delta","delta":"he"}\r\n\r',
            "\n",
            'data: {"type":"response.output_text.delta","delta":"llo"}\r\n\r\n',
        ]
    )

    chunks = _collect_sse(upstream)

    assert upstream.closed is True
    assert len(chunks) == 2
    deltas = [json.loads(chunk.decode("utf-8").split("data: ", 1)[1].strip()) for chunk in chunks]
    assert [delta["delta"] for delta in deltas] == ["he", "llo"]


def test_auto_x_search_sse_overflow_closes_upstream(monkeypatch):
    monkeypatch.setattr(main, "_SSE_MAX_BUFFER_CHARS", 100)
    upstream = _FakeSSEUpstream(["data: " + "x" * 500])  # one unterminated oversized event

    chunks = _collect_sse(upstream)

    assert chunks == []
    assert upstream.closed is True


def test_split_complete_sse_events_matrix():
    buffer, events = main._split_complete_sse_events("a\n\nb\r\n\r\nc")
    assert events == ["a", "b"] and buffer == "c"
    buffer, events = main._split_complete_sse_events("tail\r\n\r")  # partial separator stays buffered
    assert events == [] and buffer == "tail\r\n\r"
    buffer, events = main._split_complete_sse_events("x\ry\n\n")
    assert events == ["x\ry"] and buffer == ""


# ---------------------------------------------------------------------------
# P2-1: async file wrappers must not rerun blocking work inline
# ---------------------------------------------------------------------------
def test_load_json_runtime_error_is_raised_not_rerun(monkeypatch, tmp_path):
    calls = []

    def refusing_sync(path):
        calls.append(path)
        raise RuntimeError("Refusing to read symlinked token state file.")

    monkeypatch.setattr(token_manager, "_load_json_sync", refusing_sync)
    with pytest.raises(RuntimeError, match="symlinked"):
        asyncio.run(token_manager._load_json(tmp_path / "auth_state.json"))
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# P2-4: unsupported reasoning effort is surfaced, not silently dropped
# ---------------------------------------------------------------------------
def test_unsupported_reasoning_effort_surfaces_route_warning(monkeypatch):
    metadata = {
        "mode": "semantic_research",
        "intent": "research",
        "handles": [],
        "query": "deep research topic",
        "count": 10,
        "sort": "relevance",
    }
    plan = resolve_plan(metadata, explicit_model="grok-custom-unknown")
    assert plan.route_warning and "reasoning_effort" in plan.route_warning

    async def fake_search(arguments):
        return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, arguments["model"])

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_search)
    monkeypatch.setattr(mcp_x_search.mcp_retrieve, "fetch_oembed_posts", _empty_oembed)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {"query": "deep research topic", "intent": "research", "model": "grok-custom-unknown"},
                },
            }
        )
    )
    structured = response["result"]["structuredContent"]
    assert any("reasoning_effort" in warning for warning in structured["warnings"])


async def _empty_oembed(status_ids, handles):
    return OEmbedResult(posts=[], warnings=[])


# ---------------------------------------------------------------------------
# P2-5: source dedup canonicalizes parameterized URLs
# ---------------------------------------------------------------------------
def _stage_payload_with_sources(sources):
    return {
        "items": [],
        "posts": [],
        "sources": [{"url": url, "title": title} for url, title in sources],
        "warnings": [],
        "retrieval_stages": [],
        "models_used": [],
    }


def test_source_dedup_collapses_tracking_url_variants():
    payload = _stage_payload_with_sources([("https://Example.com/article", "primary")])
    variant = _stage_payload_with_sources(
        [
            ("https://example.com/article?utm_source=x", "tracked"),
            ("https://example.com:443/article#section", "fragmented"),
            ("https://example.com/article?fbclid=abc", "click"),
        ]
    )
    merge_stage_payload(payload, variant)
    assert len(payload["sources"]) == 1


def test_source_dedup_keeps_semantic_query_variants_distinct():
    payload = _stage_payload_with_sources([("https://example.com/list?page=1", "page one")])
    variant = _stage_payload_with_sources([("https://example.com/list?page=2", "page two")])
    merge_stage_payload(payload, variant)
    assert len(payload["sources"]) == 2


def test_canonical_url_key_ordering_and_defaults():
    assert _canonical_url_key("https://a.com/p?b=2&a=1") == _canonical_url_key("https://A.com/p?a=1&b=2")
    assert _canonical_url_key("http://a.com:80/p") == _canonical_url_key("http://a.com/p")
    assert _source_key({"url": "https://example.com/a?utm_medium=post", "title": None}) == _source_key(
        {"url": "https://example.com/a", "title": None}
    )


# ---------------------------------------------------------------------------
# P2-6: malformed auth state shapes become typed errors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("content", ["123", '"weird"', "true"])
def test_non_dict_auth_state_raises_auth_required(monkeypatch, tmp_path, content):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    with pytest.raises(token_manager.AuthRequiredError):
        asyncio.run(token_manager.read_local_state())


def test_non_string_access_token_raises_auth_required(monkeypatch, tmp_path):
    state_path = tmp_path / "auth_state.json"
    broken = dict(_VALID_STATE)
    broken["access_token"] = {"nested": "object"}
    state_path.write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    with pytest.raises(token_manager.AuthRequiredError, match="invalid access_token"):
        asyncio.run(token_manager.read_local_state())
