"""Native xAI OAuth2/OIDC browser login for Grok MCP Gateway.

This module implements Authorization Code + PKCE using only the Python standard
library plus the project's existing httpx dependency. Credentials are persisted
through token_manager.save_local_state(), so the gateway's existing atomic-write
and file-permission protections remain the single storage implementation.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

import token_manager

XAI_AUTH_ENDPOINT = "https://auth.x.ai/oauth2/authorize"
XAI_TOKEN_ENDPOINT = token_manager.XAI_TOKEN_ENDPOINT
XAI_ACCOUNTS_ORIGIN = "https://accounts.x.ai"

# Public first-party client ID used by xAI's open-source Grok Build client.
# GROK_PROXY_OAUTH_CLIENT_ID can override this without changing code.
DEFAULT_XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"

# Deliberately narrower than Grok Build's full workspace/conversation scope set.
# The gateway needs an offline refresh token and API access, not workspace APIs.
DEFAULT_SCOPES: tuple[str, ...] = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "grok-cli:access",
    "api:access",
)

DEFAULT_LOGIN_TIMEOUT_SECONDS = 600.0
DEFAULT_CALLBACK_SCAN_COUNT = 20
_MAX_OAUTH_ERROR_CODE_LENGTH = 80
_SAFE_ERROR_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class OAuthLoginError(RuntimeError):
    """Raised when the interactive xAI OAuth login cannot be completed safely."""


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str


@dataclass(frozen=True)
class _CallbackResult:
    code: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class _CallbackDecision:
    status: int
    success: bool
    title: str
    message: str
    terminal: bool = False


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce() -> PkcePair:
    """Generate an RFC 7636 S256 verifier/challenge pair.

    64 random bytes produce an 86-character URL-safe verifier, comfortably
    inside RFC 7636's 43..128 character verifier requirement.
    """
    verifier = _b64url_no_pad(secrets.token_bytes(64))
    if not 43 <= len(verifier) <= 128:  # defensive invariant
        raise RuntimeError("Generated PKCE verifier has an invalid length.")
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def _new_random_token() -> str:
    return secrets.token_urlsafe(32)


def _normalize_scopes(scopes: Optional[Sequence[str]] = None) -> tuple[str, ...]:
    if scopes is None:
        raw = (os.getenv("GROK_PROXY_OAUTH_SCOPES") or "").strip()
        if raw:
            scopes = tuple(part for part in re.split(r"[\s,]+", raw) if part)
        else:
            scopes = DEFAULT_SCOPES

    normalized: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        value = str(scope).strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        raise OAuthLoginError("At least one OAuth scope is required.")
    return tuple(normalized)


def resolve_client_id(explicit_client_id: Optional[str] = None) -> str:
    client_id = (
        explicit_client_id
        or os.getenv("GROK_PROXY_OAUTH_CLIENT_ID")
        or DEFAULT_XAI_CLIENT_ID
    ).strip()
    if not client_id:
        raise OAuthLoginError("xAI OAuth client_id is empty.")
    if any(ch.isspace() for ch in client_id):
        raise OAuthLoginError("xAI OAuth client_id must not contain whitespace.")
    return client_id


def _env_callback_port() -> Optional[int]:
    raw = (os.getenv("GROK_PROXY_OAUTH_CALLBACK_PORT") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise OAuthLoginError("GROK_PROXY_OAUTH_CALLBACK_PORT must be an integer.") from exc
    if value == 0:
        return None
    if not 1 <= value <= 65535:
        raise OAuthLoginError("GROK_PROXY_OAUTH_CALLBACK_PORT must be between 1 and 65535.")
    return value


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    pkce: PkcePair,
    state: str,
    nonce: str,
    scopes: Sequence[str],
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "referrer": "grok-mcp-gateway",
    }
    return f"{XAI_AUTH_ENDPOINT}?{urlencode(params)}"


def _safe_oauth_error_code(value: object) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > _MAX_OAUTH_ERROR_CODE_LENGTH
        or not _SAFE_ERROR_RE.fullmatch(text)
    ):
        return "authorization_failed"
    return text


def _single_query_value(params: Mapping[str, list[str]], name: str) -> Optional[str]:
    values = params.get(name)
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"duplicate {name}")
    return values[0]


class _CallbackState:
    """One-shot callback state with strict CSRF checking and replay prevention."""

    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._consumed = False
        self._result: Optional[_CallbackResult] = None

    @property
    def result(self) -> Optional[_CallbackResult]:
        with self._lock:
            return self._result

    def process(self, params: Mapping[str, list[str]]) -> _CallbackDecision:
        try:
            received_state = _single_query_value(params, "state")
            code = _single_query_value(params, "code")
            oauth_error = _single_query_value(params, "error")
        except ValueError:
            return _CallbackDecision(
                status=400,
                success=False,
                title="Invalid callback",
                message="The authorization callback was malformed. Return to the terminal and try again.",
            )

        if not received_state or not hmac.compare_digest(received_state, self.expected_state):
            return _CallbackDecision(
                status=400,
                success=False,
                title="Invalid authorization state",
                message="The login request could not be verified. Return to the terminal and try again.",
            )

        with self._lock:
            if self._consumed:
                return _CallbackDecision(
                    status=409,
                    success=False,
                    title="Authorization already received",
                    message="This login callback has already been used. You can close this window.",
                )

            if oauth_error:
                self._consumed = True
                self._result = _CallbackResult(error=_safe_oauth_error_code(oauth_error))
                return _CallbackDecision(
                    status=200,
                    success=False,
                    title="Authorization was not completed",
                    message="You can close this window and return to the terminal.",
                    terminal=True,
                )

            if not code:
                return _CallbackDecision(
                    status=400,
                    success=False,
                    title="Missing authorization code",
                    message="No authorization code was returned. Return to the terminal and try again.",
                )

            self._consumed = True
            self._result = _CallbackResult(code=code)
            return _CallbackDecision(
                status=200,
                success=True,
                title="Authorization successful",
                message="You can close this window and return to the terminal.",
                terminal=True,
            )


def _callback_page(title: str, message: str, success: bool) -> bytes:
    safe_title = html.escape(title, quote=True)
    safe_message = html.escape(message, quote=True)
    icon = "✓" if success else "×"
    icon_class = "ok" if success else "bad"
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>{safe_title}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0a0a;color:#f5f5f5;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.card{{width:min(520px,calc(100vw - 32px));padding:42px;border:1px solid #262626;border-radius:18px;background:#111;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.35)}}.icon{{display:grid;place-items:center;width:54px;height:54px;margin:0 auto 20px;border-radius:50%;font-size:30px;font-weight:600}}.ok{{background:#10281b;color:#6ee7a0}}.bad{{background:#321515;color:#ff8585}}h1{{margin:0 0 8px;font-size:22px;letter-spacing:-.02em}}p{{margin:0;color:#a3a3a3}}@media(prefers-color-scheme:light){{body{{background:#f7f7f8;color:#171717}}.card{{background:#fff;border-color:#e5e5e5;box-shadow:0 20px 60px rgba(0,0,0,.08)}}p{{color:#666}}}}
</style>
</head>
<body><main class="card"><div class="icon {icon_class}" aria-hidden="true">{icon}</div><h1>{safe_title}</h1><p>{safe_message}</p></main></body>
</html>"""
    return page.encode("utf-8")


def _cors_origin_allowed(origin: Optional[str]) -> bool:
    return not origin or origin == XAI_ACCOUNTS_ORIGIN


def _make_callback_handler(callback_state: _CallbackState):
    class CallbackHandler(BaseHTTPRequestHandler):
        server_version = "GrokOAuthCallback/1.0"
        sys_version = ""

        def log_message(self, _format: str, *args: object) -> None:
            # BaseHTTPRequestHandler logs the raw request line, which would leak
            # authorization codes/state into stderr. Intentionally disabled.
            return

        def _common_headers(self, content_type: Optional[str] = None) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            )
            if content_type:
                self.send_header("Content-Type", content_type)

            origin = self.headers.get("Origin")
            if origin == XAI_ACCOUNTS_ORIGIN:
                self.send_header("Access-Control-Allow-Origin", XAI_ACCOUNTS_ORIGIN)
                self.send_header("Vary", "Origin")
            if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")

        def _send_html(self, decision: _CallbackDecision) -> None:
            body = _callback_page(decision.title, decision.message, decision.success)
            self.send_response(decision.status)
            self._common_headers("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def _reject_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if _cors_origin_allowed(origin):
                return False
            body = _callback_page(
                "Invalid callback origin",
                "The callback origin is not allowed. Return to the terminal and try again.",
                False,
            )
            self.send_response(403)
            self._common_headers("text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlsplit(self.path)
            if parsed.path != "/callback" or self._reject_origin():
                if parsed.path != "/callback":
                    self.send_error(404)
                return
            self.send_response(204)
            self._common_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlsplit(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self._common_headers("text/plain; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self._reject_origin():
                return

            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
            decision = callback_state.process(params)
            self._send_html(decision)
            if decision.terminal:
                callback_state.event.set()

    return CallbackHandler


class _PrivateThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class CallbackReceiver:
    """Temporary loopback HTTP receiver for the OAuth redirect."""

    def __init__(
        self,
        expected_state: str,
        *,
        preferred_port: Optional[int] = None,
        max_scan: int = DEFAULT_CALLBACK_SCAN_COUNT,
    ) -> None:
        self._state = _CallbackState(expected_state)
        self._server = self._bind(preferred_port, max_scan)
        self.port = int(self._server.server_address[1])
        self.redirect_uri = f"http://127.0.0.1:{self.port}/callback"
        self._thread: Optional[threading.Thread] = None

    def _bind(self, preferred_port: Optional[int], max_scan: int) -> _PrivateThreadingHTTPServer:
        handler = _make_callback_handler(self._state)
        if preferred_port is None:
            try:
                return _PrivateThreadingHTTPServer(("127.0.0.1", 0), handler)
            except OSError as exc:
                raise OAuthLoginError("Could not bind the local OAuth callback server.") from exc

        if not 1 <= preferred_port <= 65535:
            raise OAuthLoginError("Callback port must be between 1 and 65535.")
        if max_scan < 1:
            raise OAuthLoginError("Callback port scan count must be at least 1.")

        final_port = min(65535, preferred_port + max_scan - 1)
        last_exc: Optional[OSError] = None
        for port in range(preferred_port, final_port + 1):
            try:
                return _PrivateThreadingHTTPServer(("127.0.0.1", port), handler)
            except OSError as exc:
                last_exc = exc
        raise OAuthLoginError(
            f"No free OAuth callback port found in {preferred_port}..{final_port}."
        ) from last_exc

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Callback receiver is already started.")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="xai-oauth-callback",
            daemon=True,
        )
        self._thread.start()

    def wait(self, timeout_seconds: float) -> str:
        if timeout_seconds <= 0:
            raise OAuthLoginError("OAuth callback timeout must be positive.")
        if not self._state.event.wait(timeout_seconds):
            raise OAuthLoginError("Timed out waiting for the xAI OAuth callback.")
        result = self._state.result
        if result is None:
            raise OAuthLoginError("OAuth callback completed without a result.")
        if result.error:
            raise OAuthLoginError(f"xAI authorization failed: {result.error}")
        if not result.code:
            raise OAuthLoginError("xAI callback did not contain an authorization code.")
        return result.code

    def close(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def __enter__(self) -> "CallbackReceiver":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _decode_jwt_payload_unverified(token: str) -> dict[str, object]:
    """Decode JWT payload only for consistency metadata; never authenticates it."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _client_id_claim_from_tokens(payload: Mapping[str, object]) -> Optional[str]:
    # xAI access tokens may carry client_id. An id_token's aud is also expected
    # to identify the OAuth client in OIDC. These are only consistency checks.
    access_claims = _decode_jwt_payload_unverified(str(payload.get("access_token") or ""))
    access_client_id = access_claims.get("client_id")
    if isinstance(access_client_id, str) and access_client_id.strip():
        return access_client_id.strip()

    id_claims = _decode_jwt_payload_unverified(str(payload.get("id_token") or ""))
    id_aud = id_claims.get("aud")
    if isinstance(id_aud, str) and id_aud.strip():
        return id_aud.strip()
    if isinstance(id_aud, list) and len(id_aud) == 1 and isinstance(id_aud[0], str):
        return id_aud[0].strip() or None
    return None


def _parse_expires_in(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise OAuthLoginError("Token response contains an invalid expires_in value.")
    try:
        expires_in = int(value)
    except (TypeError, ValueError) as exc:
        raise OAuthLoginError("Token response contains an invalid expires_in value.") from exc
    if expires_in <= 0:
        raise OAuthLoginError("Token response contains a non-positive expires_in value.")
    return expires_in


def build_token_state(payload: Mapping[str, object], *, client_id: str) -> dict[str, object]:
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not access_token:
        raise OAuthLoginError("Token response is missing access_token.")
    if not refresh_token:
        raise OAuthLoginError(
            "Token response is missing refresh_token; offline_access is required for a resident gateway."
        )

    returned_client_id = _client_id_claim_from_tokens(payload)
    if returned_client_id and not hmac.compare_digest(returned_client_id, client_id):
        raise OAuthLoginError("Token client_id does not match the OAuth client used for login.")

    token_type = str(payload.get("token_type") or "Bearer").strip() or "Bearer"
    if token_type.lower() != "bearer":
        raise OAuthLoginError(f"Unsupported OAuth token_type: {token_type}")

    expires_in = _parse_expires_in(payload.get("expires_in"))
    now_epoch = int(time.time())
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    state: dict[str, object] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "token_endpoint": XAI_TOKEN_ENDPOINT,
        "authorized_at": now_iso,
        "last_refresh_at": now_iso,
        "last_refresh_status": "login_success",
        "last_refresh_error_class": None,
        "refresh_token_rotated": False,
        "refresh_success_count": 0,
        "refresh_failure_count": 0,
        "credential_source": "native_xai_oauth",
        "reauth_required": False,
    }
    if expires_in is not None:
        state["expires_at"] = now_epoch + expires_in
    return state


async def exchange_authorization_code(
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, object]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }

    async def _post(active_client: httpx.AsyncClient) -> httpx.Response:
        try:
            return await active_client.post(
                XAI_TOKEN_ENDPOINT,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=data,
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise OAuthLoginError(
                f"Token exchange request failed: {exc.__class__.__name__}"
            ) from exc

    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as owned_client:
            response = await _post(owned_client)
    else:
        response = await _post(client)

    if response.status_code != 200:
        oauth_error = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                oauth_error = _safe_oauth_error_code(body.get("error"))
        except (ValueError, TypeError):
            pass
        suffix = f", {oauth_error}" if oauth_error else ""
        raise OAuthLoginError(f"Token exchange failed (HTTP {response.status_code}{suffix}).")

    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthLoginError("Token exchange returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise OAuthLoginError("Token exchange returned an invalid JSON object.")
    return payload


async def persist_token_response(
    payload: Mapping[str, object],
    *,
    client_id: str,
) -> dict[str, object]:
    state = build_token_state(payload, client_id=client_id)
    await token_manager.save_local_state(state)
    return state


async def login_xai_oauth(
    *,
    client_id: Optional[str] = None,
    scopes: Optional[Sequence[str]] = None,
    callback_port: Optional[int] = None,
    timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
    browser_open: Optional[Callable[[str], object]] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> dict[str, object]:
    """Run native browser OAuth login and persist gateway-private credentials."""
    resolved_client_id = resolve_client_id(client_id)
    resolved_scopes = _normalize_scopes(scopes)
    if callback_port is None:
        callback_port = _env_callback_port()

    pkce = generate_pkce()
    state = _new_random_token()
    nonce = _new_random_token()
    receiver = CallbackReceiver(state, preferred_port=callback_port)
    receiver.start()

    auth_url = build_authorization_url(
        client_id=resolved_client_id,
        redirect_uri=receiver.redirect_uri,
        pkce=pkce,
        state=state,
        nonce=nonce,
        scopes=resolved_scopes,
    )

    print("Opening xAI authorization in your browser...")
    opener = browser_open or webbrowser.open
    try:
        opened = bool(await asyncio.to_thread(opener, auth_url))
    except Exception:
        opened = False
    if not opened:
        print("Browser launch failed. Open this URL manually:")
        print(auth_url)

    try:
        code = await asyncio.to_thread(receiver.wait, timeout_seconds)
    finally:
        await asyncio.to_thread(receiver.close)

    payload = await exchange_authorization_code(
        code=code,
        redirect_uri=receiver.redirect_uri,
        client_id=resolved_client_id,
        code_verifier=pkce.code_verifier,
        client=http_client,
    )
    saved = await persist_token_response(payload, client_id=resolved_client_id)
    print(f"xAI OAuth login successful. Credentials saved to {token_manager.LOCAL_AUTH_PATH}")
    return saved


__all__ = [
    "CallbackReceiver",
    "DEFAULT_SCOPES",
    "DEFAULT_XAI_CLIENT_ID",
    "OAuthLoginError",
    "PkcePair",
    "build_authorization_url",
    "build_token_state",
    "exchange_authorization_code",
    "generate_pkce",
    "login_xai_oauth",
    "persist_token_response",
    "resolve_client_id",
]
