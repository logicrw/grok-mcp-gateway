import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieve.oembed import merge_oembed_posts
from x_oembed import OEmbedPost, fetch_oembed_posts


def test_fetch_oembed_posts_extracts_paragraph_text():
    def handler(request):
        assert request.url.params["url"] == "https://x.com/i/status/2071385784154759468"
        html = (
            '<blockquote class="twitter-tweet">'
            '<p lang="en" dir="ltr">First line <a href="https://t.co/example">link</a></p>'
            "&mdash; xAI (@xai)"
            "</blockquote>"
        )
        return httpx.Response(
            200,
            request=request,
            json={"html": html, "author_url": "https://twitter.com/xai"},
        )

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


def test_fetch_oembed_posts_rejects_untrusted_redirect():
    def handler(request):
        return httpx.Response(302, request=request, headers={"Location": "https://example.com/oembed"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_oembed_posts(["2071385784154759468"], [], client=client)

    result = asyncio.run(run())

    assert result.posts == []
    assert result.warnings == ["public oEmbed failed for target status 2071385784154759468"]


def test_merge_oembed_preserves_metadata_and_deduplicates_by_status_id():
    status_id = "2071385784154759468"
    payload = {
        "items": [
            {
                "id": status_id,
                "url": f"https://x.com/xai/status/{status_id}",
                "author": "verified_author",
                "created_at": "2026-07-13T00:00:00Z",
                "text": "",
                "metrics": {"likes": 42},
                "relation": "primary",
                "confidence": "medium",
                "warnings": ["existing warning"],
                "citation_backed": False,
            }
        ],
        "posts": [
            {
                "url": f"https://x.com/xai/status/{status_id}",
                "author": "verified_author",
                "created_at": "2026-07-13T00:00:00Z",
                "text": "",
                "metrics": {"likes": 42},
            }
        ],
        "sources": [{"url": f"https://x.com/xai/status/{status_id}", "title": "stable"}],
        "groups": {},
        "source_extraction_status": "available",
    }

    merge_oembed_posts(
        payload,
        [OEmbedPost(status_id, f"https://x.com/i/status/{status_id}", None, "public embed text")],
    )

    assert len(payload["items"]) == 1
    assert len(payload["posts"]) == 1
    assert len(payload["sources"]) == 1
    assert payload["items"][0]["author"] == "verified_author"
    assert payload["items"][0]["created_at"] == "2026-07-13T00:00:00Z"
    assert payload["items"][0]["metrics"] == {"likes": 42}
    assert payload["items"][0]["warnings"] == ["existing warning"]
    assert payload["items"][0]["text"] == "public embed text"
