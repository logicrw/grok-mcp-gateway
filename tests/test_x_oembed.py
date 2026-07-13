import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from x_oembed import fetch_oembed_posts


def test_fetch_oembed_posts_extracts_paragraph_text():
    def handler(request):
        assert request.url.params["url"] == "https://x.com/i/status/2071385784154759468"
        html = (
            '<blockquote class="twitter-tweet">'
            '<p lang="en" dir="ltr">First line <a href="https://t.co/example">link</a></p>'
            "&mdash; xAI (@xai)"
            "</blockquote>"
        )
        return httpx.Response(200, request=request, json={"html": html})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_oembed_posts(["2071385784154759468"], ["xai"], client=client)

    result = asyncio.run(run())

    assert result.warnings == []
    assert len(result.posts) == 1
    assert result.posts[0].text == "First line link"
    assert result.posts[0].author == "xai"
    assert result.posts[0].url == "https://x.com/i/status/2071385784154759468"


def test_fetch_oembed_posts_follows_publish_x_redirect():
    def handler(request):
        if request.url.host == "publish.twitter.com":
            return httpx.Response(301, request=request, headers={"Location": str(request.url.copy_with(host="publish.x.com"))})
        html = '<blockquote><p>Redirected text</p></blockquote>'
        return httpx.Response(200, request=request, json={"html": html})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_oembed_posts(["2071385784154759468"], ["xai"], client=client)

    result = asyncio.run(run())

    assert result.warnings == []
    assert result.posts[0].text == "Redirected text"


def test_fetch_oembed_posts_reports_unavailable_status():
    def handler(request):
        return httpx.Response(404, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_oembed_posts(["2071385784154759468"], [], client=client)

    result = asyncio.run(run())

    assert result.posts == []
    assert result.warnings == ["public oEmbed unavailable for target status 2071385784154759468"]
