import asyncio
import base64
import importlib
import json
import os
import socket
import stat
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from error_sanitizer import sanitize_text
import main
import token_manager


def _unsigned_jwt(payload):
    header = {"alg": "none", "typ": "JWT"}

    def encode(part):
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


def test_prepare_forward_headers_strips_hop_by_hop_and_incoming_credentials():
    incoming = {
        "Host": "127.0.0.1:9996",
        "Connection": "keep-alive, X-Debug-Hop",
        "X-Debug-Hop": "remove-me",
        "Authorization": "Bearer user-supplied-dummy-key",
        "Proxy-Authorization": "Basic should-not-forward",
        "Content-Length": "123",
        "TE": "trailers",
        "X-Proxy-Api-Key": "secret-proxy-key",
        "Cookie": "session=do-not-forward",
        "Forwarded": "for=192.0.2.1",
        "X-Forwarded-For": "192.0.2.1",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Proto": "http",
        "X-Forwarded-Port": "443",
        "X-Forwarded-Prefix": "/evil",
        "X-Real-IP": "192.0.2.2",
        "X-Request-ID": "keep-me",
    }

    headers = main._prepare_forward_headers(incoming, {"Authorization": "Bearer upstream-oauth"})

    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered["authorization"] == "Bearer upstream-oauth"
    assert lowered["x-request-id"] == "keep-me"
    assert "host" not in lowered
    assert "connection" not in lowered
    assert "x-debug-hop" not in lowered
    assert "proxy-authorization" not in lowered
    assert "content-length" not in lowered
    assert "te" not in lowered
    assert "x-proxy-api-key" not in lowered
    assert "cookie" not in lowered
    assert "forwarded" not in lowered
    assert "x-forwarded-for" not in lowered
    assert "x-forwarded-host" not in lowered
    assert "x-forwarded-proto" not in lowered
    assert "x-forwarded-port" not in lowered
    assert "x-forwarded-prefix" not in lowered
    assert "x-real-ip" not in lowered


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_allowed_without_proxy_api_key(host):
    main._validate_startup_security(host, proxy_api_key=None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10"])
def test_non_loopback_bind_requires_proxy_api_key(host):
    with pytest.raises(RuntimeError, match="PROXY_API_KEY"):
        main._validate_startup_security(host, proxy_api_key=None)


def test_non_loopback_bind_rejects_short_proxy_api_key():
    with pytest.raises(RuntimeError, match="at least 16"):
        main._validate_startup_security("0.0.0.0", proxy_api_key="short")

    main._validate_startup_security("0.0.0.0", proxy_api_key="long-enough-secret")


def test_proxy_api_key_accepts_bearer_or_x_proxy_header():
    assert main._request_has_valid_proxy_api_key({"authorization": "Bearer secret"}, "secret")
    assert main._request_has_valid_proxy_api_key({"x-proxy-api-key": "secret"}, "secret")
    assert not main._request_has_valid_proxy_api_key({"authorization": "Bearer wrong"}, "secret")
    assert not main._request_has_valid_proxy_api_key({}, "secret")


def test_sanitize_text_redacts_common_secret_shapes():
    secret = (
        '"refresh_token":"abc.def.ghi" '
        "'access_token': 'abc.def.ghi' "
        'api_key="sk-abcdef123456" '
        "token=plain-secret "
        "Authorization: Bearer bearer-secret-value "
        "jwt=eyJhbGciOiJub25l.eyJzdWIiOiIxIn0.signature "
        "email=user@example.com "
        "cookie=session-secret"
    )

    sanitized = sanitize_text(secret)

    assert "abc.def.ghi" not in sanitized
    assert "sk-abcdef123456" not in sanitized
    assert "plain-secret" not in sanitized
    assert "bearer-secret-value" not in sanitized
    assert "eyJhbGci" not in sanitized
    assert "user@example.com" not in sanitized
    assert "session-secret" not in sanitized


def test_find_port_fails_fast_when_autoscan_disabled():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((main.config.HOST, 0))
        occupied_port = sock.getsockname()[1]

        with pytest.raises(RuntimeError, match="already in use"):
            main.find_port(occupied_port, max_scan=1)

        assert main.find_port(occupied_port, max_scan=10) != occupied_port


def test_secondary_api_key_auth_is_not_part_of_runtime_config():
    assert not any(name for name in dir(token_manager) if name.startswith("get_") and name.endswith("_fallback"))


def test_preflight_stops_when_oauth_state_unavailable(monkeypatch):
    async def fake_read_local_state():
        raise RuntimeError("missing oauth state")

    monkeypatch.setattr(main.token_manager, "read_local_state", fake_read_local_state)

    with pytest.raises(RuntimeError, match="missing oauth state"):
        asyncio.run(main._preflight_startup())


def test_health_reports_expired_oauth_without_secondary_auth(monkeypatch):
    async def fake_read_local_state():
        return {
            "access_token": _unsigned_jwt({"exp": 1}),
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        }

    monkeypatch.setattr(main.token_manager, "read_local_state", fake_read_local_state)

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["provider"] == "xai-oauth"
    assert "token expired" in payload["detail"]


def test_upstream_retry_attempts_are_at_least_one(monkeypatch):
    monkeypatch.setenv("UPSTREAM_RETRY_ATTEMPTS", "0")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.UPSTREAM_RETRY_ATTEMPTS == 1
    finally:
        monkeypatch.delenv("UPSTREAM_RETRY_ATTEMPTS", raising=False)
        importlib.reload(config)


def test_auto_x_search_injection_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main.config, "GROK_PROXY_AUTO_X_SEARCH", False)

    body = b'{"model":"grok-4.3","input":"hello"}'

    assert main._maybe_inject_auto_x_search("POST", "v1/responses", body) == body


def test_auto_x_search_injection_adds_responses_tool(monkeypatch):
    monkeypatch.setattr(main.config, "GROK_PROXY_AUTO_X_SEARCH", True)
    monkeypatch.setattr(main.config, "GROK_PROXY_X_SEARCH_ALLOWED_HANDLES", ["elonmusk"])
    monkeypatch.setattr(main.config, "GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING", True)
    monkeypatch.setattr(main.config, "GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING", False)

    body = b'{"model":"grok-4.3","input":"latest posts","tools":[{"type":"web_search"}]}'

    updated = json_loads(main._maybe_inject_auto_x_search("POST", "v1/responses", body))

    assert updated["tools"] == [
        {"type": "web_search"},
        {
            "type": "x_search",
            "allowed_x_handles": ["elonmusk"],
            "enable_image_understanding": True,
        },
    ]


def test_auto_x_search_injection_does_not_duplicate_existing_tool(monkeypatch):
    monkeypatch.setattr(main.config, "GROK_PROXY_AUTO_X_SEARCH", True)

    body = b'{"model":"grok-4.3","input":"latest posts","tools":[{"type":"x_search"}]}'

    assert main._maybe_inject_auto_x_search("POST", "v1/responses", body) == body


def test_auto_x_search_stream_filter_drops_internal_custom_tool_events():
    block = (
        "event: response.output_item.added\n"
        'data: {"type":"response.output_item.added","item":{"type":"custom_tool_call","name":"x_keyword_search"}}'
    )

    assert main._sanitize_auto_x_search_sse_event(block) is None


def test_auto_x_search_stream_filter_strips_completed_tool_items():
    block = (
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"tools":[{"type":"x_search"}],'
        '"output":[{"type":"custom_tool_call","name":"x_keyword_search"},{"type":"message","content":[]}]}}'
    )

    sanitized = main._sanitize_auto_x_search_sse_event(block)
    payload = json_loads(sanitized.split("data: ", 1)[1].encode("utf-8"))

    assert payload["response"]["tools"] == []
    assert payload["response"]["output"] == [{"type": "message", "content": []}]


def test_token_endpoint_is_strictly_allowlisted():
    assert token_manager._validate_token_endpoint("https://auth.x.ai/oauth2/token") == "https://auth.x.ai/oauth2/token"
    for endpoint in [
        "http://auth.x.ai/oauth2/token",
        "https://evil.example/oauth2/token",
        "https://auth.x.ai.evil.example/oauth2/token",
        "https://auth.x.ai/other",
        "https://auth.x.ai/oauth2/token?redirect=https://evil.example",
        "https://auth.x.ai/oauth2/token#fragment",
    ]:
        with pytest.raises(RuntimeError, match="untrusted"):
            token_manager._validate_token_endpoint(endpoint)


def test_oauth_client_id_is_imported_from_hermes_token_claims(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({
            "providers": {
                "xai-oauth": {
                    "tokens": {
                        "access_token": _unsigned_jwt({"client_id": "client-from-access", "aud": "aud-from-access"}),
                        "refresh_token": "refresh",
                        "id_token": _unsigned_jwt({"aud": "client-from-id"}),
                        "token_type": "Bearer",
                    },
                    "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
                }
            }
        }),
        encoding="utf-8",
    )

    state = asyncio.run(token_manager.load_from_hermes(auth_path))

    assert state["client_id"] == "client-from-access"
    assert state["refresh_token"] == "refresh"


def test_load_from_hermes_prefers_newest_usable_pool_credential(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "xai-oauth": [
                        {
                            "access_token": _unsigned_jwt({"client_id": "old-client", "exp": 1000}),
                            "refresh_token": "old-refresh",
                            "last_status": "exhausted",
                            "last_refresh": "2026-05-18T04:19:30Z",
                            "priority": 0,
                        },
                        {
                            "access_token": _unsigned_jwt({"client_id": "stale-client", "exp": 2000}),
                            "refresh_token": "stale-refresh",
                            "last_refresh": "2026-06-01T07:15:56Z",
                            "priority": 1,
                        },
                        {
                            "access_token": _unsigned_jwt({"client_id": "new-client", "exp": 3000}),
                            "refresh_token": "new-refresh",
                            "last_refresh": "2026-06-01T07:53:56Z",
                            "priority": 2,
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    state = asyncio.run(token_manager.load_from_hermes(auth_path))

    assert state["client_id"] == "new-client"
    assert state["refresh_token"] == "new-refresh"


def test_missing_hermes_auth_file_stops_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(token_manager.shutil, "which", lambda command: "/usr/local/bin/hermes" if command == "hermes" else None)
    monkeypatch.setattr(token_manager, "HERMES_AUTH_PATH", tmp_path / "missing-auth.json")

    with pytest.raises(RuntimeError, match="Hermes auth.json not found"):
        asyncio.run(token_manager.init_local_state())


def test_missing_hermes_cli_and_missing_auth_stops_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(token_manager.shutil, "which", lambda command: None)
    monkeypatch.setattr(token_manager, "HERMES_AUTH_PATH", tmp_path / "missing-auth.json")

    with pytest.raises(RuntimeError, match="Hermes auth.json not found"):
        asyncio.run(token_manager.init_local_state())


def test_imported_hermes_auth_bootstraps_without_hermes_cli(tmp_path, monkeypatch):
    auth_path = tmp_path / ".hermes" / "auth.json"
    auth_path.parent.mkdir()
    auth_path.write_text(
        json.dumps({
            "providers": {
                "xai-oauth": {
                    "tokens": {
                        "access_token": _unsigned_jwt({"client_id": "client-from-access"}),
                        "refresh_token": "refresh",
                    },
                    "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
                }
            }
        }),
        encoding="utf-8",
    )
    local_state = tmp_path / "state" / "auth_state.json"

    monkeypatch.setattr(token_manager.shutil, "which", lambda command: None)
    monkeypatch.setattr(token_manager, "HERMES_AUTH_PATH", auth_path)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", local_state)

    state = asyncio.run(token_manager.init_local_state())

    assert state["client_id"] == "client-from-access"
    assert state["refresh_token"] == "refresh"
    assert local_state.exists()


def test_token_state_save_uses_private_permissions(tmp_path):
    state_path = tmp_path / "state" / "auth_state.json"

    token_manager._save_json_sync(state_path, {"access_token": "a", "refresh_token": "r"})

    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert [p.name for p in state_path.parent.iterdir()] == ["auth_state.json"]
    assert json.loads(state_path.read_text())["refresh_token"] == "r"


def test_existing_app_token_state_directory_permissions_are_repaired(tmp_path):
    state_dir = tmp_path / "grok-oauth-proxy"
    state_dir.mkdir(mode=0o755)
    state_path = state_dir / "auth_state.json"

    token_manager._save_json_sync(state_path, {"access_token": "a"})

    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_group_writable_custom_token_state_directory_is_rejected(tmp_path):
    state_dir = tmp_path / "custom-state"
    state_dir.mkdir(mode=0o777)
    os.chmod(state_dir, 0o777)

    with pytest.raises(RuntimeError, match="group/world-writable"):
        token_manager._save_json_sync(state_dir / "auth_state.json", {"access_token": "a"})


def test_existing_token_state_permissions_are_repaired_before_read(tmp_path):
    state_path = tmp_path / "state" / "auth_state.json"
    state_path.parent.mkdir()
    state_path.write_text('{"access_token":"a"}', encoding="utf-8")
    os.chmod(state_path, 0o644)

    data = token_manager._load_json_sync(state_path)

    assert data == {"access_token": "a"}
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_symlinked_token_state_is_rejected_before_read(tmp_path):
    target = tmp_path / "target.json"
    symlink = tmp_path / "auth_state.json"
    target.write_text('{"access_token":"a"}', encoding="utf-8")
    symlink.symlink_to(target)

    with pytest.raises(RuntimeError, match="symlinked"):
        token_manager._load_json_sync(symlink)


def test_legacy_source_tree_token_state_is_removed_after_migration(tmp_path, monkeypatch):
    local_state = tmp_path / "new" / "auth_state.json"
    legacy_state = tmp_path / "legacy_auth_state.json"
    legacy_state.write_text(
        json.dumps({
            "access_token": "a",
            "refresh_token": "r",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
            "client_id": "client-from-legacy",
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", local_state)
    monkeypatch.setattr(token_manager, "LEGACY_LOCAL_AUTH_PATH", legacy_state)

    data = asyncio.run(token_manager.read_local_state())

    assert data["access_token"] == "a"
    assert local_state.exists()
    assert not legacy_state.exists()


def test_health_and_metrics_require_proxy_auth_when_configured(monkeypatch):
    async def fake_read_local_state():
        return {"access_token": "not-a-jwt", "token_endpoint": "https://auth.x.ai/oauth2/token"}

    monkeypatch.setattr(main.config, "PROXY_API_KEY", "secret")
    monkeypatch.setattr(main.token_manager, "read_local_state", fake_read_local_state)

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/metrics").status_code == 401
        assert client.get("/health", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert client.get("/metrics", headers={"X-Proxy-Api-Key": "secret"}).status_code == 200


def test_health_reports_expired_token_state(monkeypatch):
    expired = _unsigned_jwt({"exp": 1})

    async def fake_read_local_state():
        return {"access_token": expired, "token_endpoint": "https://auth.x.ai/oauth2/token"}

    monkeypatch.setattr(main.config, "PROXY_API_KEY", None)
    monkeypatch.setattr(main.token_manager, "read_local_state", fake_read_local_state)

    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert "token expired" in response.json()["detail"]


def test_http_mcp_lists_x_retrieve_tool(monkeypatch):
    async def fake_preflight_startup():
        return None

    monkeypatch.setattr(main, "_preflight_startup", fake_preflight_startup)

    with TestClient(main.app) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 200
    assert response.json()["result"]["tools"][0]["name"] == "x_retrieve"


def test_http_mcp_notifications_return_accepted(monkeypatch):
    async def fake_preflight_startup():
        return None

    monkeypatch.setattr(main, "_preflight_startup", fake_preflight_startup)

    with TestClient(main.app) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )

    assert response.status_code == 202


def test_catchall_token_resolution_failure_is_sanitized(monkeypatch):
    async def fake_read_local_state():
        return {"access_token": "not-a-jwt", "token_endpoint": "https://auth.x.ai/oauth2/token"}

    async def fake_get_auth_headers():
        raise RuntimeError("refresh_token=super-secret upstream detail")

    monkeypatch.setattr(main.config, "PROXY_API_KEY", None)
    monkeypatch.setattr(main.token_manager, "read_local_state", fake_read_local_state)
    monkeypatch.setattr(main.token_manager, "get_auth_headers", fake_get_auth_headers)

    with TestClient(main.app) as client:
        response = client.get("/v1/models")

    assert response.status_code == 503
    assert response.json() == {"error": "Token resolution failed"}
    assert "super-secret" not in response.text


def test_refresh_uses_client_id_from_imported_state(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers, data, timeout):
        captured.update({"url": url, "headers": headers, "data": data, "timeout": timeout})
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"access_token": "new-access", "refresh_token": "new-refresh", "token_type": "Bearer", "expires_in": 3600},
        )

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", tmp_path / "auth_state.json")

    updated = asyncio.run(token_manager.refresh_access_token({
        "refresh_token": "old-refresh",
        "client_id": "client-from-hermes",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
    }))

    assert captured["data"]["client_id"] == "client-from-hermes"
    assert captured["data"]["refresh_token"] == "old-refresh"
    assert updated["client_id"] == "client-from-hermes"


def test_refresh_without_client_id_stops(monkeypatch):
    with pytest.raises(RuntimeError, match="client_id"):
        asyncio.run(token_manager.refresh_access_token({
            "refresh_token": "old-refresh",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        }))


def test_auth_headers_raise_when_oauth_unavailable(monkeypatch):
    async def fake_get_access_token():
        raise RuntimeError("OAuth refresh failed")

    monkeypatch.setattr(token_manager, "get_access_token", fake_get_access_token)

    with pytest.raises(RuntimeError, match="OAuth refresh failed"):
        asyncio.run(token_manager.get_auth_headers())


def test_refresh_failure_does_not_return_upstream_secret(monkeypatch, tmp_path):
    def fake_post(url, headers, data, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(
            400,
            request=request,
            text="refresh_token=super-secret Authorization: Bearer upstream-secret user@example.com",
        )

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", tmp_path / "auth_state.json")

    async def fake_load_from_hermes(auth_path=None):
        return None

    monkeypatch.setattr(token_manager, "load_from_hermes", fake_load_from_hermes)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(token_manager.refresh_access_token({
            "refresh_token": "old-refresh",
            "client_id": "client-from-hermes",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        }))

    message = str(exc_info.value)
    assert "400" in message
    assert "super-secret" not in message
    assert "upstream-secret" not in message
    assert "user@example.com" not in message


def test_refresh_failure_rehydrates_new_hermes_credential(monkeypatch, tmp_path):
    future_token = _unsigned_jwt({"exp": 4_102_444_800, "client_id": "client-from-hermes"})

    def fake_post(url, headers, data, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(400, request=request, text="refresh_token=old-secret")

    async def fake_load_from_hermes(auth_path=None):
        return {
            "access_token": future_token,
            "refresh_token": "new-refresh",
            "client_id": "client-from-hermes",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        }

    monkeypatch.setattr(token_manager.httpx, "post", fake_post)
    monkeypatch.setattr(token_manager, "load_from_hermes", fake_load_from_hermes)
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", tmp_path / "auth_state.json")

    updated = asyncio.run(token_manager.refresh_access_token({
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "client-from-hermes",
        "token_endpoint": "https://auth.x.ai/oauth2/token",
    }))

    assert updated["access_token"] == future_token
    assert updated["refresh_token"] == "new-refresh"
    assert updated["last_refresh_status"] == "rehydrated_from_hermes"
    assert updated["refresh_failure_count"] == 1
    assert updated["reauth_required"] is False


def test_rehydrate_does_not_overwrite_newer_local_token(monkeypatch, tmp_path):
    newer_token = _unsigned_jwt({"exp": 4_102_444_800, "client_id": "client-from-hermes"})
    older_token = _unsigned_jwt({"exp": 4_102_441_200, "client_id": "client-from-hermes"})

    async def fake_load_from_hermes(auth_path=None):
        return {
            "access_token": older_token,
            "refresh_token": "older-refresh",
            "client_id": "client-from-hermes",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        }

    state_path = tmp_path / "auth_state.json"
    monkeypatch.setattr(token_manager, "LOCAL_AUTH_PATH", state_path)
    monkeypatch.setattr(token_manager, "load_from_hermes", fake_load_from_hermes)

    result = asyncio.run(token_manager.rehydrate_from_hermes({
        "access_token": newer_token,
        "refresh_token": "newer-refresh",
        "client_id": "client-from-hermes",
    }))

    assert result is None
    assert not state_path.exists()


def test_refresh_diagnostics_are_safe():
    diagnostics = token_manager.get_refresh_diagnostics(
        {
            "last_refresh_status": "failure",
            "last_refresh_error_class": "RuntimeError",
            "refresh_failure_count": 2,
            "refresh_success_count": 1,
            "refresh_token_rotated": True,
            "credential_source": "hermes_rehydrated",
            "reauth_required": True,
        }
    )

    assert diagnostics["last_refresh_status"] == "failure"
    assert diagnostics["refresh_failure_count"] == 2
    assert diagnostics["refresh_success_count"] == 1
    assert diagnostics["refresh_token_rotated"] is True
    assert diagnostics["credential_source"] == "hermes_rehydrated"
    assert diagnostics["reauth_required"] is True


def test_streaming_proxy_refreshes_and_retries_once_on_401(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.requests = []

        async def send(self, request, stream):
            self.requests.append(request)
            if len(self.requests) == 1:
                return httpx.Response(401, request=request, content=b"expired")
            return httpx.Response(200, request=request, content=b"ok")

    async def fake_get_access_token(*, force_refresh=False):
        assert force_refresh is True
        return "fresh-token"

    fake_client = FakeClient()
    monkeypatch.setattr(main, "httpx_client", fake_client, raising=False)
    monkeypatch.setattr(main.token_manager, "get_access_token", fake_get_access_token)
    monkeypatch.setattr(main.config, "UPSTREAM_RETRY_ATTEMPTS", 1)

    response = asyncio.run(
        main._streaming_proxy(
            "POST",
            "https://api.x.ai/v1/chat/completions",
            {"Authorization": "Bearer stale-token"},
            b"{}",
        )
    )
    body = b"".join(asyncio.run(_collect_streaming_response(response)))

    assert response.status_code == 200
    assert body == b"ok"
    assert len(fake_client.requests) == 2
    assert fake_client.requests[1].headers["authorization"] == "Bearer fresh-token"


def test_post_requests_do_not_retry_transient_upstream_statuses(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.requests = []

        async def send(self, request, stream):
            self.requests.append(request)
            return httpx.Response(503, request=request, content=b"try later")

    fake_client = FakeClient()
    monkeypatch.setattr(main, "httpx_client", fake_client, raising=False)
    monkeypatch.setattr(main.config, "UPSTREAM_RETRY_ATTEMPTS", 3)

    response = asyncio.run(
        main._streaming_proxy(
            "POST",
            "https://api.x.ai/v1/chat/completions",
            {"Authorization": "Bearer token"},
            b"{}",
        )
    )
    body = b"".join(asyncio.run(_collect_streaming_response(response)))

    assert response.status_code == 503
    assert body == b"try later"
    assert len(fake_client.requests) == 1


def test_get_requests_retry_transient_upstream_statuses(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.requests = []

        async def send(self, request, stream):
            self.requests.append(request)
            status = 503 if len(self.requests) == 1 else 200
            return httpx.Response(status, request=request, content=b"ok")

    fake_client = FakeClient()
    monkeypatch.setattr(main, "httpx_client", fake_client, raising=False)
    monkeypatch.setattr(main.config, "UPSTREAM_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(main.config, "UPSTREAM_RETRY_DELAY", 0.0)

    response = asyncio.run(
        main._streaming_proxy(
            "GET",
            "https://api.x.ai/v1/models",
            {"Authorization": "Bearer token"},
            b"",
        )
    )
    body = b"".join(asyncio.run(_collect_streaming_response(response)))

    assert response.status_code == 200
    assert body == b"ok"
    assert len(fake_client.requests) == 2


def test_upstream_connection_errors_return_sanitized_response(monkeypatch):
    class FakeClient:
        async def send(self, request, stream):
            raise httpx.ConnectError("secret backend detail", request=request)

    monkeypatch.setattr(main, "httpx_client", FakeClient(), raising=False)
    monkeypatch.setattr(main.config, "UPSTREAM_RETRY_ATTEMPTS", 1)

    response = asyncio.run(
        main._streaming_proxy(
            "GET",
            "https://api.x.ai/v1/models",
            {"Authorization": "Bearer token"},
            b"",
        )
    )
    assert response.status_code == 502
    assert b"secret backend detail" not in response.body
    assert b"Upstream request failed" in response.body


def test_response_headers_strip_connection_nominated_headers(monkeypatch):
    class FakeClient:
        async def send(self, request, stream):
            return httpx.Response(
                200,
                request=request,
                headers={
                    "Connection": "X-Hop-Response",
                    "X-Hop-Response": "remove-me",
                    "Proxy-Authenticate": "remove-me",
                    "Transfer-Encoding": "chunked",
                    "X-Keep": "keep-me",
                },
                content=b"ok",
            )

    monkeypatch.setattr(main, "httpx_client", FakeClient(), raising=False)

    response = asyncio.run(
        main._streaming_proxy(
            "GET",
            "https://api.x.ai/v1/models",
            {"Authorization": "Bearer token"},
            b"",
        )
    )
    body = b"".join(asyncio.run(_collect_streaming_response(response)))

    assert response.status_code == 200
    assert body == b"ok"
    assert response.headers["x-keep"] == "keep-me"
    assert "connection" not in response.headers
    assert "x-hop-response" not in response.headers
    assert "proxy-authenticate" not in response.headers
    assert "transfer-encoding" not in response.headers


async def _collect_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return chunks


def json_loads(raw: bytes):
    import json

    return json.loads(raw.decode("utf-8"))
