import asyncio
import base64
import importlib
import json
import os
import stat
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
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


def test_proxy_api_key_accepts_bearer_or_x_proxy_header():
    assert main._request_has_valid_proxy_api_key({"authorization": "Bearer secret"}, "secret")
    assert main._request_has_valid_proxy_api_key({"x-proxy-api-key": "secret"}, "secret")
    assert not main._request_has_valid_proxy_api_key({"authorization": "Bearer wrong"}, "secret")
    assert not main._request_has_valid_proxy_api_key({}, "secret")


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


def test_missing_hermes_auth_file_stops_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(token_manager.shutil, "which", lambda command: "/usr/local/bin/hermes" if command == "hermes" else None)
    monkeypatch.setattr(token_manager, "HERMES_AUTH_PATH", tmp_path / "missing-auth.json")

    with pytest.raises(RuntimeError, match="Hermes auth.json not found"):
        asyncio.run(token_manager.init_local_state())


def test_missing_hermes_cli_stops_startup(monkeypatch):
    monkeypatch.setattr(token_manager.shutil, "which", lambda command: None)

    with pytest.raises(RuntimeError, match="Hermes Agent CLI not found"):
        asyncio.run(token_manager.init_local_state())


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


def test_http_mcp_lists_x_search_tool():
    with TestClient(main.app) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 200
    assert response.json()["result"]["tools"][0]["name"] == "x_search"


def test_http_mcp_notifications_return_accepted():
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
