# X Retrieval Model Evaluation Baseline

Date: 2026-06-25

Scope: eight de-identified topics, three xAI models, local X Search through this gateway. The score measures extraction structure and is not independent truth verification.

| Model | Average score | Successful | Valid JSON | Cases with status IDs | X URLs | Average latency | Noise cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `grok-4.3` | 39.0 | 8/8 | 8/8 | 7/8 | 33 | 14.08s | 0 |
| `grok-build-0.1` | 23.5 | 5/8 | 5/8 | 4/8 | 20 | 41.90s | 0 |
| `grok-composer-2.5-fast` | 29.38 | 8/8 | 2/8 | 8/8 | 36 | 19.67s | 8 |

The baseline supported keeping Composer as an optional expansion source rather than a trusted structured-output model. Raw conversation text, model responses, local prompts, and OAuth details are intentionally excluded from the repository.

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
