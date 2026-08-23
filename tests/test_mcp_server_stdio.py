from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server


def test_stdio_main_answers_ping_and_skips_notifications():
    async def run():
        reader = asyncio.StreamReader()
        writer = io.StringIO()
        reader.feed_data(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        reader.feed_data(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        reader.feed_eof()
        await mcp_server.stdio_main(reader, writer)
        return writer.getvalue()

    lines = [line for line in asyncio.run(run()).splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_stdio_main_returns_parse_error_for_invalid_json():
    async def run():
        reader = asyncio.StreamReader()
        writer = io.StringIO()
        reader.feed_data(b"{not-json}\n")
        reader.feed_eof()
        await mcp_server.stdio_main(reader, writer)
        return writer.getvalue()

    payload = json.loads(asyncio.run(run()).strip())
    assert payload["error"]["code"] == -32700
    assert payload["id"] is None
