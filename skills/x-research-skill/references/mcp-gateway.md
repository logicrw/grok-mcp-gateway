# Grok MCP Gateway Reference

## Primary Tool: `x_retrieve`

`x_retrieve` is the unified, multi-tier public tool exposed by `grok_mcp_gateway` (`http://127.0.0.1:9996/mcp`).

Reasoning effort defaults to `low` for fast general research (~29s) and `high`
for `verify_claim`. Callers can also explicitly pass `reasoning_effort` (`"low"`,
`"medium"`, `"high"`, `"xhigh"`) or configure `GROK_PROXY_SMART_REASONING_EFFORT`.

### Common Invocation Patterns

```json
// 1. Fact Verification (Smart Lane, automatic high reasoning)
{
  "name": "x_retrieve",
  "arguments": {
    "intent": "verify_claim",
    "query": "Did Anthropic announce Claude 3.7 with hybrid thinking?",
    "from_date": "2026-08-01"
  }
}

// 2. Exact Tweet / Status ID Lookup (Deterministic oEmbed - 0 Model Cost)
{
  "name": "x_retrieve",
  "arguments": {
    "query": "https://x.com/xai/status/2087630662631100586"
  }
}

// 3. Timeline / Latest by Handle (Fast Lane - 1-3s Non-Reasoning)
{
  "name": "x_retrieve",
  "arguments": {
    "handles": ["xai", "elonmusk"],
    "sort": "latest",
    "lookback_days": 14,
    "count": 5
  }
}

// 4. Topic Research with Best-Effort Noise Filtering
{
  "name": "x_retrieve",
  "arguments": {
    "intent": "research",
    "query": "Grok MCP Gateway local agent architecture",
    "best_effort_filters": {
      "min_likes": 10,
      "min_views": 1000
    },
    "count": 10
  }
}

// 5. Reaction Tracking
{
  "name": "x_retrieve",
  "arguments": {
    "intent": "reaction_tracking",
    "query": "OpenAI o3 launch community benchmark feedback"
  }
}

// 6. Freshness Control (v0.3.0 response cache)
{
  "name": "x_retrieve",
  "arguments": {
    "handles": ["xai"],
    "sort": "latest",
    "force_refresh": true
  }
}
```

Cache behavior: exact tweet targets and latest-by-handle feeds are served
from a local cache (24h / 8min TTL). Use `force_refresh: true` when the
answer must reflect the last minutes, or `max_age_seconds: 300` to accept
only entries no older than five minutes.

---

## Response Interpretation

The gateway outputs normalized `x_retrieve.v1` payload:

- `retrieval_status`:
  - `ok`: High-confidence result.
  - `degraded`: Best-effort fallback; check `warnings` field.
  - `empty` / `no_match`: No matching tweets found within constraints.
  - `error`: Failed; check `isError` and `warnings`.
- `sources`: Array of extracted primary citations `[{"url": "...", "title": "..."}]`. Pass these to web-fetch tools (`defuddle`, `firecrawl`, `read_url_content`) for primary source verification.
- `posts`: Array of structured tweet objects with `author`, `text`, `url`, `metrics`.
- `items`: Normalized per-post view; `items[].new_since_last_fetch` marks posts by that author not seen in earlier fetches (monitoring signal).
- `target_match`: For explicit status targets — `matched` / `missing` status IDs.
- `cache`: `{hit, age_seconds, policy, saved_cost_in_usd_ticks}` — on a hit, data is as fresh as `age_seconds`; re-query with `force_refresh: true` if that is too old.
- `usage_cost_ticks`: What this retrieval cost in upstream xAI ticks (0 on a cache hit; `cache.saved_cost_in_usd_ticks` shows what the hit saved).

---

## Monitoring Recipe (handle tracking)

1. Run the same `{"handles": ["@sama"], "sort": "latest"}` query on a schedule.
2. Filter `items[]` for `new_since_last_fetch == true`.
3. Summarize those posts as the delta digest; treat the rest as context.
4. The gateway dedupes seen posts across queries via ID-only history — no
   manual state tracking needed.

---

## Self-Healing Authentication

If `x_retrieve` returns an error with `"error": "AUTH_REQUIRED"`:
- The error payload contains `auth_login_command` (e.g. `python /path/to/main.py --login`).
- The Agent should immediately run this command via its shell tool, let the user approve in the browser, and then retry the `x_retrieve` call.
