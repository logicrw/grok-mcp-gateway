# X Retrieval Architecture

`x_retrieve` is the only public MCP retrieval tool. The gateway keeps model-dependent behavior behind one deterministic controller so model upgrades do not change the client contract.

## Request paths

| Request shape | Stable stage | Deterministic recovery | Optional expansion |
| --- | --- | --- | --- |
| Explicit status URL or ID | Active stable model (Grok 4.5 by default), low reasoning when supported | Concurrent public oEmbed, then one batched fallback using the same active stable model | Composer is never used |
| Latest by handle | Active stable model, low reasoning when supported | Quality and target validation | Composer only when stable output is empty |
| Structured posts | Active stable model, low reasoning when supported | Schema normalization and quality gate | Composer only when the quality gate fails |
| Research, source, reactions | Active stable model, medium reasoning when supported | Schema normalization and quality gate | Composer only when the quality gate fails |
| Claim verification | Active stable model, high reasoning when supported | Schema normalization and target validation | Composer only when allowed by policy |

Reasoning effort is attached only to the documented `grok-4.5` model. Custom models and `grok-composer-2.5-fast` do not inherit that parameter. This follows the [Grok 4.5](https://docs.x.ai/developers/grok-4-5) and [reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning) contracts.

`model_policy=stable_only` disables Composer/raw expansion; it does not disable public oEmbed or the active stable model's exact-target fallback. Exact-target results are deterministically filtered to the requested status IDs before return.

## Bounded orchestration

One `x_retrieve` request has a 120-second total deadline and a 60-second generative-stage ceiling. The outer stage deadline wraps semaphore wait, OAuth refresh, retry, and response parsing. Separately, each individual xAI HTTP request currently has a 60-second client timeout. These are distinct boundaries: the outer stage budget can expire first because it includes work outside the HTTP call, and later stages receive only the remaining total budget.

| Setting | Default | Bound |
| --- | ---: | ---: |
| `GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS` | 120 | 10-300 |
| `GROK_PROXY_RETRIEVE_STAGE_TIMEOUT_SECONDS` | 60 | 5-120 and no greater than total |
| `GROK_PROXY_RETRIEVE_MAX_TARGETS` | 5 | 1-10 |
| `GROK_PROXY_RETRIEVE_OEMBED_CONCURRENCY` | 3 | 1-10 |

Explicit targets are capped before orchestration and the cap is returned in `warnings`. Missing targets are sent in one fallback prompt, avoiding one model round trip per status ID.

## Trust boundaries

- Stable and raw model output always passes schema normalization and deterministic target matching.
- Composer remains a quality-gated candidate expansion source. Non-JSON output is accepted only when a deterministic parser finds a real X status URL or labeled 15-20 digit ID.
- Public oEmbed is used only for exact IDs and never broadens the requested target set.
- `timeline_verified=false` remains explicit because xAI X Search is generated retrieval, not an official X API timeline.
- Context compaction is intentionally absent. Each stage is a short, independent Responses request rather than a growing agent conversation.

## Operations

`/health` reports active stable and raw model IDs. `/health?deep=1` adds `listed`, `not_listed`, or `unknown` from `/v1/models`; absence from that list is not reported as an entitlement failure.

`/metrics` records final status, stage/model-role/status/reasoning effort, timeout boundary, bounded error kind, reasoning tokens, and server-side X Search calls. Model roles are limited to `stable`, `raw`, `public_oembed`, and `unknown`; exact model IDs remain visible through health and response diagnostics. No response body, prompt, OAuth value, request-provided model name, or user content is used as a metric label.

## Model upgrade protocol

For each new stable or raw model:

1. Run the same de-identified latest, exact-target, research, source, and reaction cases.
2. Compare target match, usable status URLs, original text, JSON reliability, latency, timeout rate, reasoning tokens, and X Search calls.
3. Keep deterministic routing, quality gates, and oEmbed unchanged during the comparison.
4. Change one model role at a time and retain environment overrides for rollback.
5. Remove Composer only when stable-only retrieval consistently matches its useful yield without increasing empty or no-match results.
