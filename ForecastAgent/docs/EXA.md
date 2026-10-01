# Optional Exa discovery

Tavily remains the primary search provider. `search_exa` adds one independent
discovery attempt for an important official/scientific source or crosscheck.
The model stays Ultra. No framework, additional SDK or Codex dependency is needed.

## Credentials and frozen budgets

The Actions secret `EXA_API` maps to runtime environment variable `EXA_API_KEY`.
The collection worker exposes this tool only when the key is present and the
task has an enabled frozen budget. New tasks with a key receive at most **one**
Exa attempt. Existing ledgers receive no new allowance when the key is added.
Failures, interruptions and unknown outcomes consume the attempt; no retry,
automatic fallback or resume can restart it. Tavily's separate three-attempt
budget for `collection_v3` and the shared eight HTTP fetch attempts are unchanged.

The request fixes `type=auto`, `numResults=10`, omits `contents`, and does not
request summaries, synthesis or deep search. Results contain discovery metadata;
selected originals use the free readers already available. Supported categories
are general, news, publication and financial report. Bare include domains
**restrict** results; there is no invented preference mode or automatic relaxation.
Results are deduplicated against accepted Tavily/Exa leads and Metaculus is excluded.

## Historical limitations

Program-owned publication bounds use the exact UTC cutoff. A recent search also
sets a 60-day lower boundary. Undated or post-cutoff hits are quarantined; verified
historical model views expose URLs and dates only. Publication filtering cannot
establish a historical page version. Existing archive/body eligibility rules apply.
Raw responses and request IDs are saved separately from usable evidence.

## Cost and validation

Exa's [published pricing](https://exa.ai/pricing) lists $10 monthly starter credits
and Auto/Fast search at $7 per 1,000 requests for up to ten results, as checked on
2026-10-01. The provider's returned `costDollars` is an estimate, not an account
balance or invoice. Free credit eligibility and account billing limits are managed
in the Exa dashboard; the runtime does not claim to enforce the account-wide cap.

`Verify Exa discovery` makes one metadata search and at most one free page read.
It calls neither Ultra nor Tavily and submits no forecasts. Its durable artifact
is restored on later verification runs, preventing repeated test charges.
The verification checks real authentication, search results, deduplication,
free reading, snapshots and local artifact import. Unit tests cover temporal
quarantine, one-attempt failures and persistence across interrupted restarts.

References: [Search API](https://exa.ai/docs/reference/search),
[OpenAPI specification](https://exa.ai/docs/exa-spec.json).
