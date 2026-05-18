import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_x_search


def test_build_x_search_tool_keeps_only_requested_options():
    tool = mcp_x_search._build_x_search_tool(
        {
            "allowed_x_handles": ["@xai", " elonmusk "],
            "enable_image_understanding": True,
            "enable_video_understanding": False,
        }
    )

    assert tool == {
        "type": "x_search",
        "allowed_x_handles": ["xai", "elonmusk"],
        "enable_image_understanding": True,
    }


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


def test_tools_list_returns_single_x_search_tool():
    response = asyncio.run(mcp_x_search._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    assert response["result"]["tools"][0]["name"] == "x_search"
    assert response["result"]["tools"][0]["inputSchema"]["required"] == ["query"]


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
