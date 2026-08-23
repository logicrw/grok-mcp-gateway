# Changelog

All notable changes to this fork are documented here. Dates use `YYYY-MM-DD`.

## 0.3.0 - 2026-08-23

### Added

- Add a response cache for deterministic `x_retrieve` queries: exact status
  targets (24h TTL) and latest-by-handle feeds (8min TTL) are served from a
  local SQLite store (`cache.sqlite`, WAL mode, shared across gateway
  processes) with per-request `force_refresh` and `max_age_seconds` overrides.
  Semantic research is never cached; degraded and error payloads are never
  stored. Hits report `cache: {hit, age_seconds, policy, saved_cost_in_usd_ticks}`.
- Coalesce concurrent identical requests in-process: one upstream run shared by
  all waiters (shielded, so caller cancellation never discards the result or
  its cache write).
- Track fetch history with status IDs and content hashes only (no post text)
  and mark `new_since_last_fetch` on items unseen for their author, enabling
  cheap handle monitoring across queries.
- Report `usage_cost_ticks` per stage and per response, so agents and cache
  hits can see what a retrieval cost or saved.
- Add cache metrics `mcp_x_retrieve_cache_total{result=hit|miss|bypass|write|error}`.
- New settings: `GROK_PROXY_RETRIEVE_CACHE`, `GROK_PROXY_RETRIEVE_CACHE_PATH`,
  `GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES`,
  `GROK_PROXY_RETRIEVE_CACHE_EXACT_TTL_SECONDS`,
  `GROK_PROXY_RETRIEVE_CACHE_LATEST_TTL_SECONDS`.

### Changed

- Enforce `0600` on the cache file and its WAL side files; the zero-content-
  logging privacy promise now documents the opt-out cache store explicitly
  (`GROK_PROXY_RETRIEVE_CACHE=false` disables all disk persistence, including
  ID-only fetch history).

## Unreleased

### Security

- Hold one retrieve admission permit per request across all tier transitions
  (Fast -> Smart -> raw): `request_admission()` queues once per request and
  stages never re-queue; the admission timeout moved to
  `GROK_PROXY_RETRIEVE_QUEUE_TIMEOUT_SECONDS`.
- Persist interactive writes (native login, imports) through `save_local_state`
  under the inter-process flock with a monotonically increasing
  `state_version`, closing the last writer that could clobber a refresh
  transaction.
- Suppress redundant token-endpoint retries for 8 seconds after a transient
  refresh failure (timeout/429/5xx); credential rejections
  (`invalid_grant`/`invalid_client`) are never suppressed so self-healing login
  stays immediate.

### Changed

- Remove the `mcp_x_search.py` compatibility facade: tests and the tool
  dispatcher now use `mcp_tools`, `retrieve.pipeline`, and `retrieve.x_search`
  directly; the stdio entrypoint is `python mcp_server.py`.
- Rename the concurrency setting to `GROK_PROXY_RETRIEVE_CONCURRENCY`
  (the legacy `GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` still applies when the new
  name is unset).
- Drop `_structured_output` for models not known to support strict JSON schema
  output instead of risking an upstream 400, and surface both reasoning-effort
  and structured-output downgrades as one route warning for custom models.
- Make the xAI internal tool names used for auto-x_search artifact attribution
  configurable via `GROK_PROXY_X_SEARCH_INTERNAL_TOOL_NAMES`.

### Added

- Add a real two-subprocess refresh integration test against a one-shot
  rotating fake OAuth server (flock transaction, single upstream refresh, no
  rollback) plus negative-cache, versioned-persist, and single-permit tests.
- Add a `tests/conftest.py` with per-test token-state isolation and a
  `loopback_client` fixture; document all new environment variables in both
  READMEs and the routing RFC.

### Security (audit adoption)

- Reject DNS-rebinding and cross-site browser requests on loopback binds via a
  Host allowlist (421) and browser-Origin rejection (403), with
  `GROK_PROXY_ALLOWED_ORIGINS` as the explicit opt-in for local web origins.
- Never roll the on-disk OAuth state back to a stale snapshot: refresh runs as
  one inter-process locked transaction (`auth_state.json.lock` + flock) with a
  monotonically increasing `state_version`, adopts a newer credential another
  process already persisted, and skips failure writes when disk advanced.
- Make the refresh+persist transaction immune to caller cancellation via a
  gateway-owned shielded task; later callers join the in-flight refresh instead
  of replaying an already-rotated refresh token.
- Harden stdio framing: invalid UTF-8, oversized frames (>1 MiB), and non-object
  JSON return `-32700`/`-32600` errors and the server keeps serving instead of
  dying; notifications never receive responses and `jsonrpc` must be `"2.0"`.

### Fixed (audit adoption)

- Run the `seed_then_research` Smart stage with real Smart-lane model, turns,
  and deadline instead of an inherited zero-second timeout, and keep the
  corroborating evidence posts in the final payload (exact-only filtering now
  applies only to `exact_only`).
- Classify OAuth `invalid_grant`/`invalid_client` as `AUTH_REQUIRED` with
  `auth_login_command`, `stage="auth_refresh"`, and `retryable=false`; transient
  refresh failures (timeout/429/5xx) no longer mark `reauth_required`.
- Treat retrieve admission queueing as overload (`StageOverloaded`): overloaded
  requests never escalate to Smart or raw expansion.
- Auto x_search shim strips only x_keyword_search-attributed tool-call items
  and events, so client-owned custom tool calls survive; SSE parsing supports
  CRLF/CR separators, chunk boundaries, and a 4 MB per-event buffer cap.
- Canonicalize source/post/item URL keys (scheme/host case, default ports,
  fragments, tracking parameters, parameter order) before deduplication.
- Surface unsupported auto reasoning effort on custom models as an explicit
  route warning instead of silently omitting reasoning.
- Raise typed `AuthRequiredError` for non-object or malformed `auth_state.json`
  shapes instead of crashing with `AttributeError`.
- Close the shared xAI Responses client when a stdio session ends, and recreate
  the refresh single-flight lock per event loop for embedded callers.

### Added (audit adoption)

- Add sanitized xAI Responses fixtures under `tests/fixtures/xai/` and replay
  tests for Fast JSON, Smart citations, and non-JSON raw candidate text.
- Accept `reasoning.effort=xhigh` on grok-4.6 when explicitly requested.
- Put `AUTH_REQUIRED` plus an absolute `python /path/to/main.py --login`
  command on AuthRequiredError, `x_retrieve` tool descriptions, and error
  payloads so an Agent can relaunch browser login and retry.

### Changed

- Name failed retrieve stages from the actual lane (`fast_extract`,
  `smart_extract`, `custom_extract`, `validation`) instead of the leftover
  `stable_extract` label.
- When Fast Lane fails and remaining budget is below the Smart escalation
  floor, return a `degraded` payload and still attempt raw expansion instead
  of raising a hard MCP error.
- Keep daily Smart retrieval at `medium` and `verify_claim` at `high`; do not
  default to `xhigh`.

## 0.2.0 - 2026-08-23

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

- Move retrieval internals into the `retrieve/` package and split MCP tool
  registration (`mcp_tools.py`) from xAI x_search I/O (`retrieve/x_search.py`).
  `mcp_x_search.py` remains a compatibility facade. The
  `GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` environment variable is unchanged.
- Read MCP stdio frames through `asyncio.StreamReader` instead of blocking
  `sys.stdin` iteration.
- Record quality-gate pass/fail on the exact-target and seed-then-research
  pipelines, matching the general retrieve path.
- Bump the package and MCP `serverInfo` version to `0.2.0`.
- Document Smart Lane reasoning as `low` / `medium` / `high` only. `xhigh` is
  not in the capability table and is not sent on the Responses payload.
- Remove unused `HERMES_POLL_INTERVAL`. The daemon never polled Hermes
  `auth.json`; empty local state requires `python main.py --login`. Explicit
  Hermes import remains `scripts/import_xai_oauth.py`.
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

- Route production xAI Responses bodies and plan-builder tests through one
  constructor (`build_xai_responses_payload`) so Fast/Smart/raw payload fields
  cannot drift.
- Deduplicate merged `sources` by status ID/URL in
  `retrieve_payload.merge_stage_payload`.
- Populate `x_retrieve` error payloads with parsed `mode` and `request`
  metadata when arguments are valid, instead of always reporting
  `semantic_research`.
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
- Remove unused `reasoning_effort_for` and `should_run_raw`; share `_groups`
  from `retrieve.payload` with the oEmbed merger.

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
