# Grok OAuth Proxy

[English](README.md) | [한국어](README.ko.md)

A lightweight, local reverse proxy that exposes the [xAI Grok API](https://docs.x.ai/) through your existing **Hermes Agent** OAuth session. No API key required — it reuses the browser-based OAuth tokens already stored in `~/.hermes/auth.json`.

Built with [FastAPI](https://fastapi.tiangolo.com/) and designed to drop into any OpenAI-compatible client (e.g., LiteLLM, OpenAI Python SDK, curl).

> Fork note: this project is based on [yelixir-dev/grok-oauth-proxy](https://github.com/yelixir-dev/grok-oauth-proxy). This fork adds a resident HTTP MCP `x_search` bridge for local multi-agent clients such as Alma.

---

## Features

- **Zero-config OAuth** — Automatically copies and manages xAI OAuth tokens from Hermes Agent.
- **Independent token lifecycle** — Runs its own token refresh loop so it never races with Hermes.
- **Token prewarm** — Refreshes the access token in the background before it expires.
- **Hermes auth.json watcher** — Detects re-authentication in Hermes and re-imports tokens automatically.
- **Streaming support** — Full SSE streaming for `/v1/chat/completions`.
- **Upstream retry** — Retries idempotent requests on 502/503/429 and transient connection failures; avoids duplicate-generating POST retries.
- **Prometheus metrics** — Built-in `/metrics` endpoint for request counts, durations, and token expiry.
- **Deep health checks** — `/health?deep=1` performs an actual upstream ping to verify end-to-end connectivity.
- **Resident MCP X Search** — Optional `/mcp` endpoint exposes xAI `x_search` as a shared local MCP tool without spawning one process per client.
- **MCP concurrency guard** — Bounds concurrent `x_search` tool calls when several local agents share the same proxy process.
- **Secure file permissions** — Local token copy is written with `0o600` permissions.

---

## Architecture

```
┌─────────────────┐     HTTP      ┌──────────────────────┐     HTTPS + Bearer    ┌─────────────┐
│  Your Client    │ ─────────────>│  Grok OAuth Proxy    │ ─────────────────────>│  api.x.ai   │
│  (LiteLLM, etc) │   OpenAI fmt  │  (127.0.0.1:9996)    │   OAuth token         │   (xAI)     │
└─────────────────┘               └──────────────────────┘                       └─────────────┘
                                           │
                                           │ reads / refreshes
                                           ▼
                                    ┌──────────────┐
                                    │ auth_state   │
                                    │ .json        │  (copied from Hermes, 0o600)
                                    └──────────────┘
```

1. On startup, the proxy first verifies that the Hermes CLI is installed.
2. It then verifies that Hermes has `xai-oauth` credentials in `~/.hermes/auth.json`.
3. It copies the OAuth tokens and public `client_id` claim from Hermes into a local `auth_state.json`.
4. All subsequent token refreshes are performed independently against `https://auth.x.ai/oauth2/token` using that imported client id.
5. Incoming requests are forwarded to `https://api.x.ai/v1/*` with the current Bearer token injected.

For MCP clients, the same resident process also exposes `POST /mcp`:

```
┌─────────────────┐     HTTP MCP     ┌──────────────────────┐     xAI Responses + x_search
│  Alma / Agents  │ ────────────────> │  Grok OAuth Proxy    │ ───────────────────────────> api.x.ai
│  MCP clients    │   /mcp JSON-RPC   │  (127.0.0.1:9996)    │
└─────────────────┘                   └──────────────────────┘
```

---

## Installation

### Quick Install (Recommended)

```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy

# Desktop
./install.sh

# Headless server
./install.sh --headless

# Headless + auto-enable systemd service
./install.sh --headless --enable-service
```

### Manual Install

#### Prerequisites
- Python 3.9+
- An active [Hermes Agent](https://github.com/NousResearch/hermes-agent) installation
- xAI Grok OAuth already configured in Hermes

```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Quick Start (Desktop with Browser)

```bash
source .venv/bin/activate
python main.py
```

The proxy will start on `http://127.0.0.1:9996` (scans upward if the port is taken).

### Test it

```bash
curl http://127.0.0.1:9996/health
```

```bash
curl http://127.0.0.1:9996/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4.3",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## Headless Server Setup

**Quick install:**
```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy
./install.sh --headless --enable-service
```

This project is designed to work on headless servers (VPS, cloud instances, containers, etc.) where you cannot open a browser for OAuth login.

### Recommended Token Ownership Flow

For the most reliable long-running setup, give the proxy its own refresh-token
chain instead of making Hermes and the proxy share one live chain:

```text
Hermes local OAuth login
→ transfer the resulting xAI OAuth refresh-token chain to grok-oauth-proxy
  (local proxy or headless server)
→ re-authenticate Hermes locally
→ Hermes and grok-oauth-proxy now refresh independently
```

Why: xAI/Grok access tokens are short-lived, and live testing showed refresh
tokens rotate when refreshed. A transferred chain can keep the proxy alive, while
a second Hermes login gives your desktop Hermes a separate chain. If xAI changes
its session policy later, fall back to one active owner and rerun
`refresh_remote_xai_oauth.py` whenever you re-authenticate Hermes.

### Recommended Installation (using install.sh)

1. **On a machine with browser** (your laptop or desktop):
   - Install Hermes
   - Run `hermes model` and complete xAI Grok OAuth login
   - Verify the token exists:
     ```bash
     python -c 'import json, pathlib; data=json.load(open(pathlib.Path.home()/".hermes/auth.json")); print("xai-oauth present:", "xai-oauth" in data.get("providers", {}) or bool(data.get("credential_pool", {}).get("xai-oauth")))'
     ```

2. **Copy only the xAI OAuth credentials to the server** (Recommended)

   On the machine with browser, run:
   ```bash
   cd grok-oauth-proxy
   python scripts/export_xai_oauth.py > ~/xai-oauth.json
   ```

   Copy the exported file to the server:
   ```bash
   scp ~/xai-oauth.json user@your-server:/tmp/xai-oauth.json
   ```

   On the headless server, import it:
   ```bash
   python scripts/import_xai_oauth.py /tmp/xai-oauth.json
   rm -f /tmp/xai-oauth.json
   chmod 700 ~/.hermes
   chmod 600 ~/.hermes/auth.json
   sudo systemctl restart grok-oauth-proxy
   ```

   By default, `import_xai_oauth.py` also removes the proxy's stale local
   `auth_state.json`, so the next restart rehydrates from the newly imported
   Hermes credentials. Use `--no-reset-proxy-state` only if you intentionally
   want to leave the running proxy token state untouched.

   Or refresh a remote headless server in one step from your browser machine:
   ```bash
   python scripts/refresh_remote_xai_oauth.py \
    --host user@example.com \
    --identity ~/.ssh/id_ed25519 \
     --print-reauth-command
   ```

   The one-step helper exports only `xai-oauth`, copies it over SSH, imports it,
   resets stale proxy token state, restarts `grok-oauth-proxy`, and runs a deep
   health check. With `--print-reauth-command`, it also prints the final Hermes
   re-auth command for the recommended split-chain flow.

   This approach only exports the `xai-oauth` section, which is much safer than copying the entire `~/.hermes/auth.json`.

3. **On the headless server** (Recommended)

   ```bash
   git clone https://github.com/logicrw/grok-oauth-proxy.git
   cd grok-oauth-proxy

   # Basic headless install
   ./install.sh --headless

   # Or install + enable systemd service at once
   ./install.sh --headless --enable-service
   ```

   The `install.sh --headless` script will:
   - Check for the exported `xai-oauth.json` file
   - Create a virtual environment
   - Install dependencies
   - Import your xAI OAuth credentials

   On first start the proxy will:
   - Detect Hermes CLI
   - Read `~/.hermes/auth.json`
   - Extract `xai-oauth` tokens + `client_id` from JWT claims
   - Create `~/.local/state/grok-oauth-proxy/auth_state.json` (0o600)

### Running Persistently on Headless

**systemd (Linux)** example:

```ini
# /etc/systemd/system/grok-oauth-proxy.service
[Unit]
Description=Grok OAuth Proxy for Hermes
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/grok-oauth-proxy
Environment=HOME=/home/youruser
Environment=HERMES_AUTH_PATH=/home/youruser/.hermes/auth.json
Environment=PATH=/home/youruser/grok-oauth-proxy/.venv/bin:/home/youruser/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/youruser/grok-oauth-proxy/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grok-oauth-proxy
```

**macOS LaunchAgent** example is documented in [`services/README.md`](services/README.md).

Note: systemd services do not always inherit the interactive shell environment. The installer writes `HOME`, `HERMES_AUTH_PATH`, and a PATH that includes both the project virtualenv and `~/.local/bin` so the service can find Hermes CLI on headless hosts.

---

## Configuration

All settings are optional and read from environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_HOST` | `127.0.0.1` | Bind address. Non-loopback binds require `PROXY_API_KEY`. |
| `PROXY_PORT` | `9996` | Base port. If occupied, scans `+1` up to 20 times |
| `PROXY_API_KEY` | unset | Optional local proxy auth key. Required when binding outside loopback. Accepted as `Authorization: Bearer ***` or `X-Proxy-Api-Key: <key>`. |
| `GROK_PROXY_AUTH_STATE` | `~/.local/state/grok-oauth-proxy/auth_state.json` | Local proxy-owned token state path. |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `HERMES_AUTH_PATH` | `~/.hermes/auth.json` | Path to Hermes auth store |
| `TOKEN_REFRESH_WINDOW` | `300` | Seconds before expiry to trigger a background refresh |
| `HERMES_POLL_INTERVAL` | `60` | Seconds between Hermes auth.json change checks |
| `UPSTREAM_RETRY_ATTEMPTS` | `2` | Max attempts for idempotent upstream requests (`GET`, `HEAD`, `OPTIONS`, `TRACE`) and transient connection errors. Non-idempotent requests such as model-generating `POST` calls are not retried on 502/503/429 to avoid duplicate billing/side effects. A 401 token-refresh retry is still performed once. |
| `UPSTREAM_RETRY_DELAY` | `1.0` | Base delay in seconds between retries |
| `GROK_PROXY_MCP_MODEL` | `grok-4.3` | Default model used by the MCP `x_search` tool. |
| `GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` | `3` | Max concurrent MCP `x_search` tool calls in the resident proxy process. |
| `GROK_PROXY_AUTO_X_SEARCH` | `false` | Compatibility shim that injects `x_search` into `/v1/responses` requests for clients that cannot attach xAI tools themselves. |
| `GROK_PROXY_X_SEARCH_ALLOWED_HANDLES` | unset | Optional comma-separated handle allowlist for automatic `/v1/responses` x_search injection. |
| `GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING` | `false` | Enable image understanding for automatic `/v1/responses` x_search injection. |
| `GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING` | `false` | Enable video understanding for automatic `/v1/responses` x_search injection. |

### Example

```bash
PROXY_PORT=8080 LOG_LEVEL=DEBUG python main.py
```

---

## API Endpoints

This proxy is path-transparent: anything under `/{path:path}` is forwarded to `https://api.x.ai/{path}` with the current Hermes `xai-oauth` bearer token injected. That makes it usable for the same direct-to-xAI surfaces described in the Hermes xAI Grok OAuth guide, not only chat.

Common xAI surfaces:

| Surface | Example path | Notes |
|---------|--------------|-------|
| Chat / responses-compatible clients | `/v1/chat/completions`, `/v1/responses` | Supports normal and streaming requests; client-supplied `Authorization` is stripped and replaced. |
| Models | `/v1/models` | Used by deep health checks and model discovery. |
| TTS | `/v1/tts` | Reuses the same OAuth bearer token when the upstream endpoint is available to the account. |
| Image generation | `/v1/images/generations` or xAI image endpoints | Path-transparent forwarding keeps non-chat xAI features available. |
| Video generation | xAI Grok Imagine video endpoints | Forwarded unchanged; large/streaming responses are streamed back. |
| Transcription / audio | xAI audio endpoints | Forwarded unchanged. |
| X Search via Responses | `/v1/responses` with xAI search tools | Works as a normal Responses API request when the account/provider supports it. |

Local management endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/{path:path}` | Any | Proxies to `https://api.x.ai/{path}` |
| `/health` | `GET` | Proxy status and token expiry |
| `/health?deep=1` | `GET` | Deep health: actually pings `api.x.ai/v1/models` |
| `/metrics` | `GET` | Prometheus-compatible metrics |
| `/mcp` | `POST` | HTTP JSON-RPC MCP endpoint exposing the `x_search` tool |

### Health Response

```json
{
  "status": "ok",
  "provider": "xai-oauth",
  "api_base": "https://api.x.ai",
  "token_expires_at": "2026-05-17T11:46:33Z",
  "token_endpoint": "https://auth.x.ai/oauth2/token"
}
```

---

## LiteLLM Integration

```yaml
model_list:
  - model_name: grok-4.3
    litellm_params:
      model: openai/grok-4.3
      api_base: http://127.0.0.1:9996
      api_key: "dummy"  # proxy injects the real OAuth bearer token
```

---

## Alma MCP Integration

Use the resident HTTP MCP endpoint so multiple Alma agents can share the same proxy process:

```json
{
  "mcpServers": {
    "x_search": {
      "url": "http://127.0.0.1:9996/mcp"
    }
  }
}
```

Smoke-test the MCP tool list:

```bash
curl -sS http://127.0.0.1:9996/mcp \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Smoke-test a live X search:

```bash
curl -sS http://127.0.0.1:9996/mcp \
  -H "Content-Type: application/json" \
  --data '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "x_search",
      "arguments": {
        "query": "Search recent X posts from @xai about Hermes Agent. Reply in one short sentence.",
        "allowed_x_handles": ["xai"]
      }
    }
  }'
```

If the proxy is installed as a macOS LaunchAgent, reload it after code or environment changes:

```bash
launchctl kickstart -k gui/$(id -u)/io.logicrw.grok-oauth-proxy
```

---

## How It Works

### Token Isolation

Hermes and the proxy share the same xAI account and OAuth client identity, but **not the same token state file**:

- Hermes owns `~/.hermes/auth.json`
- The proxy owns `~/.local/state/grok-oauth-proxy/auth_state.json` by default (created on first start, `chmod 600`; override with `GROK_PROXY_AUTH_STATE`)
- The proxy does not ship an `XAI_CLIENT_ID` constant. It imports the public client id from Hermes token claims (`client_id`/`aud`) during first start and after Hermes re-authentication.

This means:
- Hermes can refresh its token without invalidating the proxy's session.
- The proxy can refresh its token without racing Hermes.
- If Hermes re-authenticates (new login), the background watcher detects the change and re-imports.

### Background Tasks

Two `asyncio` tasks run continuously while the proxy is up:

1. **Token Prewarm Watcher** — Checks token expiry every `TOKEN_REFRESH_WINDOW / 2` seconds. If the token is about to expire, it refreshes proactively so real API calls never hit a stale token.
2. **Hermes File Watcher** — Polls `~/.hermes/auth.json` mtime every `HERMES_POLL_INTERVAL` seconds. On change, re-imports the latest `xai-oauth` credentials.

---

## Security Notes

- The proxy listens on `127.0.0.1` by default. If `PROXY_HOST` is set to a non-loopback address such as `0.0.0.0`, startup is refused unless `PROXY_API_KEY` is configured.
- When `PROXY_API_KEY` is set, proxy requests must include either `Authorization: Bearer <key>` or `X-Proxy-Api-Key: <key>`. The client credential is stripped before forwarding; the proxy always injects its own xAI OAuth bearer token upstream.
- Hop-by-hop headers, incoming client credentials (`Authorization`, `Proxy-Authorization`, `Connection`, `TE`, etc.), cookies, and spoofable forwarding headers (`Forwarded`, `X-Forwarded-*`, `X-Real-IP`) are stripped before forwarding to `api.x.ai`.
- The local token state directory is created with `0o700` permissions when the proxy creates it, and `auth_state.json` is written atomically with `0o600` permissions. Existing token-state files are permission-repaired before reads when possible.
- Uvicorn access logs are disabled by default to avoid logging query strings; the app log records method/path/status only.
- The proxy uses the same OAuth `client_id` that Hermes obtained during xAI Grok OAuth login. The client id is imported from the local Hermes auth state at runtime, not hard-coded into the distributable source. This is technically a third-party client reuse; use at your own discretion with respect to xAI's Terms of Service.


---

## Development

```bash
source .venv/bin/activate
python main.py
```

### Tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

### Running in the background

```bash
nohup python main.py > proxy.log 2>&1 &
```

### Project Structure

```
grok-oauth-proxy/
├── main.py           # FastAPI app, proxy logic, background watchers
├── mcp_x_search.py   # Minimal MCP x_search tool handler
├── token_manager.py  # Async-safe OAuth token read / refresh
├── config.py         # Environment variable configuration
├── requirements.txt
└── README.md
```

---

## License

MIT
