"""Grok MCP Gateway — local reverse proxy to api.x.ai using Hermes xAI OAuth tokens.

Runs independently on a local port (default 9996, scans upward if taken).
Features: token prewarm, Hermes auth.json watch, deep health, request logging,
upstream retry, Prometheus metrics.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import socket
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Mapping, Optional

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

import config
import mcp_server
import mcp_x_search
import token_manager
import xai_responses

logger = logging.getLogger(__name__)

httpx_client: httpx.AsyncClient
_bg_tasks: set[asyncio.Task] = set()

# Metrics state
_request_counts: defaultdict[str, int] = defaultdict(int)
_request_total_duration: float = 0.0
_request_total_count: int = 0

# Hermes watcher state
_last_hermes_mtime: float = 0.0

# RFC 9110 hop-by-hop headers plus sensitive client credentials that must never
# be forwarded to xAI. The proxy injects its own upstream OAuth Authorization.
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "authorization",
    "x-proxy-api-key",
    "cookie",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_startup_security(host: str, proxy_api_key: Optional[str]) -> None:
    """Refuse unauthenticated non-loopback binds."""
    if not _is_loopback_host(host) and not proxy_api_key:
        raise RuntimeError(
            "PROXY_API_KEY is required when PROXY_HOST is not loopback. "
            "This proxy injects live xAI OAuth credentials and must not be "
            "exposed without an authentication layer."
        )


def _request_has_valid_proxy_api_key(headers: Mapping[str, str], proxy_api_key: Optional[str]) -> bool:
    """Return True when optional local proxy auth is disabled or satisfied."""
    if not proxy_api_key:
        return True
    lowered = {k.lower(): v for k, v in headers.items()}
    x_proxy_key = lowered.get("x-proxy-api-key", "")
    if hmac.compare_digest(x_proxy_key, proxy_api_key):
        return True
    auth = lowered.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), proxy_api_key)


def _prepare_forward_headers(incoming_headers: Mapping[str, str], auth_headers: Mapping[str, str]) -> dict[str, str]:
    """Build upstream-safe headers for api.x.ai."""
    connection_tokens: set[str] = set()
    for key, value in incoming_headers.items():
        if key.lower() == "connection":
            connection_tokens.update(part.strip().lower() for part in value.split(",") if part.strip())

    blocked = _HOP_BY_HOP_HEADERS | connection_tokens
    forwarded: dict[str, str] = {}
    for key, value in incoming_headers.items():
        lowered_key = key.lower()
        if lowered_key in blocked or lowered_key.startswith("x-forwarded-"):
            continue
        forwarded[key] = value

    forwarded.update(auth_headers)
    forwarded.setdefault("user-agent", "grok-mcp-gateway/0.3")
    return forwarded


def _is_retry_safe_method(method: str) -> bool:
    """Return True for HTTP methods that can be retried without duplicating side effects."""
    return method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}


def _build_auto_x_search_tool() -> dict:
    tool: dict = {"type": "x_search"}
    if config.GROK_PROXY_X_SEARCH_ALLOWED_HANDLES:
        tool["allowed_x_handles"] = config.GROK_PROXY_X_SEARCH_ALLOWED_HANDLES
    if config.GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING:
        tool["enable_image_understanding"] = True
    if config.GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING:
        tool["enable_video_understanding"] = True
    return tool


def _maybe_inject_auto_x_search(method: str, path: str, body: bytes) -> bytes:
    """Add xAI X Search to Responses requests for clients that cannot attach tools."""
    if not config.GROK_PROXY_AUTO_X_SEARCH:
        return body
    if method.upper() != "POST" or path.strip("/") != "v1/responses":
        return body
    if not body:
        return body

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body

    if not isinstance(payload, dict):
        return body
    tools = payload.get("tools")
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        return body
    if any(isinstance(tool, dict) and tool.get("type") == "x_search" for tool in tools):
        return body

    payload["tools"] = [*tools, _build_auto_x_search_tool()]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _strip_auto_x_search_response_fields(response: dict) -> bool:
    changed = False
    tools = response.get("tools")
    if isinstance(tools, list):
        filtered_tools = [
            tool for tool in tools
            if not (isinstance(tool, dict) and tool.get("type") == "x_search")
        ]
        if len(filtered_tools) != len(tools):
            response["tools"] = filtered_tools
            changed = True

    output = response.get("output")
    if isinstance(output, list):
        filtered_output = [
            item for item in output
            if not (isinstance(item, dict) and item.get("type") == "custom_tool_call")
        ]
        if len(filtered_output) != len(output):
            response["output"] = filtered_output
            changed = True
    return changed


def _sanitize_auto_x_search_sse_event(block: str) -> Optional[str]:
    event_name = ""
    data_index: Optional[int] = None
    data_payload = ""
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:") and data_index is None:
            data_index = index
            data_payload = line.removeprefix("data:").strip()

    if event_name in {
        "response.custom_tool_call_input.delta",
        "response.custom_tool_call_input.done",
    }:
        return None
    if data_index is None or not data_payload:
        return block

    try:
        payload = json.loads(data_payload)
    except json.JSONDecodeError:
        return block

    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") == "custom_tool_call":
        return None

    changed = False
    response = payload.get("response")
    if isinstance(response, dict):
        changed = _strip_auto_x_search_response_fields(response)
    if not changed:
        return block

    lines[data_index] = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    return "\n".join(lines)


async def _iter_auto_x_search_compatible_sse(upstream: httpx.Response) -> AsyncGenerator[bytes, None]:
    buffer = ""
    try:
        async for chunk in upstream.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                sanitized = _sanitize_auto_x_search_sse_event(block)
                if sanitized:
                    yield f"{sanitized}\n\n".encode("utf-8")
        if buffer:
            sanitized = _sanitize_auto_x_search_sse_event(buffer)
            if sanitized:
                yield f"{sanitized}\n\n".encode("utf-8")
    finally:
        await upstream.aclose()


async def _preflight_startup() -> None:
    """Validate bind/auth settings and make sure OAuth state can be loaded."""
    _validate_startup_security(config.HOST, config.PROXY_API_KEY)
    await token_manager.read_local_state()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global httpx_client
    try:
        await _preflight_startup()
        logger.info("Token state ready.")
    except Exception as exc:
        logger.error("Failed to initialize token state: %s", exc.__class__.__name__)
        raise

    httpx_client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0),
    )

    # Background watchers
    t1 = asyncio.create_task(_token_watcher(), name="token-watcher")
    t2 = asyncio.create_task(_hermes_watcher(), name="hermes-watcher")
    _bg_tasks.update({t1, t2})

    yield

    # Cancel background tasks
    for t in list(_bg_tasks):
        t.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)
    _bg_tasks.clear()
    await httpx_client.aclose()
    await xai_responses.aclose_client()


app = FastAPI(title="Grok MCP Gateway", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Background watchers
# ---------------------------------------------------------------------------
async def _token_watcher() -> None:
    """Prewarm token before it expires."""
    while True:
        try:
            state = await token_manager.read_local_state()
            access_token = state.get("access_token", "")
            exp = token_manager.get_token_expiry(access_token)
            if exp:
                remaining = exp - time.time()
                if remaining < config.TOKEN_REFRESH_WINDOW:
                    logger.info("Token expiring in %.0fs, pre-refreshing...", remaining)
                    await token_manager.get_access_token(force_refresh=True)
                else:
                    logger.debug("Token healthy (expires in %.0fs)", remaining)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Token watcher error: %s", exc.__class__.__name__)
        await asyncio.sleep(max(30, config.TOKEN_REFRESH_WINDOW // 2))


async def _hermes_watcher() -> None:
    """Poll Hermes auth.json and re-import when it changes."""
    global _last_hermes_mtime
    while True:
        try:
            await asyncio.sleep(config.HERMES_POLL_INTERVAL)
            if not config.HERMES_AUTH_PATH.exists():
                continue
            mtime = config.HERMES_AUTH_PATH.stat().st_mtime
            if _last_hermes_mtime == 0:
                _last_hermes_mtime = mtime
                continue
            if mtime != _last_hermes_mtime:
                _last_hermes_mtime = mtime
                logger.info("Hermes auth.json changed, re-importing tokens...")
                hermes_state = await token_manager.load_from_hermes(config.HERMES_AUTH_PATH)
                if hermes_state and hermes_state.get("access_token") and hermes_state.get("client_id"):
                    await token_manager.save_local_state(hermes_state)
                    logger.info("Hermes tokens re-imported.")
                else:
                    logger.warning("Hermes auth.json changed but no complete xai-oauth credentials found.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Hermes watcher error: %s", exc.__class__.__name__)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def proxy_auth_middleware(request: Request, call_next):
    if config.PROXY_API_KEY:
        if not _request_has_valid_proxy_api_key(request.headers, config.PROXY_API_KEY):
            return Response(
                content=json.dumps({"error": "Unauthorized"}),
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    status = response.status_code
    method = request.method
    path = request.url.path
    key = f"{method}:{status}"
    _request_counts[key] += 1
    global _request_total_duration, _request_total_count
    _request_total_duration += duration
    _request_total_count += 1
    logger.info("%s %s -> %d (%.3fs)", method, path, status, duration)
    return response


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------
async def _streaming_proxy(
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    *,
    auto_x_search_injected: bool = False,
) -> Response:
    """Forward to upstream with retry and streaming."""
    upstream: Optional[httpx.Response] = None
    last_exc: Optional[Exception] = None
    auth_refreshed = False
    max_attempts = config.UPSTREAM_RETRY_ATTEMPTS if _is_retry_safe_method(method) else 1
    attempt = 0

    while attempt < max_attempts:
        if upstream is not None:
            await upstream.aclose()
        try:
            upstream = await httpx_client.send(
                httpx.Request(method, url, headers=headers, content=body or None),
                stream=True,
            )
        except Exception as exc:
            last_exc = exc
            attempt += 1
            if attempt >= max_attempts:
                break
            await asyncio.sleep(config.UPSTREAM_RETRY_DELAY)
            continue

        attempt += 1

        if upstream.status_code == 401 and not auth_refreshed:
            await upstream.aclose()
            auth_refreshed = True
            max_attempts += 1
            try:
                fresh_token = await token_manager.get_access_token(force_refresh=True)
            except Exception as exc:
                last_exc = exc
                upstream = None
                break
            headers = dict(headers)
            headers["Authorization"] = f"Bearer {fresh_token}"
            continue

        if upstream.status_code not in (502, 503, 429):
            break
        if attempt < max_attempts:
            await asyncio.sleep(config.UPSTREAM_RETRY_DELAY * attempt)

    if upstream is None:
        logger.error(
            "Upstream request failed: %s",
            last_exc.__class__.__name__ if last_exc else "UnknownError",
        )
        return Response(
            content=json.dumps({"error": "Upstream request failed"}),
            status_code=502,
            media_type="application/json",
        )

    content_type = upstream.headers.get("content-type", "")

    async def iter_body() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    response_connection_tokens: set[str] = set()
    for value in upstream.headers.get_list("connection"):
        response_connection_tokens.update(part.strip().lower() for part in value.split(",") if part.strip())
    excluded = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        *response_connection_tokens,
    }
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in excluded
    }

    content = iter_body()
    if auto_x_search_injected and "text/event-stream" in content_type.lower():
        content = _iter_auto_x_search_compatible_sse(upstream)

    return StreamingResponse(
        content=content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=content_type,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health(response: Response, deep: bool = False) -> dict:
    try:
        state = await token_manager.read_local_state()
        access_token = state.get("access_token", "")
        exp = token_manager.get_token_expiry(access_token)
        exp_str = None
        if exp:
            exp_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp))
        result = {
            "status": "ok",
            "provider": "xai-oauth",
            "api_base": token_manager.XAI_API_BASE,
            "token_expires_at": exp_str,
            "token_endpoint": state.get("token_endpoint"),
        }
        if exp and exp <= time.time():
            response.status_code = 503
            result["status"] = "error"
            result["detail"] = "token expired; refresh or re-authenticate xAI OAuth"
        if deep:
            t0 = time.time()
            try:
                auth_headers = await token_manager.get_auth_headers()
                r = await httpx_client.get(
                    f"{token_manager.XAI_API_BASE}/v1/models",
                    headers=auth_headers,
                    timeout=10.0,
                )
                if r.status_code == 200:
                    result["deep_check"] = {
                        "status": "ok",
                        "latency_ms": round((time.time() - t0) * 1000, 2),
                        "upstream_status": r.status_code,
                    }
                else:
                    response.status_code = 503
                    result["status"] = "error"
                    result["deep_check"] = {
                        "status": "fail",
                        "latency_ms": round((time.time() - t0) * 1000, 2),
                        "upstream_status": r.status_code,
                    }
            except Exception:
                response.status_code = 503
                result["status"] = "error"
                result["deep_check"] = {"status": "fail", "error": "upstream deep health check failed"}
        return result
    except Exception:
        response.status_code = 503
        return {"status": "error", "detail": "token state unavailable"}


@app.get("/metrics")
async def metrics() -> Response:
    lines: list[str] = []
    lines.append("# HELP proxy_requests_total Total requests by method and status")
    lines.append("# TYPE proxy_requests_total counter")
    for key, count in _request_counts.items():
        method, status = key.split(":", 1)
        lines.append(f'proxy_requests_total{{method="{method}",status="{status}"}} {count}')

    lines.append("# HELP proxy_request_duration_seconds_total Total request duration")
    lines.append("# TYPE proxy_request_duration_seconds_total counter")
    lines.append(f"proxy_request_duration_seconds_total {_request_total_duration}")

    lines.append("# HELP proxy_request_count_total Total request count")
    lines.append("# TYPE proxy_request_count_total counter")
    lines.append(f"proxy_request_count_total {_request_total_count}")

    try:
        state = await token_manager.read_local_state()
        exp = token_manager.get_token_expiry(state.get("access_token", ""))
        if exp:
            lines.append("# HELP proxy_token_expires_at Unix timestamp of token expiry")
            lines.append("# TYPE proxy_token_expires_at gauge")
            lines.append(f"proxy_token_expires_at {exp}")
    except Exception:
        pass

    lines.extend(mcp_x_search.metrics_lines())

    return Response(content="\n".join(lines) + "\n", media_type="text/plain")


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    """Handle minimal MCP JSON-RPC over HTTP for shared local clients."""
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        return Response(content=json.dumps(response), status_code=400, media_type="application/json")

    requests = payload if isinstance(payload, list) else [payload]
    if not all(isinstance(item, dict) for item in requests):
        response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        return Response(content=json.dumps(response), status_code=400, media_type="application/json")

    responses: list[dict] = []
    for item in requests:
        response = await mcp_server.handle(item)
        if response:
            responses.append(response)
    if not responses:
        return Response(status_code=202)

    body: object = responses if isinstance(payload, list) else responses[0]
    return Response(content=json.dumps(body, ensure_ascii=False, separators=(",", ":")), media_type="application/json")


@app.get("/mcp")
@app.options("/mcp")
async def mcp_transport_not_available() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def catchall(request: Request, path: str) -> Response:
    try:
        auth_headers = await token_manager.get_auth_headers()
    except Exception as exc:
        logger.error("Token resolution failed: %s", exc.__class__.__name__)
        return Response(
            content=json.dumps({"error": "Token resolution failed"}),
            status_code=503,
            media_type="application/json",
        )

    url = f"{token_manager.XAI_API_BASE}/{path}"
    if request.query_params:
        url = f"{url}?{request.query_params}"

    forwarded_headers = _prepare_forward_headers(request.headers, auth_headers)

    original_body = await request.body()
    body = _maybe_inject_auto_x_search(request.method, path, original_body)
    return await _streaming_proxy(
        request.method,
        url,
        forwarded_headers,
        body,
        auto_x_search_injected=body != original_body,
    )


# ---------------------------------------------------------------------------
# Port scanner & entrypoint
# ---------------------------------------------------------------------------
def find_port(start: int = config.PORT, max_scan: int = 20) -> int:
    for offset in range(max_scan):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((config.HOST, port))
                return port
            except OSError:
                continue
    if max_scan <= 1:
        raise RuntimeError(
            f"Port {start} is already in use. Set PROXY_PORT to a free port or enable "
            "GROK_GATEWAY_PORT_AUTOSCAN=1 for development."
        )
    raise RuntimeError(f"No available port found in range {start}~{start + max_scan - 1}")


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        asyncio.run(_preflight_startup())
    except Exception as exc:
        logger.error("Startup preflight failed: %s", exc.__class__.__name__)
        raise SystemExit(1) from exc

    port = find_port(config.PORT, max_scan=20 if config.GROK_GATEWAY_PORT_AUTOSCAN else 1)
    logger.info("Starting Grok MCP Gateway on http://%s:%d", config.HOST, port)
    uvicorn.run(app, host=config.HOST, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
