# X Retrieval Model Evaluation Baseline

Date: 2026-06-25

Scope: eight de-identified topics, three xAI models, local X Search through this gateway. The score measures extraction structure and is not independent truth verification.

| Model | Average score | Successful | Valid JSON | Cases with status IDs | X URLs | Average latency | Noise cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `grok-4.3` | 39.0 | 8/8 | 8/8 | 7/8 | 33 | 14.08s | 0 |
| `grok-build-0.1` | 23.5 | 5/8 | 5/8 | 4/8 | 20 | 41.90s | 0 |
| `grok-composer-2.5-fast` | 29.38 | 8/8 | 2/8 | 8/8 | 36 | 19.67s | 8 |

The baseline supported keeping Composer as an optional expansion source rather than a trusted structured-output model. Raw conversation text, model responses, local prompts, and OAuth details are intentionally excluded from the repository.

Grok 4.5 comparisons should reuse the architecture's model upgrade protocol and record the same fields plus timeout boundary, reasoning tokens, and server-side X Search calls.
