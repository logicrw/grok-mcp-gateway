# Changelog

All notable changes to this fork are documented here. Dates use `YYYY-MM-DD`.

## Unreleased

### Added

- Add `x_retrieve.v1` as the single public/default MCP retrieval tool for
  semantic X research, structured post retrieval, source discovery, reaction
  tracking, and latest-by-handle retrieval.
- Keep an internal `x_posts.v1` normalization contract with `schema_version`,
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
- Add clean-environment MCP HTTP tests, stricter sanitizer coverage, invalid MCP
  params tests, and xAI 401 refresh-retry tests.
- Document the project scope explicitly so users do not confuse it with a
  general MCP router, Node.js template, Docker deployment, or official X API MCP
  replacement.
- Preserve the previous `x_posts` and `x_latest_posts` capabilities inside
  `x_retrieve` modes instead of exposing separate public MCP tools.
- Add retrieve-specific model environment variables:
  `GROK_PROXY_RETRIEVE_MODEL` and `GROK_PROXY_RETRIEVE_RAW_MODEL`, while keeping
  `GROK_PROXY_MCP_MODEL` and `GROK_PROXY_MCP_RAW_MODEL` as compatibility
  fallbacks.

### Changed

- Default `GROK_GATEWAY_MCP_TOOL_ALLOWLIST` is now `x_retrieve`.
- Remove `x_search`, `x_posts`, and `x_latest_posts` from the public vNext MCP
  `tools/list`; calls to those old tool names now return a clear removed-tool
  error pointing to `x_retrieve`.
- Extend MCP metrics with `mcp_x_retrieve_quality_gate_total` and
  `mcp_x_retrieve_raw_expansion_total` so production raw-expansion behavior is
  observable.
- Rename `engagement_filter` to `best_effort_filters` in the internal
  structured-post request builder. The old key is still accepted as a deprecated
  compatibility alias for the builder.
- Restrict structured-post sorting to `latest` and `relevance` to avoid
  implying API-grade popularity sorting.
- Upgrade MCP initialize responses to protocol version `2025-06-18`.
- Move startup hard exits to the CLI boundary; FastAPI lifespan now raises
  normal startup exceptions instead of calling `sys.exit()`.
- Update README and service examples to make the official X API boundary and
  LaunchAgent/systemd environment requirements explicit.
- Keep date-only `to_date` values unchanged to match xAI's inclusive date-range
  documentation.
- Make `x_posts.v1` contract fields gateway-owned instead of trusting generated
  model JSON for `request`, `filter_reliability`, `backend`, or
  `timeline_verified`.
- Return pure serialized JSON in MCP text content for post-extraction results.

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
