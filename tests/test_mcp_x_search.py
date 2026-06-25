import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_x_search
import xai_responses


def test_build_x_search_tool_keeps_only_requested_options():
    tool = mcp_x_search._build_x_search_tool(
        {
            "allowed_x_handles": ["@xai", " elonmusk "],
            "from_date": "2026-05-18",
            "to_date": "2026-05-18",
            "enable_image_understanding": True,
            "enable_video_understanding": False,
        }
    )

    assert tool == {
        "type": "x_search",
        "allowed_x_handles": ["xai", "elonmusk"],
        "from_date": "2026-05-18",
        "to_date": "2026-05-18",
        "enable_image_understanding": True,
    }


def test_build_x_search_tool_keeps_date_only_to_date_inclusive():
    tool = mcp_x_search._build_x_search_tool({"from_date": "2026-05-18", "to_date": "2026-05-18"})

    assert tool["from_date"] == "2026-05-18"
    assert tool["to_date"] == "2026-05-18"


def test_build_x_search_tool_keeps_datetime_to_date_exact():
    tool = mcp_x_search._build_x_search_tool(
        {"from_date": "2026-05-18T00:00:00Z", "to_date": "2026-05-18T23:59:59Z"}
    )

    assert tool["from_date"] == "2026-05-18T00:00:00Z"
    assert tool["to_date"] == "2026-05-18T23:59:59Z"


def test_build_x_search_tool_supports_excluded_handles():
    tool = mcp_x_search._build_x_search_tool({"excluded_x_handles": ["@grok"]})

    assert tool == {"type": "x_search", "excluded_x_handles": ["grok"]}


def test_build_x_search_tool_rejects_conflicting_handle_filters():
    try:
        mcp_x_search._build_x_search_tool(
            {"allowed_x_handles": ["xai"], "excluded_x_handles": ["grok"]}
        )
    except ValueError as exc:
        assert "cannot be used together" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_x_search_tool_rejects_invalid_dates():
    try:
        mcp_x_search._build_x_search_tool({"from_date": "today"})
    except ValueError as exc:
        assert "ISO8601" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_x_search_tool_rejects_reversed_dates():
    try:
        mcp_x_search._build_x_search_tool({"from_date": "2026-05-20", "to_date": "2026-05-18"})
    except ValueError as exc:
        assert "from_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_x_search_tool_rejects_invalid_handles():
    try:
        mcp_x_search._build_x_search_tool({"allowed_x_handles": ["xai/evil"]})
    except ValueError as exc:
        assert "X handles" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_latest_posts_search_arguments_constrains_handle_and_dates():
    arguments, metadata = mcp_x_search._build_latest_posts_search_arguments(
        {"handle": "@logicrw", "count": 5, "from_date": "2026-05-01", "to_date": "2026-05-18"}
    )

    assert arguments["allowed_x_handles"] == ["logicrw"]
    assert arguments["from_date"] == "2026-05-01"
    assert arguments["to_date"] == "2026-05-18"
    assert metadata["handles"] == ["logicrw"]
    assert "Return up to 5 posts" in arguments["query"]
    assert "Preserve each post's text exactly as available" in arguments["query"]
    assert "Return only compact JSON" in arguments["query"]


def test_build_latest_posts_search_arguments_rejects_multiple_handles():
    try:
        mcp_x_search._build_latest_posts_search_arguments({"handle": "xai,openai"})
    except ValueError as exc:
        assert "single X handle" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_latest_posts_search_arguments_rejects_bad_count():
    try:
        mcp_x_search._build_latest_posts_search_arguments({"handle": "xai", "count": 30})
    except ValueError as exc:
        assert "between 1 and 20" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_compile_time_range_parses_week_before_last():
    compiled = mcp_x_search._compile_time_range({"time_range": "上上周"}, today=mcp_x_search.date(2026, 5, 18))

    assert compiled["from_date"] == "2026-05-04"
    assert compiled["to_date"] == "2026-05-10"
    assert compiled["compiled"] is True


def test_compile_time_range_parses_month_day_range_with_local_year():
    compiled = mcp_x_search._compile_time_range(
        {"time_range": "4月1日到4月2日"}, today=mcp_x_search.date(2026, 5, 18)
    )

    assert compiled["from_date"] == "2026-04-01"
    assert compiled["to_date"] == "2026-04-02"
    assert "2026" in compiled["assumption"]


def test_compile_time_range_marks_unparsed_text():
    compiled = mcp_x_search._compile_time_range({"time_range": "AI寒武纪之后那段时间"})

    assert compiled["compiled"] is False
    assert compiled["from_date"] is None
    assert compiled["to_date"] is None


def test_build_posts_search_arguments_supports_flexible_filters():
    arguments, metadata = mcp_x_search._build_posts_search_arguments(
        {
            "handles": ["@logicrw", "xai"],
            "query": "Hermes Agent",
            "time_range": "上个月",
            "count": 7,
            "sort": "relevance",
            "include_replies": False,
            "include_reposts": False,
            "best_effort_filters": {"min_views": 10000000},
        }
    )

    assert arguments["allowed_x_handles"] == ["logicrw", "xai"]
    assert metadata["count"] == 7
    assert metadata["sort"] == "relevance"
    assert metadata["best_effort_filters"] == {"min_views": 10000000}
    assert "Hermes Agent" in arguments["query"]
    assert "views >= 10000000" in arguments["query"]
    assert "Exclude replies" in arguments["query"]


def test_build_posts_search_arguments_requires_handle_or_query():
    try:
        mcp_x_search._build_posts_search_arguments({"time_range": "上个月"})
    except ValueError as exc:
        assert "at least one handle or a query" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_posts_search_arguments_accepts_retrieve_query_contract_and_rejects_over_limit():
    try:
        mcp_x_search._build_posts_search_arguments({"handles": ["xai"], "unknown": True})
    except ValueError as exc:
        assert "unsupported argument keys" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    query_at_limit = "x" * mcp_x_search.mcp_posts.POST_QUERY_MAX_CHARS
    arguments, metadata = mcp_x_search._build_posts_search_arguments({"query": query_at_limit})
    assert metadata["query"] == query_at_limit
    assert isinstance(arguments["query"], str)
    payload = mcp_x_search._x_search_payload(arguments)
    assert query_at_limit in payload["input"]

    try:
        mcp_x_search._build_posts_search_arguments({"query": "x" * (mcp_x_search.mcp_posts.POST_QUERY_MAX_CHARS + 1)})
    except ValueError as exc:
        assert f"at most {mcp_x_search.mcp_posts.POST_QUERY_MAX_CHARS}" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_x_search_payload_rejects_unknown_keys_and_long_query():
    try:
        mcp_x_search._x_search_payload({"query": "hello", "unknown": True})
    except ValueError as exc:
        assert "unsupported argument keys" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        mcp_x_search._x_search_payload({"query": ["xai"]})
    except ValueError as exc:
        assert "query must be a string" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    payload = mcp_x_search._x_search_payload({"query": "x" * mcp_x_search.X_SEARCH_INPUT_MAX_CHARS})
    assert len(payload["input"]) == mcp_x_search.X_SEARCH_INPUT_MAX_CHARS

    try:
        mcp_x_search._x_search_payload({"query": "x" * (mcp_x_search.X_SEARCH_INPUT_MAX_CHARS + 1)})
    except ValueError as exc:
        assert f"at most {mcp_x_search.X_SEARCH_INPUT_MAX_CHARS}" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_extract_output_text_supports_responses_content_shape():
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "first"},
                    {"type": "output_text", "text": "second"},
                ],
            }
        ]
    }

    assert mcp_x_search._extract_output_text(response) == "first\nsecond"


def test_tools_list_returns_only_retrieve_tool_by_default():
    response = asyncio.run(mcp_x_search._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    tools = {tool["name"]: tool for tool in response["result"]["tools"]}

    assert set(tools) == {"x_retrieve"}
    assert tools["x_retrieve"]["inputSchema"].get("required", []) == []
    assert tools["x_retrieve"]["outputSchema"]["properties"]["schema_version"]["const"] == "x_retrieve.v1"
    assert "query" in tools["x_retrieve"]["inputSchema"]["properties"]
    assert tools["x_retrieve"]["inputSchema"]["properties"]["query"]["maxLength"] == 2000
    assert "handles" in tools["x_retrieve"]["inputSchema"]["properties"]
    assert "lookback_days" in tools["x_retrieve"]["inputSchema"]["properties"]
    to_date_description = tools["x_retrieve"]["inputSchema"]["properties"]["to_date"]["description"]
    assert "passed through unchanged" in to_date_description
    assert "normalized by the proxy" not in to_date_description
    assert "anyOf" not in tools["x_retrieve"]["inputSchema"]
    assert tools["x_retrieve"]["outputSchema"]["properties"]["timeline_verified"]["const"] is False
    assert "items" in tools["x_retrieve"]["outputSchema"]["required"]
    assert "groups" in tools["x_retrieve"]["outputSchema"]["required"]
    assert "posts" in tools["x_retrieve"]["outputSchema"]["required"]
    assert "filter_reliability" in tools["x_retrieve"]["outputSchema"]["required"]
    assert "source_extraction_status" in tools["x_retrieve"]["outputSchema"]["required"]


def test_initialize_uses_structured_content_protocol_version():
    response = asyncio.run(mcp_x_search._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))

    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_initialize_can_echo_supported_legacy_protocol_version():
    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
    )

    assert response["result"]["protocolVersion"] == "2024-11-05"


def test_tools_call_rejects_invalid_params():
    response = asyncio.run(
        mcp_x_search._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "bad"})
    )

    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "invalid params"


def test_tools_call_rejects_invalid_arguments():
    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": "bad"},
            }
        )
    )

    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "arguments must be an object"


def test_tools_list_respects_allowlist(monkeypatch):
    monkeypatch.setattr(mcp_x_search.config, "GROK_GATEWAY_MCP_TOOL_ALLOWLIST", [])

    response = asyncio.run(mcp_x_search._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    assert response["result"]["tools"] == []


def test_tools_call_respects_allowlist(monkeypatch):
    monkeypatch.setattr(mcp_x_search.config, "GROK_GATEWAY_MCP_TOOL_ALLOWLIST", [])

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": {"query": "latest @xai posts"}},
            }
        )
    )

    assert response["error"]["code"] == -32602
    assert "GROK_GATEWAY_MCP_TOOL_ALLOWLIST" in response["error"]["message"]


def test_removed_tool_names_return_clear_error():
    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "x_latest_posts", "arguments": {"handle": "xai"}},
            }
        )
    )

    assert response["error"]["code"] == -32602
    assert "removed" in response["error"]["message"]
    assert "x_retrieve" in response["error"]["message"]


def test_tools_call_wraps_retrieve_research_result(monkeypatch):
    before = mcp_x_search._x_search_total_count

    async def fake_call(arguments):
        assert "latest @xai posts" in arguments["query"]
        return xai_responses.ResponsesResult(
            '{"posts":[{"text":"searched","author":"xai","url":"https://x.com/xai/status/1"}]}',
            {},
            [{"url": "https://x.com/xai/status/1"}],
            {"input_tokens": 1},
            "grok-4.3",
            inline_citations=[{"url": "https://x.com/xai/status/1"}],
            credential_source="xai-oauth",
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": {"query": "latest @xai posts"}},
            }
        )
    )

    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["schema_version"] == "x_retrieve.v1"
    assert structured["tool"] == "x_retrieve"
    assert structured["mode"] == "semantic_research"
    assert structured["items"][0]["text"] == "searched"
    assert structured["items"][0]["url"] == "https://x.com/xai/status/1"
    assert structured["sources"] == [{"url": "https://x.com/xai/status/1"}]
    assert structured["request"]["query"] == "latest @xai posts"
    assert mcp_x_search._x_search_total_count == before + 1


def test_tools_call_sanitizes_upstream_error(monkeypatch):
    async def fake_call(arguments):
        raise RuntimeError(
            "xAI Responses request failed with upstream status 500: refresh_token=super-secret Authorization: Bearer abc"
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "x_retrieve", "arguments": {"query": "latest @xai posts"}},
            }
        )
    )

    text = response["result"]["content"][0]["text"]
    assert response["result"]["isError"] is True
    assert "super-secret" not in text
    assert "Bearer abc" not in text


def test_xai_responses_post_sanitizes_upstream_body(monkeypatch):
    async def fake_auth_context(*, force_refresh=False):
        return {"headers": {"Authorization": "Bearer local-token"}, "credential_source": "xai-oauth"}

    def handler(request):
        return httpx.Response(
            500,
            request=request,
            text="refresh_token=super-secret Authorization: Bearer upstream-secret user@example.com",
        )

    async def run():
        monkeypatch.setattr(xai_responses.token_manager, "get_auth_context", fake_auth_context)
        xai_responses._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        xai_responses._client_loop = asyncio.get_running_loop()
        try:
            try:
                await xai_responses.post({"model": "grok-4.3", "input": "hi", "tools": [{"type": "x_search"}]})
            except RuntimeError as exc:
                message = str(exc)
            else:
                raise AssertionError("expected RuntimeError")
        finally:
            await xai_responses.aclose_client()
        return message

    message = asyncio.run(run())

    assert "500" in message
    assert "super-secret" not in message
    assert "upstream-secret" not in message
    assert "user@example.com" not in message


def test_xai_responses_retries_once_on_401(monkeypatch):
    calls = {"count": 0, "force_refresh": []}

    async def fake_auth_context(*, force_refresh=False):
        calls["force_refresh"].append(force_refresh)
        token = "fresh" if force_refresh else "stale"
        return {"headers": {"Authorization": f"Bearer {token}"}, "credential_source": "xai-oauth"}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            assert request.headers["authorization"] == "Bearer stale"
            return httpx.Response(401, request=request, text="expired")
        assert request.headers["authorization"] == "Bearer fresh"
        return httpx.Response(200, request=request, json={"output_text": "ok", "model": "grok-4.3"})

    async def run():
        monkeypatch.setattr(xai_responses.token_manager, "get_auth_context", fake_auth_context)
        xai_responses._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        xai_responses._client_loop = asyncio.get_running_loop()
        try:
            return await xai_responses.post({"model": "grok-4.3", "input": "hi", "tools": [{"type": "x_search"}]})
        finally:
            await xai_responses.aclose_client()

    result = asyncio.run(run())

    assert result.text == "ok"
    assert result.credential_source == "xai-oauth"
    assert calls["count"] == 2
    assert calls["force_refresh"] == [False, True]


def test_xai_responses_caps_citation_sources():
    citations = [{"url": f"https://x.com/xai/status/{idx}", "text": "x" * 3000} for idx in range(25)]

    extracted = xai_responses._extract_citations({"citations": citations})

    assert len(extracted) == 20
    assert extracted[0]["truncated"] is True
    assert len(extracted[0]["raw"]) <= 2048


def test_tools_call_wraps_retrieve_latest_by_handle_result(monkeypatch):
    before = mcp_x_search._x_search_total_count
    seen = {}

    async def fake_call(arguments):
        seen.update(arguments)
        return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, "grok-4.3")

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {
                        "handles": ["@logicrw"],
                        "sort": "latest",
                        "count": 3,
                    },
                },
            }
        )
    )

    text = response["result"]["content"][0]["text"]
    assert json.loads(text)["tool"] == "x_retrieve"
    assert seen["allowed_x_handles"] == ["logicrw"]
    assert "Return up to 3 posts" in seen["query"]
    assert response["result"]["structuredContent"]["mode"] == "latest_by_handle"
    assert response["result"]["structuredContent"]["request"]["lookback_days"] == 30
    assert response["result"]["structuredContent"]["posts"] == []
    assert response["result"]["structuredContent"]["items"] == []
    assert mcp_x_search._x_search_total_count == before + 1


def test_tools_call_wraps_retrieve_posts_result(monkeypatch):
    before = mcp_x_search._x_search_total_count
    seen = {}

    async def fake_call(arguments):
        seen.update(arguments)
        return xai_responses.ResponsesResult(
            '{"posts":[{"text":"hello"}]}',
            {},
            [{"url": "https://x.com/xai/status/1"}],
            None,
            "grok-4.3",
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {"handles": ["logicrw"], "query": "Hermes", "intent": "posts", "time_range": "上上周"},
                },
            }
        )
    )

    text = response["result"]["content"][0]["text"]
    assert json.loads(text)["tool"] == "x_retrieve"
    assert seen["allowed_x_handles"] == ["logicrw"]
    assert "Hermes" in seen["query"]
    assert response["result"]["structuredContent"]["posts"][0]["text"] == "hello"
    assert response["result"]["structuredContent"]["items"][0]["text"] == "hello"
    assert response["result"]["structuredContent"]["schema_version"] == "x_retrieve.v1"
    assert response["result"]["structuredContent"]["source_extraction_status"] == "extracted_unmapped"
    assert response["result"]["structuredContent"]["sources"] == [{"url": "https://x.com/xai/status/1"}]
    assert mcp_x_search._x_search_total_count == before + 1


def test_tools_call_runs_raw_expansion_when_quality_gate_fails(monkeypatch):
    calls = []

    async def fake_call(arguments):
        calls.append(dict(arguments))
        if len(calls) == 1:
            return xai_responses.ResponsesResult(
                '{"posts":[{"text":"stable candidate","author":"xai"}]}',
                {},
                [],
                None,
                "grok-4.3",
            )
        return xai_responses.ResponsesResult(
            '{"posts":[{"text":"raw candidate","author":"xai","url":"https://x.com/xai/status/2"}]}',
            {},
            [{"url": "https://x.com/xai/status/2"}],
            None,
            mcp_x_search.mcp_retrieve.RAW_MODEL,
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {"query": "find the original post", "intent": "source_discovery"},
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert len(calls) == 2
    assert calls[1]["model"] == mcp_x_search.mcp_retrieve.RAW_MODEL
    assert "Expand raw candidate X posts" in calls[1]["query"]
    assert [item["text"] for item in structured["items"]] == ["stable candidate", "raw candidate"]
    assert structured["retrieval_stages"][-1]["name"] == "raw_expansion"
    assert structured["models_used"] == ["grok-4.3", mcp_x_search.mcp_retrieve.RAW_MODEL]


def test_tools_call_can_disable_raw_expansion_with_stable_only(monkeypatch):
    calls = []

    async def fake_call(arguments):
        calls.append(dict(arguments))
        return xai_responses.ResponsesResult(
            '{"posts":[{"text":"stable candidate","author":"xai"}]}',
            {},
            [],
            None,
            "grok-4.3",
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {
                        "query": "find the original post",
                        "intent": "source_discovery",
                        "model_policy": "stable_only",
                    },
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert len(calls) == 1
    assert structured["retrieval_stages"][-1]["status"] == "skipped"
    assert structured["models_used"] == ["grok-4.3"]


def test_tools_call_respects_require_original_text_quality_gate(monkeypatch):
    calls = []

    async def fake_call(arguments):
        calls.append(dict(arguments))
        if len(calls) == 1:
            return xai_responses.ResponsesResult(
                '{"posts":[{"text":"","author":"xai","url":"https://x.com/xai/status/1"}]}',
                {},
                [{"url": "https://x.com/xai/status/1"}],
                None,
                "grok-4.3",
            )
        return xai_responses.ResponsesResult(
            '{"posts":[{"text":"raw text","author":"xai","url":"https://x.com/xai/status/2"}]}',
            {},
            [{"url": "https://x.com/xai/status/2"}],
            None,
            mcp_x_search.mcp_retrieve.RAW_MODEL,
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {
                        "query": "structured post lookup",
                        "intent": "posts",
                        "quality": {"require_original_text": True},
                    },
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert len(calls) == 2
    assert structured["items"][-1]["text"] == "raw text"
    assert structured["retrieval_stages"][-1]["status"] == "success"


def test_tools_call_sanitizes_raw_expansion_failure_warning(monkeypatch):
    calls = []

    async def fake_call(arguments):
        calls.append(dict(arguments))
        if len(calls) == 1:
            return xai_responses.ResponsesResult(
                '{"posts":[{"text":"stable candidate","author":"xai"}]}',
                {},
                [],
                None,
                "grok-4.3",
            )
        raise ValueError("raw failed refresh_token=super-secret Authorization: Bearer abcdefghi")

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {"query": "find source", "intent": "source_discovery"},
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    warning_text = " ".join(structured["warnings"])
    assert response["result"]["isError"] is False
    assert structured["retrieval_stages"][-1]["status"] == "failed"
    assert "super-secret" not in warning_text
    assert "abcdefghi" not in warning_text


def test_posts_result_does_not_trust_model_contract_fields(monkeypatch):
    async def fake_call(arguments):
        return xai_responses.ResponsesResult(
            json.dumps(
                {
                    "schema_version": "evil",
                    "backend": "official_x_api",
                    "timeline_verified": True,
                    "source_limit": "official timeline",
                    "query_compiled": {"handles": ["evil"], "sort": "official"},
                    "filter_reliability": {"date": "official_x_api"},
                    "posts": [{"text": "hello", "author": "xai"}],
                }
            ),
            {},
            [],
            None,
            "grok-4.3",
        )

    monkeypatch.setattr(mcp_x_search, "_call_x_search_result", fake_call)

    response = asyncio.run(
        mcp_x_search._handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "x_retrieve",
                    "arguments": {"handles": ["xai"], "query": "Hermes", "intent": "source_discovery", "time_range": "上上周"},
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert structured["schema_version"] == "x_retrieve.v1"
    assert structured["backend"] == "xai_x_search_orchestrated"
    assert structured["timeline_verified"] is False
    assert structured["source_limit"].startswith("Generated retrieval")
    assert structured["request"]["handles"] == ["xai"]
    assert structured["filter_reliability"]["date"] == "x_search_tool_parameter"
    assert json.loads(response["result"]["content"][0]["text"]) == structured


def test_metrics_lines_include_x_retrieve_counters():
    lines = "\n".join(mcp_x_search.metrics_lines())

    assert "mcp_x_retrieve_requests_total" in lines
    assert "mcp_x_retrieve_concurrency_limit" in lines
    assert "mcp_x_retrieve_quality_gate_total" in lines
    assert "mcp_x_retrieve_raw_expansion_total" in lines


def test_unknown_tool_returns_protocol_error():
    response = asyncio.run(
        mcp_x_search._handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "other"}}
        )
    )

    assert response["error"]["code"] == -32602
