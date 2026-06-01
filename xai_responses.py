"""xAI Responses API adapter used by MCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

import config
from error_sanitizer import sanitize_text, upstream_error_message
import token_manager

logger = logging.getLogger(__name__)

XAI_RESPONSES_URL = f"{token_manager.XAI_API_BASE}/v1/responses"
_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None
_client_lock: Optional[asyncio.Lock] = None
_client_lock_loop: Optional[asyncio.AbstractEventLoop] = None


@dataclass
class ResponsesResult:
    text: str
    compact: Dict[str, Any]
    citations: list[Dict[str, Any]]
    usage: Any
    model: str
    inline_citations: list[Dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    credential_source: str = "unknown"

    def raw_json(self) -> str:
        return json.dumps(self.compact, ensure_ascii=False, separators=(",", ":"))


def _extract_output_text(response: Dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def _compact_response(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: response.get(key)
        for key in ("id", "model", "status", "usage", "output", "citations", "inline_citations", "degraded")
        if response.get(key) is not None
    }


def _bounded_source(item: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "type",
        "url",
        "title",
        "snippet",
        "text",
        "source",
        "id",
        "start_index",
        "end_index",
        "post_id",
        "tweet_id",
    }
    compact = {key: item[key] for key in allowed if key in item}
    if not compact:
        compact = dict(item)
    serialized = json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) > 2048:
        return {
            "type": str(compact.get("type") or "xai_citation"),
            "truncated": True,
            "raw": sanitize_text(serialized)[:2048],
        }
    return compact


def _extract_named_sources(response: Dict[str, Any], names: tuple[str, ...], *, max_items: int = 20) -> list[Dict[str, Any]]:
    citations: list[Dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in names:
                nested = value.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, dict):
                            citations.append(item)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(response)
    seen: set[str] = set()
    unique: list[Dict[str, Any]] = []
    for item in citations:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        bounded = _bounded_source(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(bounded)
        if len(unique) >= max_items:
            break
    return unique


def _extract_citations(response: Dict[str, Any]) -> list[Dict[str, Any]]:
    return _extract_named_sources(response, ("citations", "annotations", "sources"))


def _extract_inline_citations(response: Dict[str, Any]) -> list[Dict[str, Any]]:
    return _extract_named_sources(response, ("inline_citations",))


def _extract_degraded(response: Dict[str, Any]) -> bool:
    if isinstance(response.get("degraded"), bool):
        return bool(response["degraded"])
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and isinstance(item.get("degraded"), bool):
                return bool(item["degraded"])
    return False


async def get_client() -> httpx.AsyncClient:
    global _client, _client_loop
    current_loop = asyncio.get_running_loop()
    lock = _client_creation_lock(current_loop)
    async with lock:
        if _client is None or _client.is_closed or _client_loop is not current_loop:
            old_client = _client
            if old_client is not None and not old_client.is_closed:
                try:
                    await old_client.aclose()
                except RuntimeError:
                    logger.debug("Could not close xAI Responses client from a previous event loop.")
            _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
            _client_loop = current_loop
    return _client


def _client_creation_lock(loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    global _client_lock, _client_lock_loop
    if _client_lock is None or _client_lock_loop is not loop:
        _client_lock = asyncio.Lock()
        _client_lock_loop = loop
    return _client_lock


async def aclose_client() -> None:
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None


async def _headers(*, force_refresh: bool = False) -> tuple[Dict[str, str], str]:
    context = await token_manager.get_auth_context(force_refresh=force_refresh)
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **context["headers"],
    }, str(context.get("credential_source") or "unknown")


async def post(payload: Dict[str, Any]) -> ResponsesResult:
    headers, credential_source = await _headers()
    client = await get_client()
    response = await client.post(XAI_RESPONSES_URL, headers=headers, json=payload)
    if response.status_code == 401:
        headers, credential_source = await _headers(force_refresh=True)
        response = await client.post(XAI_RESPONSES_URL, headers=headers, json=payload)
    if response.status_code >= 400:
        if config.GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS:
            logger.debug("xAI Responses upstream error body: %s", sanitize_text(response.text))
        raise RuntimeError(upstream_error_message("xAI Responses", response.status_code))
    try:
        data = response.json()
    except ValueError as exc:
        if config.GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS:
            logger.debug("xAI Responses invalid JSON body: %s", sanitize_text(response.text))
        raise RuntimeError("xAI Responses returned invalid JSON") from exc

    return ResponsesResult(
        text=_extract_output_text(data),
        compact=_compact_response(data),
        citations=_extract_citations(data),
        usage=data.get("usage"),
        model=str(data.get("model") or payload.get("model") or ""),
        inline_citations=_extract_inline_citations(data),
        degraded=_extract_degraded(data),
        credential_source=credential_source,
    )
