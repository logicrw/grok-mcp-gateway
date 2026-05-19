# Changelog

All notable changes to this fork are documented here. Dates use `YYYY-MM-DD`.

## Unreleased

### Added

- Add a fixed `x_posts.v1` MCP output contract with `schema_version`,
  `tool_version`, `backend`, `timeline_verified=false`, `warnings`,
  `filter_reliability`, `request`, `sources`, and normalized `posts`.
- Add `mcp_server.py` as the JSON-RPC protocol layer and `xai_responses.py` as
  the shared xAI Responses API adapter.
- Add sanitized upstream error handling for MCP tool calls and token refresh
  failures.
- Add headless bootstrap support when imported `HERMES_AUTH_PATH` credentials
  already exist but the Hermes CLI is not installed.
- Add `GROK_GATEWAY_PORT_AUTOSCAN` and keep service mode fail-fast on occupied
  ports by default.
- Add `GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS` for sanitized debug logging.
- Document the project scope explicitly so users do not confuse it with a
  general MCP router, Node.js template, Docker deployment, or official X API MCP
  replacement.
- Add `x_posts`, a structured MCP extraction tool for handles, topics, flexible
  time ranges, and best-effort engagement filters on top of the existing xAI
  `x_search` backend.
- Keep `x_latest_posts` as a shortcut for the common single-handle latest-posts
  workflow.

### Changed

- Rename `engagement_filter` to `best_effort_filters` in the public `x_posts`
  schema. The old key is still accepted as a deprecated compatibility alias.
- Restrict `x_posts` sorting to `latest` and `relevance` to avoid implying
  API-grade popularity sorting.
- Upgrade MCP initialize responses to protocol version `2025-06-18`.
- Move startup hard exits to the CLI boundary; FastAPI lifespan now raises
  normal startup exceptions instead of calling `sys.exit()`.
- Update README and service examples to make the official X API boundary and
  LaunchAgent/systemd environment requirements explicit.

## 2026-05-18

### Added

- Rename and present the fork as Grok MCP Gateway.
- Add a resident HTTP MCP endpoint at `/mcp` with the focused `x_search` tool.
- Support non-Grok local models using X Search indirectly through MCP-capable
  clients.
- Add client configuration examples for Alma, LiteLLM, and OpenAI-compatible SDK
  usage.
- Add persistent-run documentation for macOS LaunchAgent and systemd.
- Add health, deep health, metrics, and MCP smoke-test documentation.

### Changed

- Keep the upstream Grok OAuth proxy and headless OAuth transfer flow from
  `yelixir-dev/grok-oauth-proxy`.
- Preserve compatibility with existing `GROK_PROXY_*` environment variables and
  default token-state paths.
