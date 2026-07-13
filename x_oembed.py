from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final, Optional
from urllib.parse import urlparse

import httpx

import config

OEMBED_ENDPOINT: Final = "https://publish.twitter.com/oembed"
OEMBED_HOSTS: Final = {"publish.twitter.com", "publish.x.com"}
OEMBED_REDIRECT_LIMIT: Final = 3
OEMBED_TIMEOUT_SECONDS: Final = 8.0
USER_AGENT: Final = "grok-mcp-gateway/0.1 (+https://github.com/logicrw/grok-mcp-gateway)"


@dataclass(frozen=True)
class OEmbedPost:
    status_id: str
    url: str
    author: Optional[str]
    text: str


@dataclass(frozen=True)
class OEmbedResult:
    posts: list[OEmbedPost]
    warnings: list[str]


class TweetParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_paragraph = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "p":
            self._in_paragraph = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_paragraph:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(part.strip() for part in self._parts if part.strip()).strip()


async def fetch_oembed_posts(
    status_ids: list[str],
    _handles: list[str],
    *,
    client: Optional[httpx.AsyncClient] = None,
    concurrency: Optional[int] = None,
) -> OEmbedResult:
    posts: list[OEmbedPost] = []
    warnings: list[str] = []
    if not status_ids:
        return OEmbedResult(posts=posts, warnings=warnings)

    unique_status_ids = list(dict.fromkeys(status_ids))
    limit = concurrency or config.GROK_PROXY_RETRIEVE_OEMBED_CONCURRENCY
    semaphore = asyncio.Semaphore(limit)

    async def fetch_one(status_id: str, http_client: httpx.AsyncClient) -> OEmbedPost | str | None:
        async with semaphore:
            return await _fetch_one(http_client, status_id)

    if client is not None:
        results = await asyncio.gather(*(fetch_one(status_id, client) for status_id in unique_status_ids))
        for result in results:
            _append_oembed_result(posts, warnings, result)
        return OEmbedResult(posts=posts, warnings=warnings)

    timeout = httpx.Timeout(OEMBED_TIMEOUT_SECONDS, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as owned_client:
        results = await asyncio.gather(*(fetch_one(status_id, owned_client) for status_id in unique_status_ids))
        for result in results:
            _append_oembed_result(posts, warnings, result)
    return OEmbedResult(posts=posts, warnings=warnings)


async def _fetch_one(
    client: httpx.AsyncClient,
    status_id: str,
) -> OEmbedPost | str | None:
    url = f"https://x.com/i/status/{status_id}"
    try:
        response = await _get_oembed_response(client, url)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError:
        return f"public oEmbed unavailable for target status {status_id}"
    except (httpx.RequestError, ValueError):
        return f"public oEmbed failed for target status {status_id}"

    html = data.get("html") if isinstance(data, dict) else None
    text = _extract_tweet_text(html if isinstance(html, str) else "")
    if not text:
        return f"public oEmbed returned no text for target status {status_id}"
    return OEmbedPost(status_id=status_id, url=url, author=_author_from_response(data), text=text)


async def _get_oembed_response(client: httpx.AsyncClient, status_url: str) -> httpx.Response:
    response = await client.get(
        OEMBED_ENDPOINT,
        params={"url": status_url, "omit_script": "1"},
        follow_redirects=False,
    )
    for _ in range(OEMBED_REDIRECT_LIMIT):
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            return response
        redirect_url = response.url.join(location)
        if redirect_url.scheme != "https" or redirect_url.host not in OEMBED_HOSTS:
            raise httpx.RequestError("untrusted public oEmbed redirect", request=response.request)
        response = await client.get(redirect_url, follow_redirects=False)
    return response


def _append_oembed_result(posts: list[OEmbedPost], warnings: list[str], result: OEmbedPost | str | None) -> None:
    if isinstance(result, OEmbedPost):
        posts.append(result)
    elif isinstance(result, str):
        warnings.append(result)


def _extract_tweet_text(html: str) -> str:
    parser = TweetParagraphParser()
    parser.feed(html)
    return parser.text()


def _author_from_response(data: object) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    author_url = data.get("author_url")
    if not isinstance(author_url, str):
        return None
    parsed = urlparse(author_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    }:
        return None
    handle = parsed.path.strip("/").split("/", 1)[0]
    return handle or None
