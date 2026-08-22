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
- Add target-status handling for `x_retrieve`: explicit X status URLs and
  15-20 digit status IDs now populate `request.target_status_ids`,
  `target_match`, concurrent public oEmbed recovery, and one batched exact
  fallback before `no_match` is returned.
- Add a narrow public oEmbed fallback for explicit target status IDs when
  generated retrieval misses the target or returns only empty text.
- Add route-aware Grok 4.5 reasoning, total/stage deadlines, explicit-target
  caps, deterministic non-JSON status parsing, and low-cardinality stage/error/
  usage metrics.
- Add Ruff and BasedPyright CI gates and make warnings fail the Python test
  matrix.

### Changed

- Bump the MCP `x_retrieve` stable retrieval fallback from `grok-4.3` to
  `grok-4.5`, keeping `GROK_PROXY_RETRIEVE_MODEL` and `GROK_PROXY_MCP_MODEL`
  override precedence unchanged. See the official Grok 4.5 and X Search tool
  contracts at https://docs.x.ai/developers/grok-4-5 and
  https://docs.x.ai/developers/tools/x-search.
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
- Clarify the long-term thin-gateway boundary: official X API providers,
  persistent databases/caches, learned routing, and in-gateway multi-query
  planning remain outside the project unless production evidence changes that
  decision.
- Document Agent-versus-gateway ownership, on-demand model evaluation, local
  schema-cache refresh, and the repository evidence-retention policy.
- Keep date-only `to_date` values unchanged to match xAI's inclusive date-range
  documentation.
- Make `x_posts.v1` contract fields gateway-owned instead of trusting generated
  model JSON for `request`, `filter_reliability`, `backend`, or
  `timeline_verified`.
- Return pure serialized JSON in MCP text content for post-extraction results.
- Preserve short stage diagnostics for general retrieval when an upstream raw
  stage returns non-JSON text with no usable posts. Exact-target responses omit
  unstructured raw text and previews so nearby posts cannot cross that boundary.
- Return `x_retrieve.v1` structured error payloads for runtime retrieval
  failures, so agents do not receive bare non-JSON `x_retrieve failed` text.
- Keep Composer behind the general quality gate and remove it from the exact
  target path. Exact targets now use the active stable model (Grok 4.5 by
  default), concurrent oEmbed, and at most one batched fallback using that same
  resolved stable model.
- Extend shallow/deep health with active stable/raw model IDs and upstream model
  listing status without treating an unlisted model as a proven entitlement
  failure.
- Raise the supported runtime to Python 3.10+ and refresh FastAPI, Uvicorn,
  pytest, Ruff, and BasedPyright to versions with current security fixes.

### Fixed

- Read `GROK_PROXY_RETRIEVE_MODEL` / `GROK_PROXY_MCP_MODEL` from `config.py` and
  use that value for both `mcp_x_search.DEFAULT_MODEL` and the Smart Lane model
  in `retrieve_policy.get_routing_config()`.
- Restore BasedPyright on `oauth_flow.CallbackHandler.log_message` and
  `_parse_expires_in` so the CI type gate is green.
- Export recorded `mcp_x_retrieve_route_total` series from
  `retrieve_metrics.metrics_lines()`.
- Deduplicate merged `posts` by status ID/URL in
  `retrieve_payload.merge_stage_payload`.
- Point README LaunchAgent install instructions at
  `services/service-examples.md` instead of a missing plist file.

- Preserve the exception type when an upstream timeout has no message, and let
  explicit status-ID retrieval continue to the public oEmbed fallback after a
  stable X Search timeout instead of failing before deterministic recovery.

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
