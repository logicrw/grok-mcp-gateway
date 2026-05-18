# Grok OAuth Proxy

[English](README.md) | [Korean](README.ko.md)

A local Grok OAuth gateway for multi-agent AI clients.

This fork exposes xAI Grok through an OpenAI-compatible local proxy and adds a
resident HTTP MCP bridge for xAI X Search. That means clients such as Alma, and
other local agent setups built around Claude Code, Antigravity, Codex, Gemini
CLI, LiteLLM, or similar provider layers, can use your Hermes Agent xAI OAuth
session to:

- call Grok models without a separate xAI API key;
- share one long-running local proxy process;
- give non-Grok models access to X Search through MCP.

The important boundary is this: Claude, GPT, Gemini, and other non-Grok models
do not become natively X-aware. Their client calls this proxy's `x_search` MCP
tool, and the proxy performs the X Search through xAI using your local OAuth
session.

> Attribution: this project is based on
> [yelixir-dev/grok-oauth-proxy](https://github.com/yelixir-dev/grok-oauth-proxy).
> This fork keeps the upstream Grok OAuth proxy/headless flow and adds the
> resident HTTP MCP `x_search` gateway used by local multi-agent clients.

## Why This Fork Exists

Hermes Agent can authorize xAI Grok with X Premium/Premium+ through OAuth. That
solves the first problem: getting a valid Grok OAuth token without managing a
separate xAI API key.

Local AI clients usually need two more things:

1. An OpenAI-compatible base URL so they can select Grok like any other model.
2. A tool interface so models that are not Grok can still ask for X Search when
   the client supports MCP tools.

This fork provides both in one resident process:

```text
Alma / LiteLLM / local agents
        |
        | OpenAI-compatible API
        v
http://127.0.0.1:9996/v1/*
        |
        | Hermes-derived xAI OAuth bearer token
        v
https://api.x.ai/v1/*
```

```text
Alma / MCP-capable local agents
        |
        | HTTP MCP JSON-RPC
        v
http://127.0.0.1:9996/mcp
        |
        | xAI Responses API + x_search tool
        v
https://api.x.ai/v1/responses
```

## What You Get

- **Grok model gateway** - Proxies OpenAI-compatible requests to xAI using your
  Hermes Agent OAuth session.
- **No hard-coded xAI client id** - Imports the public OAuth `client_id` from
  Hermes auth state at runtime.
- **Independent token lifecycle** - Copies Hermes credentials into a local
  proxy-owned token state and refreshes independently to avoid racing Hermes.
- **Hermes auth watcher** - Re-imports xAI OAuth when Hermes re-authenticates.
- **OpenAI-compatible paths** - Forwards `/v1/chat/completions`,
  `/v1/responses`, `/v1/models`, and other xAI API paths.
- **Resident HTTP MCP X Search** - Exposes `x_search` at `/mcp` so multiple
  clients can share one process instead of spawning one tool server per agent.
- **MCP concurrency guard** - Bounds simultaneous X Search calls when several
  agents use the same gateway.
- **Optional Responses API X Search shim** - Can inject xAI `x_search` into
  `/v1/responses` requests for clients that can call Responses but cannot attach
  xAI tools themselves.
- **Streaming support** - Streams upstream responses back to clients.
- **Prometheus metrics** - Exposes request, token, and MCP X Search metrics at
  `/metrics`.
- **Deep health checks** - `/health?deep=1` verifies an actual upstream xAI
  request.
- **Headless deployment helpers** - Supports exporting only xAI OAuth
  credentials to a server and running as a systemd service.
- **Safer defaults** - Binds to loopback by default, requires `PROXY_API_KEY`
  for non-loopback binds, strips incoming credentials before forwarding, and
  writes token state with private file permissions.

## Requirements

- Python 3.9+
- Hermes Agent installed and authorized with xAI Grok OAuth
- An xAI/X subscription or entitlement that allows the requested Grok/X Search
  features
- For MCP usage: a client that supports HTTP MCP servers

Before starting the proxy, verify Hermes has xAI OAuth credentials:

```bash
python -c 'import json, pathlib; data=json.load(open(pathlib.Path.home()/".hermes/auth.json")); print("xai-oauth present:", "xai-oauth" in data.get("providers", {}) or bool(data.get("credential_pool", {}).get("xai-oauth")))'
```

If it prints `False`, run Hermes Agent's model/OAuth flow first and complete the
xAI Grok login.

## Quick Install

```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy
./install.sh
```

Start the proxy:

```bash
source .venv/bin/activate
python main.py
```

The default server is:

```text
http://127.0.0.1:9996
```

If port `9996` is occupied, the app scans upward for an available port.

## Smoke Tests

Health:

```bash
curl -sS http://127.0.0.1:9996/health
```

Deep health:

```bash
curl -sS http://127.0.0.1:9996/health?deep=1
```

Grok chat:

```bash
curl -sS http://127.0.0.1:9996/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4.3",
    "messages": [{"role": "user", "content": "Reply with one short sentence."}]
  }'
```

MCP tool list:

```bash
curl -sS http://127.0.0.1:9996/mcp \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Live X Search through MCP:

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

Metrics:

```bash
curl -sS http://127.0.0.1:9996/metrics
```

## Client Configuration

### Alma Custom Provider for Grok

Use this when you want Alma to call Grok as a model.

```text
Provider Name: Grok OAuth Proxy
Base URL:      http://127.0.0.1:9996/v1
API Key:       dummy
API Format:    Chat Completions (/chat/completions)
```

Notes:

- The API key can be any non-empty placeholder if Alma requires one.
- The proxy strips client-supplied `Authorization` before forwarding and injects
  its own xAI OAuth bearer token.
- If a client appends `/v1` automatically, use `http://127.0.0.1:9996` instead
  of `http://127.0.0.1:9996/v1`.

### Alma MCP Server for X Search

Use this when you want Alma agents, including non-Grok models, to call X Search
through MCP.

```json
{
  "mcpServers": {
    "x_search": {
      "url": "http://127.0.0.1:9996/mcp"
    }
  }
}
```

This is the preferred architecture for local multi-agent use: one resident proxy
process, many clients.

### LiteLLM

Some clients expect `api_base` to include `/v1`; others append `/v1`
themselves. Use the form your client expects.

```yaml
model_list:
  - model_name: grok-4.3
    litellm_params:
      model: openai/grok-4.3
      api_base: http://127.0.0.1:9996/v1
      api_key: dummy
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:9996/v1",
    api_key="dummy",
)

response = client.chat.completions.create(
    model="grok-4.3",
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)
print(response.choices[0].message.content)
```

## MCP X Search

The MCP server exposes one tool: `x_search`.

Tool arguments:

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | yes | Natural-language search request. Include topic, handles, time window, and desired output. |
| `allowed_x_handles` | string array | no | Restrict search to specific X handles, for example `["elonmusk", "xai"]`. |
| `enable_image_understanding` | boolean | no | Ask xAI to use image understanding when supported. |
| `enable_video_understanding` | boolean | no | Ask xAI to use video understanding when supported. |
| `model` | string | no | xAI model for the MCP call. Defaults to `GROK_PROXY_MCP_MODEL` or `grok-4.3`. |
| `raw` | boolean | no | Return compact raw xAI response JSON instead of extracted text. |

Example MCP call body:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "x_search",
    "arguments": {
      "query": "Find the latest posts from @xai about Hermes Agent and summarize them in Chinese.",
      "allowed_x_handles": ["xai"]
    }
  }
}
```

Why MCP instead of only relying on Grok's model behavior:

- The model gateway lets a client use Grok as the active model.
- MCP lets any model in an MCP-capable client call X Search as an explicit tool.
- This keeps the capability client-visible, debuggable, and shareable across
  several local agents.

## Optional Auto X Search Shim

Some clients can call `/v1/responses` but cannot attach xAI server-side tools in
their provider UI. For those clients, this proxy can inject `x_search` into
Responses API requests.

It is disabled by default:

```bash
GROK_PROXY_AUTO_X_SEARCH=true python main.py
```

Optional restrictions:

```bash
GROK_PROXY_AUTO_X_SEARCH=true \
GROK_PROXY_X_SEARCH_ALLOWED_HANDLES=xai,elonmusk \
GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING=true \
python main.py
```

Use the MCP route first when possible. MCP is clearer because the client knows
it is calling a tool. The auto shim is a compatibility fallback for clients that
cannot expose xAI tools cleanly.

## Headless Server Setup

A reliable headless setup separates the desktop Hermes token chain from the
server proxy token chain.

Recommended split-chain flow:

```text
1. Authenticate Hermes locally with browser-based xAI OAuth.
2. Export only the xAI OAuth credentials.
3. Import those credentials on the headless proxy host.
4. Re-authenticate Hermes locally so Hermes and the proxy each own their own
   refresh-token chain.
```

### Install on the Server

```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy
./install.sh --headless
```

To install and enable the systemd service:

```bash
./install.sh --headless --enable-service
```

### Export xAI OAuth from a Browser Machine

On the machine where Hermes is already authenticated:

```bash
cd grok-oauth-proxy
python scripts/export_xai_oauth.py > ~/xai-oauth.json
```

Copy it to the server:

```bash
scp ~/xai-oauth.json user@example.com:/tmp/xai-oauth.json
```

Import it on the server:

```bash
python scripts/import_xai_oauth.py /tmp/xai-oauth.json
rm -f /tmp/xai-oauth.json
chmod 700 ~/.hermes
chmod 600 ~/.hermes/auth.json
sudo systemctl restart grok-oauth-proxy
```

The export file contains refresh tokens. Treat it like a password, do not commit
it, and delete it after import.

### One-Step Remote Refresh

From the browser machine:

```bash
python scripts/refresh_remote_xai_oauth.py \
  --host user@example.com \
  --identity ~/.ssh/id_ed25519 \
  --print-reauth-command
```

The helper:

- exports only Hermes `xai-oauth`;
- copies it over SSH;
- imports it on the remote host;
- clears stale proxy token state;
- restarts the systemd service unless `--no-restart` is set;
- runs `/health?deep=1` unless `--no-health` is set;
- optionally prints the Hermes re-auth command for the split-chain flow.

## Running Persistently

### macOS LaunchAgent

The repository includes macOS service notes in [services/README.md](services/README.md).

After code or environment changes, reload the LaunchAgent:

```bash
launchctl kickstart -k gui/$(id -u)/io.logicrw.grok-oauth-proxy
```

### systemd

Example unit:

```ini
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

## Configuration

All settings are environment variables.

| Variable | Default | Description |
| --- | --- | --- |
| `PROXY_HOST` | `127.0.0.1` | Bind address. Non-loopback binds require `PROXY_API_KEY`. |
| `PROXY_PORT` | `9996` | Base port. If occupied, scans upward. |
| `PROXY_API_KEY` | unset | Optional local proxy auth key. Required when binding outside loopback. Accepted as `Authorization: Bearer <key>` or `X-Proxy-Api-Key: <key>`. |
| `GROK_PROXY_AUTH_STATE` | `~/.local/state/grok-oauth-proxy/auth_state.json` | Proxy-owned OAuth token state. |
| `HERMES_AUTH_PATH` | `~/.hermes/auth.json` | Hermes auth store. |
| `LOG_LEVEL` | `INFO` | Python app log level. |
| `TOKEN_REFRESH_WINDOW` | `300` | Seconds before expiry to refresh in the background. |
| `HERMES_POLL_INTERVAL` | `60` | Seconds between Hermes auth file checks. |
| `UPSTREAM_RETRY_ATTEMPTS` | `2` | Retry attempts for idempotent upstream requests and transient connection errors. |
| `UPSTREAM_RETRY_DELAY` | `1.0` | Base delay between upstream retries. |
| `GROK_PROXY_MCP_MODEL` | `grok-4.3` | Default xAI model used by MCP `x_search`. |
| `GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` | `3` | Max concurrent MCP `x_search` calls. |
| `GROK_PROXY_AUTO_X_SEARCH` | `false` | Inject xAI `x_search` into `/v1/responses` requests. |
| `GROK_PROXY_X_SEARCH_ALLOWED_HANDLES` | unset | Comma-separated handle allowlist for auto-injected X Search. |
| `GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING` | `false` | Enable image understanding for auto-injected X Search. |
| `GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING` | `false` | Enable video understanding for auto-injected X Search. |

Example:

```bash
PROXY_PORT=9996 LOG_LEVEL=DEBUG python main.py
```

## API Surfaces

The proxy is path-transparent. Anything not handled locally is forwarded to
`https://api.x.ai/{path}` with the proxy's current xAI OAuth bearer token.

Local endpoints:

| Endpoint | Method | Description |
| --- | --- | --- |
| `/health` | `GET` | Local status and token expiry. |
| `/health?deep=1` | `GET` | Status plus a real upstream `/v1/models` check. |
| `/metrics` | `GET` | Prometheus-compatible metrics. |
| `/mcp` | `POST` | HTTP JSON-RPC MCP endpoint exposing `x_search`. |
| `/{path:path}` | any | Forwarded to `https://api.x.ai/{path}`. |

Common forwarded xAI paths:

| Path | Use |
| --- | --- |
| `/v1/chat/completions` | OpenAI-compatible chat clients. |
| `/v1/responses` | Responses API clients and xAI server-side tools. |
| `/v1/models` | Model discovery and deep health checks. |
| Other `/v1/*` paths | Forwarded unchanged when the upstream account supports them. |

## Token Model

Hermes and the proxy use the same xAI account and OAuth client identity, but
they do not share one live token file.

- Hermes owns `~/.hermes/auth.json`.
- The proxy owns `~/.local/state/grok-oauth-proxy/auth_state.json` by default.
- The proxy imports from either Hermes auth shape:
  - `providers.xai-oauth`
  - `credential_pool.xai-oauth`
- The proxy refreshes its own copied refresh-token chain.
- If Hermes re-authenticates, the proxy watcher can re-import the new xAI OAuth
  credentials.

This design avoids having Hermes and the proxy write to the same token state at
the same time.

## Security Notes

- Keep the proxy on `127.0.0.1` unless you have a clear reason to expose it.
- If binding to `0.0.0.0` or another non-loopback address, set `PROXY_API_KEY`
  and put TLS/authentication in front of it when crossing machines.
- Do not commit `auth_state.json`, `.hermes/auth.json`, exported
  `xai-oauth.json`, logs containing bearer tokens, or service files with real
  credentials.
- The proxy strips incoming `Authorization`, `Proxy-Authorization`, cookies,
  hop-by-hop headers, and spoofable forwarding headers before calling xAI.
- Uvicorn access logs are disabled by default to reduce accidental query-string
  logging.
- Local token files are written with private permissions when the proxy creates
  them.
- This project reuses the OAuth client identity Hermes obtained during xAI Grok
  OAuth login. Use it at your own discretion with respect to xAI's terms and
  account rules.

## Troubleshooting

### `xai-oauth present: False`

Hermes has not completed xAI Grok OAuth, or `HERMES_AUTH_PATH` points to the
wrong auth file. Re-run the Hermes xAI OAuth flow, then restart the proxy.

### Alma can use Grok but cannot use X Search

Configure the MCP server separately:

```json
{
  "mcpServers": {
    "x_search": {
      "url": "http://127.0.0.1:9996/mcp"
    }
  }
}
```

The model provider and MCP server are two separate integrations.

### MCP lists the tool but calls fail

Check:

```bash
curl -sS http://127.0.0.1:9996/health?deep=1
curl -sS http://127.0.0.1:9996/metrics | rg mcp_x_search
```

Common causes:

- xAI OAuth has expired and needs Hermes re-authentication.
- The account does not have access to the requested model or X Search feature.
- `allowed_x_handles` is too restrictive.
- The client is calling `/mcp` with GET instead of POST.

### Client gets 401 or 403

The proxy may need fresh Hermes credentials, or the xAI account may not be
entitled to the requested model/tool. Re-authenticate Hermes, restart the proxy,
then run `/health?deep=1`.

### Base URL confusion

Use:

```text
http://127.0.0.1:9996/v1
```

when the client expects an OpenAI base URL.

Use:

```text
http://127.0.0.1:9996
```

when the client appends `/v1` itself.

## Development

Install dev dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Run locally:

```bash
python main.py
```

Run tests:

```bash
pytest -q
```

Useful checks before publishing:

```bash
git diff --check
pytest -q
rg -n "(ghp_|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|Bearer [A-Za-z0-9._-]{20,})" . -g '!README.md' -g '!*.pyc' -g '!__pycache__/**'
```

## Project Structure

```text
grok-oauth-proxy/
|-- main.py                         # FastAPI app, proxy routes, MCP HTTP endpoint
|-- mcp_x_search.py                 # MCP x_search handler backed by xAI Responses
|-- token_manager.py                # OAuth import, state, refresh, auth headers
|-- config.py                       # Environment configuration
|-- install.sh                      # Desktop/headless installer
|-- uninstall.sh                    # Local uninstall helper
|-- scripts/
|   |-- export_xai_oauth.py          # Export only Hermes xAI OAuth credentials
|   |-- import_xai_oauth.py          # Import xAI OAuth credentials on target host
|   `-- refresh_remote_xai_oauth.py  # SSH remote refresh helper
|-- services/
|   `-- README.md                    # Service manager notes
`-- tests/
```

## Upstream Relationship

Original project:

```text
https://github.com/yelixir-dev/grok-oauth-proxy
```

This fork:

```text
https://github.com/logicrw/grok-oauth-proxy
```

The upstream project focuses on the Grok OAuth proxy and headless OAuth transfer
flow. This fork keeps that work and adds the local multi-agent gateway layer:
resident HTTP MCP `x_search`, Alma-oriented configuration, MCP metrics, and
support for both Hermes `providers.xai-oauth` and `credential_pool.xai-oauth`
auth shapes.

## License

MIT
