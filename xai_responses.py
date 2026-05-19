"""xAI Responses API adapter used by MCP tools."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
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
        for key in ("id", "model", "status", "usage", "output", "citations")
        if response.get(key) is not None
    }


def _extract_citations(response: Dict[str, Any]) -> list[Dict[str, Any]]:
    citations: list[Dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("citations", "annotations", "sources"):
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
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


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


async def _headers(*, force_refresh: bool = False) -> Dict[str, str]:
    if force_refresh:
        token = await token_manager.get_access_token(force_refresh=True)
        auth_headers = {"Authorization": f"Bearer {token}"}
    else:
        auth_headers = await token_manager.get_auth_headers()
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **auth_headers,
    }


async def post(payload: Dict[str, Any]) -> ResponsesResult:
    headers = await _headers()
    client = await get_client()
    response = await client.post(XAI_RESPONSES_URL, headers=headers, json=payload)
    if response.status_code == 401:
        headers = await _headers(force_refresh=True)
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
    )
