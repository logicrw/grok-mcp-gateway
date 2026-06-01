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
        {"handle": "@0xlogicrw", "count": 5, "from_date": "2026-05-01", "to_date": "2026-05-18"}
    )

    assert arguments["allowed_x_handles"] == ["0xlogicrw"]
    assert arguments["from_date"] == "2026-05-01"
    assert arguments["to_date"] == "2026-05-18"
    assert metadata["handles"] == ["0xlogicrw"]
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
            "handles": ["@0xlogicrw", "xai"],
            "query": "Hermes Agent",
            "time_range": "上个月",
            "count": 7,
            "sort": "relevance",
            "include_replies": False,
            "include_reposts": False,
            "best_effort_filters": {"min_views": 10000000},
        }
    )

    assert arguments["allowed_x_handles"] == ["0xlogicrw", "xai"]
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


def test_build_posts_search_arguments_rejects_unknown_keys_and_long_query():
    try:
        mcp_x_search._build_posts_search_arguments({"handles": ["xai"], "unknown": True})
    except ValueError as exc:
        assert "unsupported argument keys" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        mcp_x_search._build_posts_search_arguments({"query": "x" * 501})
    except ValueError as exc:
        assert "at most 500" in str(exc)
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

    try:
        mcp_x_search._x_search_payload({"query": "x" * 2001})
    except ValueError as exc:
        assert "at most 2000" in str(exc)
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


def test_tools_list_returns_search_posts_and_latest_posts_tools():
    response = asyncio.run(mcp_x_search._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    tools = {tool["name"]: tool for tool in response["result"]["tools"]}

    assert set(tools) == {"x_search", "x_posts", "x_latest_posts"}
    assert tools["x_search"]["inputSchema"]["required"] == ["query"]
    assert tools["x_search"]["outputSchema"]["properties"]["schema_version"]["const"] == "x_search.v1"
    assert "from_date" in tools["x_search"]["inputSchema"]["properties"]
    assert "to_date" in tools["x_search"]["inputSchema"]["properties"]
    to_date_description = tools["x_search"]["inputSchema"]["properties"]["to_date"]["description"]
    assert "passed through unchanged" in to_date_description
    assert "normalized by the proxy" not in to_date_description
    assert "excluded_x_handles" in tools["x_search"]["inputSchema"]["properties"]
    assert "time_range" in tools["x_posts"]["inputSchema"]["properties"]
    assert "best_effort_filters" in tools["x_posts"]["inputSchema"]["properties"]
    assert "anyOf" not in tools["x_posts"]["inputSchema"]
    assert tools["x_posts"]["outputSchema"]["properties"]["timeline_verified"]["const"] is False
    assert "sources" in tools["x_posts"]["outputSchema"]["required"]
    assert "source_extraction_status" in tools["x_posts"]["outputSchema"]["required"]
    assert tools["x_latest_posts"]["inputSchema"]["required"] == ["handle"]
    assert "count" in tools["x_latest_posts"]["inputSchema"]["properties"]


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
                "params": {"name": "x_search", "arguments": "bad"},
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
                "params": {"name": "x_search", "arguments": {"query": "latest @xai posts"}},
            }
        )
    )

    assert response["error"]["code"] == -32602
    assert "GROK_GATEWAY_MCP_TOOL_ALLOWLIST" in response["error"]["message"]


def test_tools_call_wraps_search_result(monkeypatch):
    before = mcp_x_search._x_search_total_count

    async def fake_call(arguments):
        assert arguments == {"query": "latest @xai posts"}
        return xai_responses.ResponsesResult(
            "searched",
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
                "params": {"name": "x_search", "arguments": {"query": "latest @xai posts"}},
            }
        )
    )

    assert response["result"]["content"] == [{"type": "text", "text": "searched"}]
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["schema_version"] == "x_search.v1"
    assert structured["answer"] == "searched"
    assert structured["citations"] == [{"url": "https://x.com/xai/status/1"}]
    assert structured["inline_citations"] == [{"url": "https://x.com/xai/status/1"}]
    assert structured["credential_source"] == "xai-oauth"
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
                "params": {"name": "x_search", "arguments": {"query": "latest @xai posts"}},
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


def test_tools_call_wraps_latest_posts_result(monkeypatch):
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
                    "name": "x_latest_posts",
                    "arguments": {
                        "handle": "@0xlogicrw",
                        "count": 3,
                        "from_date": "2026-05-01",
                        "to_date": "2026-05-18",
                    },
                },
            }
        )
    )

    text = response["result"]["content"][0]["text"]
    assert json.loads(text)["tool"] == "x_latest_posts"
    assert seen["allowed_x_handles"] == ["0xlogicrw"]
    assert "Return up to 3 posts" in seen["query"]
    assert response["result"]["structuredContent"]["posts"] == []
    assert response["result"]["structuredContent"]["alias_of"] == "x_posts"
    assert mcp_x_search._x_search_total_count == before + 1


def test_tools_call_wraps_posts_result(monkeypatch):
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
                    "name": "x_posts",
                    "arguments": {"handles": ["0xlogicrw"], "query": "Hermes", "time_range": "上上周"},
                },
            }
        )
    )

    text = response["result"]["content"][0]["text"]
    assert json.loads(text)["tool"] == "x_posts"
    assert seen["allowed_x_handles"] == ["0xlogicrw"]
    assert "Hermes" in seen["query"]
    assert response["result"]["structuredContent"]["posts"][0]["text"] == "hello"
    assert response["result"]["structuredContent"]["schema_version"] == "x_posts.v1"
    assert response["result"]["structuredContent"]["source_extraction_status"] == "extracted_unmapped"
    assert response["result"]["structuredContent"]["sources"] == [{"url": "https://x.com/xai/status/1"}]
    assert mcp_x_search._x_search_total_count == before + 1


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
                    "name": "x_posts",
                    "arguments": {"handles": ["xai"], "query": "Hermes", "time_range": "上上周"},
                },
            }
        )
    )

    structured = response["result"]["structuredContent"]
    assert structured["schema_version"] == "x_posts.v1"
    assert structured["backend"] == "xai_x_search_generated"
    assert structured["timeline_verified"] is False
    assert structured["source_limit"].startswith("Generated extraction")
    assert structured["request"]["handles"] == ["xai"]
    assert structured["filter_reliability"]["date"] == "x_search_tool_parameter"
    assert json.loads(response["result"]["content"][0]["text"]) == structured


def test_metrics_lines_include_x_search_counters():
    lines = "\n".join(mcp_x_search.metrics_lines())

    assert "mcp_x_search_requests_total" in lines
    assert "mcp_x_search_concurrency_limit" in lines


def test_unknown_tool_returns_protocol_error():
    response = asyncio.run(
        mcp_x_search._handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "other"}}
        )
    )

    assert response["error"]["code"] == -32602
