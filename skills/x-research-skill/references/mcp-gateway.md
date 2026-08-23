# Grok MCP Gateway Reference

## Primary Tool: `x_retrieve`

`x_retrieve` is the unified, multi-tier public tool exposed by `grok_mcp_gateway` (`http://127.0.0.1:9996/mcp`).

### Common Invocation Patterns

```json
// 1. Fact Verification (Smart Lane + High/XHigh Reasoning)
{
  "name": "x_retrieve",
  "arguments": {
    "intent": "verify_claim",
    "query": "Did Anthropic announce Claude 3.7 with hybrid thinking?",
    "from_date": "2026-08-01",
    "_reasoning_effort": "high"
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
```

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

---

## Self-Healing Authentication

If `x_retrieve` returns an error with `"error": "AUTH_REQUIRED"`:
- The error payload contains `auth_login_command` (e.g. `python /path/to/main.py --login`).
- The Agent should immediately run this command via its shell tool, let the user approve in the browser, and then retry the `x_retrieve` call.

