from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import oauth_flow
import token_manager


def _unsigned_jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def enc(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{enc(header)}.{enc(payload)}."


def test_generate_pkce_is_rfc7636_s256():
    pair = oauth_flow.generate_pkce()

    assert 43 <= len(pair.code_verifier) <= 128
    assert "=" not in pair.code_verifier
    assert "=" not in pair.code_challenge

    expected = base64.urlsafe_b64encode(
        hashlib.sha256(pair.code_verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert pair.code_challenge == expected


def test_authorization_url_contains_pkce_state_nonce_and_scopes():
    pair = oauth_flow.PkcePair("v" * 64, "challenge")
    url = oauth_flow.build_authorization_url(
        client_id="client-123",
        redirect_uri="http://127.0.0.1:54321/callback",
        pkce=pair,
        state="state-123",
        nonce="nonce-123",
        scopes=("openid", "offline_access", "api:access"),
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == oauth_flow.XAI_AUTH_ENDPOINT
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == ["http://127.0.0.1:54321/callback"]
    assert query["scope"] == ["openid offline_access api:access"]
    assert query["code_challenge"] == ["challenge"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state-123"]
    assert query["nonce"] == ["nonce-123"]


def test_callback_rejects_bad_state_without_consuming_then_accepts_valid_code():
    receiver = oauth_flow.CallbackReceiver("expected-state")
    receiver.start()
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            bad = client.get(
                f"http://127.0.0.1:{receiver.port}/callback",
                params={"code": "attacker-code", "state": "wrong-state"},
            )
            assert bad.status_code == 400

            good = client.get(
                f"http://127.0.0.1:{receiver.port}/callback",
                params={"code": "real-code", "state": "expected-state"},
            )
            assert good.status_code == 200
            assert "Authorization successful" in good.text

        assert receiver.wait(1.0) == "real-code"
    finally:
        receiver.close()


def test_callback_state_is_one_shot_and_replay_is_rejected():
    receiver = oauth_flow.CallbackReceiver("expected-state")
    receiver.start()
    try:
        callback = f"http://127.0.0.1:{receiver.port}/callback"
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            first = client.get(callback, params={"code": "first-code", "state": "expected-state"})
            replay = client.get(callback, params={"code": "second-code", "state": "expected-state"})

        assert first.status_code == 200
        assert replay.status_code == 409
        assert receiver.wait(1.0) == "first-code"
    finally:
        receiver.close()


def test_callback_accepts_only_accounts_xai_cross_origin_and_supports_pna_preflight():
    receiver = oauth_flow.CallbackReceiver("expected-state")
    receiver.start()
    try:
        callback = f"http://127.0.0.1:{receiver.port}/callback"
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            preflight = client.options(
                callback,
                headers={
                    "Origin": oauth_flow.XAI_ACCOUNTS_ORIGIN,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Private-Network": "true",
                },
            )
            forbidden = client.get(
                callback,
                params={"code": "x", "state": "expected-state"},
                headers={"Origin": "https://evil.example"},
            )

        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == oauth_flow.XAI_ACCOUNTS_ORIGIN
        assert preflight.headers["access-control-allow-private-network"] == "true"
        assert forbidden.status_code == 403
    finally:
        receiver.close()


def test_exchange_authorization_code_posts_exact_pkce_form():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type")
        captured["form"] = parse_qs(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            request=request,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "token_type": "Bearer",
                "expires_in": 7200,
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await oauth_flow.exchange_authorization_code(
                code="auth-code",
                redirect_uri="http://127.0.0.1:54321/callback",
                client_id="client-123",
                code_verifier="verifier",
                client=client,
            )

    payload = asyncio.run(run())

    assert captured["url"] == oauth_flow.XAI_TOKEN_ENDPOINT
    assert captured["content_type"].startswith("application/x-www-form-urlencoded")
    assert captured["form"] == {
        "grant_type": ["authorization_code"],
        "code": ["auth-code"],
        "redirect_uri": ["http://127.0.0.1:54321/callback"],
        "client_id": ["client-123"],
        "code_verifier": ["verifier"],
    }
    assert payload["refresh_token"] == "refresh"


def test_token_exchange_error_does_not_echo_upstream_body():
    secret = "refresh_token=super-secret Authorization: Bearer upstream-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, request=request, text=secret)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await oauth_flow.exchange_authorization_code(
                code="auth-code",
                redirect_uri="http://127.0.0.1:54321/callback",
                client_id="client-123",
                code_verifier="verifier",
                client=client,
            )

    with pytest.raises(oauth_flow.OAuthLoginError) as exc_info:
        asyncio.run(run())

    assert "HTTP 400" in str(exc_info.value)
    assert "super-secret" not in str(exc_info.value)
    assert "upstream-secret" not in str(exc_info.value)


def test_build_token_state_rejects_mismatched_client_id_claim():
    with pytest.raises(oauth_flow.OAuthLoginError, match="client_id"):
        oauth_flow.build_token_state(
            {
                "access_token": _unsigned_jwt({"client_id": "different-client"}),
                "refresh_token": "refresh",
                "expires_in": 7200,
            },
            client_id="expected-client",
        )


def test_build_token_state_requires_refresh_token():
    with pytest.raises(oauth_flow.OAuthLoginError, match="refresh_token"):
        oauth_flow.build_token_state(
            {"access_token": "access", "expires_in": 7200},
            client_id="client-123",
        )


def test_persist_token_response_uses_token_manager_private_atomic_storage(tmp_path, monkeypatch):
    state_path = tmp_path / "grok-oauth-proxy" / "auth_state.json"
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)

    state = asyncio.run(
        oauth_flow.persist_token_response(
            {
                "access_token": _unsigned_jwt({"client_id": "client-123"}),
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "expires_in": 7200,
            },
            client_id="client-123",
        )
    )

    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["access_token"] == state["access_token"]
    assert on_disk["refresh_token"] == "refresh-token"
    assert on_disk["client_id"] == "client-123"
    assert on_disk["credential_source"] == "native_xai_oauth"
    assert on_disk["reauth_required"] is False

    if os.name == "posix":
        assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_read_local_state_never_implicitly_bootstraps_from_hermes(tmp_path, monkeypatch):
    local_state = tmp_path / "state" / "auth_state.json"
    legacy_state = tmp_path / "legacy-auth-state.json"

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("runtime auth must not read Hermes")

    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", local_state)
    monkeypatch.setattr(token_manager, "LEGACY_LOCAL_AUTH_PATH", legacy_state)
    monkeypatch.setattr(token_manager, "load_from_hermes", should_not_be_called)

    with pytest.raises(token_manager.AuthRequiredError, match="--login"):
        asyncio.run(token_manager.read_local_state())
