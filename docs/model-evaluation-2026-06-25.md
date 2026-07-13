# X Retrieval Model Evaluation Baseline

Date: 2026-06-25

Scope: eight de-identified topics, three xAI models, local X Search through this gateway. The score measures extraction structure and is not independent truth verification.

| Model | Average score | Successful | Valid JSON | Cases with status IDs | X URLs | Average latency | Noise cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `grok-4.3` | 39.0 | 8/8 | 8/8 | 7/8 | 33 | 14.08s | 0 |
| `grok-build-0.1` | 23.5 | 5/8 | 5/8 | 4/8 | 20 | 41.90s | 0 |
| `grok-composer-2.5-fast` | 29.38 | 8/8 | 2/8 | 8/8 | 36 | 19.67s | 8 |

The baseline supported keeping Composer as an optional expansion source rather than a trusted structured-output model. Raw conversation text, model responses, local prompts, and OAuth details are intentionally excluded from the repository.

## Retained on-demand case matrix

The original local exports are intentionally not retained, but their distinct
test shapes are. Use the smallest applicable subset when a model, prompt,
routing rule, quality gate, or fallback changes; do not run live evaluation for
every commit.

| Case shape | What it protects | Minimum evidence |
| --- | --- | --- |
| Latest by one active handle | Fast deterministic lane and recency behavior | usable status URL, original text, latency, no unnecessary raw stage |
| One known public status URL or ID | Exact matching and recovery boundary | requested/matched/missing IDs, no nearby-post substitution, oEmbed/fallback stage |
| Structured topic posts | JSON normalization and quality gate | valid items, URL coverage, warnings, raw decision |
| Research or source discovery | Broader semantic yield | usable sources, original text, latency, X Search calls |
| Reaction tracking or claim verification | Higher-reasoning route | supporting/reaction grouping, warnings, reasoning tokens |

Use public or synthetic targets and replace them when they disappear. Store
only aggregate measurements and failure classes; raw prompts, post bodies, and
model responses remain local.

## Grok 4.5 gateway validation

Date: 2026-07-13

These live smoke cases validate the new routing architecture, not independent truth or a replacement for the eight-topic baseline above.

| Case | Policy and route | Result | Latency | Yield |
| --- | --- | --- | ---: | ---: |
| Identical latest-by-handle query | Stable-only, Grok 4.5 low reasoning | `ok`, no warnings | 34.85s | 3 items / 3 sources |
| Fixed explicit status target | Active stable Grok 4.5, oEmbed available, Composer skipped | `ok`, exact match 1/1, no warnings | 14.59s | 1 target item |
| Explicit target repeat after deterministic filtering | Stable-only, Grok 4.5 low reasoning, Composer skipped | `degraded`, exact match 1/1 with one upstream caveat preserved | 30.86s | 1 target item, 0 nearby items |
| Research quality gate after parser hardening | Auto, Grok 4.5 medium reasoning plus gated Composer expansion | `ok`, no warnings | 28.02s | 10 items / 25 sources |

The identical latest query previously took 42.84s with the all-high reasoning baseline, so route-aware low reasoning reduced observed latency by about 18.6% in this run. Upstream latency remains variable; this is a smoke result, not a guaranteed performance claim. Future model comparisons should reuse the architecture's model upgrade protocol and record the baseline fields plus timeout boundary, reasoning tokens, and server-side X Search calls.

A later forced `raw_expanded` smoke on 2026-07-13 completed both `grok-4.5`
and `grok-composer-2.5-fast` stages successfully even though deep health marked
Composer `not_listed`. The request returned sources but no normalized items, so
it proves runtime access, not retrieval quality. This is why model listing and
quality must be evaluated separately.
