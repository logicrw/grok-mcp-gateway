# X Retrieval Architecture (v0.2.1)

`x_retrieve` is the only public MCP retrieval tool. The gateway keeps model-dependent behavior behind one deterministic controller so model upgrades do not change the client contract.

## Ownership and non-goals

The gateway owns one bounded retrieval request: deterministic route selection,
model calls, normalization, target validation, recovery, and telemetry. The
calling Agent owns research planning, query decomposition, synthesis, and the
decision to call `x_retrieve` again. This separation keeps the MCP contract
small while allowing stronger Agents to use it more effectively.

The project intentionally remains independent of the official X API. It will
not embed an official X API provider, `xurl`, posting/account actions, or X API
credentials. A client may configure unrelated official tools separately, but
they do not become a gateway backend.

Persistent databases or caches, learned routing, in-gateway multi-query
planning, vector stores, and multi-agent research frameworks are also non-goals
until repeated production traces show a specific failure that the current
controller cannot solve more simply. See [Agent Development Guardrails](agent-development-guardrails.md) for mandatory engineering invariants.

## Execution Lanes

The v0.2.1 gateway organizes execution into a 4-stage bounded execution pipeline:

1. **Deterministic Lane (oEmbed-first)**:
   - For explicit X status URLs or 15-20 digit IDs, public oEmbed is executed first concurrently.
   - If all target posts are recovered with full text, model calls drop to 0.
   - Missing targets trigger a batched fallback prompt via Fast Lane (or Smart Lane if budget allows).
   - Exact-only requests never invoke Composer raw expansion.

2. **Fast Lane (`grok-4.20-0309-non-reasoning`)**:
   - Conservative routing for simple `latest_by_handle` requests and simple `structured_posts` without heavy quality requirements or reasoning markers.
   - Constrained by native Structured Outputs (`text.format.json_schema`), `max_turns=1~2`, and short stage timeout (15s).
   - Forbids reasoning effort parameters (non-reasoning model).

3. **Smart Lane (`grok-4.6` default, `grok-4.5` fallback compatibility)**:
   - Default stable lane for complex semantic research, source discovery, reaction tracking, and claim verification.
   - Supports validated `reasoning.effort` (`low`, `medium`, `high`, `xhigh`) on grok-4.6. Daily Smart objectives default to `medium`; `verify_claim` defaults to `high`. Explicit `xhigh` is forwarded. grok-4.5 remains `low`/`medium`/`high`.
   - Receives automatic escalations from Fast Lane when Fast Lane results fail quality gates and remaining budget >= 35s.

4. **Raw Expansion Lane (`grok-composer-2.5-fast`)**:
   - Candidate expansion source strictly guarded by deterministic quality filters.
   - Only invoked if Smart stage fails quality requirements and policy permits raw expansion.

| Request shape / Intent | Primary Lane | Deterministic recovery | Quality gate & escalation |
| --- | --- | --- | --- |
| Explicit status URL/ID | Deterministic Lane (oEmbed first) | Concurrent public oEmbed | Missing IDs -> Fast fallback -> Smart fallback (no Composer) |
| Latest by handle | Fast Lane (`grok-4.20-0309-non-reasoning`) | Schema normalization | Quality fail -> Smart escalation -> Composer |
| Simple structured posts | Fast Lane (`grok-4.20-0309-non-reasoning`) | Structured Outputs | Quality fail -> Smart escalation -> Composer |
| Research / Source discovery | Smart Lane (`grok-4.6`, low/medium reasoning) | Structured Outputs | Quality fail -> Composer |
| Reaction tracking | Smart Lane (`grok-4.6`, low/medium reasoning) | Structured Outputs | Quality fail -> Composer |
| Claim verification | Smart Lane (`grok-4.6`, medium/high reasoning) | Structured Outputs | Quality fail -> Composer |


## Concurrency and Single-Flight OAuth

- **OAuth Refresh Coalescing**:
  - Concurrent requests that encounter HTTP 401 supply `stale_access_token` to `get_access_token()`.
  - While waiting for the async refresh lock, subsequent coroutines detect token rotation and immediately reuse the fresh access token.
  - 5 concurrent 401s generate exactly 1 upstream OAuth refresh request.

## Bounded Orchestration & Configuration

| Setting | Default | Description |
| --- | ---: | --- |
| `GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS` | 180.0 | Total request deadline |
| `GROK_PROXY_FAST_STAGE_TIMEOUT_SECONDS` | 10.0 | Fast lane stage ceiling |
| `GROK_PROXY_SMART_STAGE_TIMEOUT_SECONDS` | 120.0 | Smart lane stage ceiling |
| `GROK_PROXY_RAW_STAGE_TIMEOUT_SECONDS` | 50.0 | Raw expansion stage ceiling |
| `GROK_PROXY_SMART_ESCALATION_MIN_REMAINING_SECONDS` | 35.0 | Minimum remaining budget required for Smart escalation |
| `GROK_PROXY_FALLBACK_RESERVE_SECONDS` | 8.0 | Safety reserve time for clean finalization |
| `GROK_PROXY_FAST_MAX_TURNS` | 2 | Tool turn limit for Fast Lane |
| `GROK_PROXY_SMART_MAX_TURNS` | 5 | Tool turn limit for Smart Lane |
| `GROK_PROXY_ENABLE_AUTO_TIERING` | true | Enable Fast -> Smart adaptive routing |
| `GROK_PROXY_STORE_RESPONSES` | false | Explicit `store: false` for independent retrieval requests |

## Fixture refresh

Offline contract tests live in `tests/fixtures/xai/`. They are sanitized
Responses-shaped JSON (no Authorization, JWT, email, or live tokens). Refresh
them only from a real `x_retrieve` by saving `response.json()` after
`error_sanitizer.sanitize_text`, then stripping remaining identifiers.

| File | Lane | What the replay asserts |
| --- | --- | --- |
| `fast_latest.json` | Fast | `assemble_payload` + `finalize_payload` → `ok` |
| `smart_verify.json` | Smart | citations survive extraction; claim URL is kept |
| `raw_non_json.json` | Raw | `parse_raw_posts_from_text` recovers status URLs |

Do not hit the live xAI API from CI.

## Telemetry & Metrics

`/metrics` provides low-cardinality Prometheus telemetry:
- `mcp_x_retrieve_final_status_total`: `ok`, `empty`, `no_match`, `degraded`, `error`.
- `mcp_x_retrieve_stage_total` & `_duration_seconds_total`: bounded by `stage`, `model_role` (`fast`, `smart`, `raw`, `public_oembed`, `stable`), `status`, `reasoning_effort`.
- `mcp_x_retrieve_timeout_total`: `stage`, `total`, `upstream`.
- `mcp_x_retrieve_reasoning_tokens_total` & `mcp_x_retrieve_x_search_calls_total`.
- `mcp_x_retrieve_cost_usd_ticks_total`: parsed from upstream `usage.cost_in_usd_ticks`.
