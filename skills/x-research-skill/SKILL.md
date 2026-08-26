---
name: x-research-skill
description: >
  Research X/Twitter discourse, community reactions, launch chatter, dev opinions,
  rumor verification, or source discovery via local Grok MCP Gateway.
  Trigger when the user asks about X/Twitter, 推特, 推文, 大V观点, 社区讨论, or real-time tech discourse.
---

# X Research

IRON LAW: X is signal, not source of truth. Verify primary sources before stating facts.

Use X for real-time perspectives, breaking claims, developer sentiment, and source discovery. Never treat raw X discourse as authoritative ground truth. Always verify cited documentation, GitHub PRs/repos, arXiv preprints, official announcements, or regulatory filings before presenting claims as facts.

## Default Gateway Path

Use the local Grok MCP Gateway:

- Server: `grok_mcp_gateway`
- Endpoint: `http://127.0.0.1:9996/mcp`
- Tool: `x_retrieve`

Always pass explicit `from_date` and `to_date` for time-sensitive requests ("today", "latest", "this week", "recently").

---

## Tool Parameter Best Practices

Select the appropriate `intent` and parameters for `x_retrieve` to maximize efficiency:

| Goal / Scenario | Recommended `intent` & Arguments |
| :--- | :--- |
| **Fact-checking / Rumor verification** | `intent: "verify_claim"`, query describing the claim (auto-selects `high` reasoning). Or pass explicit `reasoning_effort: "high"`. |
| **Topic discussions / General posts** | `intent: "posts"` for sub-6s Fast Lane, or `intent: "research"` with default `low` reasoning (~29s). |
| **Deep architectural / Analytical inquiry** | Pass explicit `reasoning_effort: "high"` (or `"medium"`) when deeper CoT is requested. |
| **Launch reactions / Sentiment tracking** | `intent: "reaction_tracking"`, query about the release or event. |
| **Latest tweets from accounts** | `handles: ["user1", "user2"]`, `sort: "latest"`, `lookback_days: 7`. |
| **Exact Tweet lookup & Seed research** | Pass full Tweet URL or 15-20 digit status ID directly in `query`. (Resolves in <100ms via 0-cost oEmbed.) |
| **Breaking news / must be current** | Add `force_refresh: true` to bypass the response cache and hit upstream. Otherwise repeated deterministic queries are served from cache (check `cache.age_seconds` in the response). |
| **Tracking an author over time** | Re-run the same `handles` + `sort: "latest"` query; items carry `new_since_last_fetch: true/false` marking posts unseen in previous fetches — build monitoring digests from those. |
| **Noise & Spam reduction** | Use `best_effort_filters: {"min_likes": 20, "min_views": 5000}` instead of manual query hackery. |

---

## Closed-Loop Research Workflow

1. **Decompose & Query**: Formulate targeted `x_retrieve` calls (core claim, key author handles, counter-arguments).
2. **Inspect Response Status**:
   - If `retrieval_status == "degraded"`, note the `warnings` and mention that this is a best-effort partial result.
3. **Primary-Source Handoff (Crucial Step)**:
   - Extract linked URLs from `sources` and `posts` (e.g. GitHub repos, blog posts, news, papers).
   - Use web-reading tools (`defuddle`, `firecrawl`, or `read_url_content`) to fetch the primary documentation.
4. **Synthesize & Cross-Verify**:
   - Synthesize by thematic consensus/dissent, not individual query dumps.
   - Explicitly separate: **Confirmed Facts** (verified via primary docs), **Community Consensus** (broadly agreed signal), and **Unverified Speculation**.

---

## Output Format

For research briefs:

```markdown
### Summary & Synthesis
1-2 sentence core finding.

### Verified Signals & Key Arguments
- **@user**: Core point or claim [Tweet link] -> *Primary evidence verified: [Doc/Repo link]*
- **@expert2**: Counter-argument or benchmark nuance [Tweet link]

### Boundary & Caveats
- **Confirmed Facts**: What has been proven by primary sources.
- **X-Only Discourse**: Unverified chatter or anecdotal claims.
- **Missing / Unknown**: Questions the discourse left unanswered.
```

---

## Failure & Self-Healing Handling

- **`AUTH_REQUIRED` Error**:
  - The Grok MCP Gateway automatically provides an `auth_login_command` (e.g. `python /path/to/main.py --login`).
  - **Self-Healing Action**: Execute this command in your local terminal via bash/run_command to open the browser for user approval. Once the command exits successfully, immediately retry the `x_retrieve` call!
- **Too Noisy**: Add `best_effort_filters` (`min_likes`, `min_views`), or add handles/exact dates.
- **Too Few Results**: Broaden query with `OR`, remove strict filters, expand date window.
- **Tool Disabled / Misconfigured**: Report MCP client configuration issue directly.
