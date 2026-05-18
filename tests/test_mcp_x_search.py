import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_x_search


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
        "to_date": "2026-05-19",
        "enable_image_understanding": True,
    }


def test_build_x_search_tool_treats_date_only_to_date_as_inclusive():
    tool = mcp_x_search._build_x_search_tool({"from_date": "2026-05-18", "to_date": "2026-05-18"})

    assert tool["from_date"] == "2026-05-18"
    assert tool["to_date"] == "2026-05-19"


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
            "sort": "popular",
            "include_replies": False,
            "include_reposts": False,
            "engagement_filter": {"min_views": 10000000},
        }
    )

    assert arguments["allowed_x_handles"] == ["0xlogicrw", "xai"]
    assert metadata["count"] == 7
    assert metadata["sort"] == "popular"
    assert metadata["engagement_filter"] == {"min_views": 10000000}
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
    assert "from_date" in tools["x_search"]["inputSchema"]["properties"]
    assert "to_date" in tools["x_search"]["inputSchema"]["properties"]
    assert "excluded_x_handles" in tools["x_search"]["inputSchema"]["properties"]
    assert "time_range" in tools["x_posts"]["inputSchema"]["properties"]
    assert "engagement_filter" in tools["x_posts"]["inputSchema"]["properties"]
    assert tools["x_latest_posts"]["inputSchema"]["required"] == ["handle"]
    assert "count" in tools["x_latest_posts"]["inputSchema"]["properties"]


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
        return "searched"

    monkeypatch.setattr(mcp_x_search, "_call_x_search", fake_call)

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

    assert response["result"] == {"content": [{"type": "text", "text": "searched"}], "isError": False}
    assert mcp_x_search._x_search_total_count == before + 1


def test_tools_call_wraps_latest_posts_result(monkeypatch):
    before = mcp_x_search._x_search_total_count
    seen = {}

    async def fake_call(arguments):
        seen.update(arguments)
        return '{"posts":[]}'

    monkeypatch.setattr(mcp_x_search, "_call_x_search", fake_call)

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
    assert text.startswith("Tool: x_latest_posts")
    assert seen["allowed_x_handles"] == ["0xlogicrw"]
    assert "Return up to 3 posts" in seen["query"]
    assert response["result"]["structuredContent"]["posts"] == []
    assert mcp_x_search._x_search_total_count == before + 1


def test_tools_call_wraps_posts_result(monkeypatch):
    before = mcp_x_search._x_search_total_count
    seen = {}

    async def fake_call(arguments):
        seen.update(arguments)
        return '{"posts":[{"text":"hello"}]}'

    monkeypatch.setattr(mcp_x_search, "_call_x_search", fake_call)

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
    assert text.startswith("Tool: x_posts")
    assert seen["allowed_x_handles"] == ["0xlogicrw"]
    assert "Hermes" in seen["query"]
    assert response["result"]["structuredContent"]["posts"][0]["text"] == "hello"
    assert mcp_x_search._x_search_total_count == before + 1


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
