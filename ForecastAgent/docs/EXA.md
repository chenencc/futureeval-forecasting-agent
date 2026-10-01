# Exa discovery

Tavily remains the primary search provider. `search_exa` adds one independent
discovery attempt for an important official/scientific source or crosscheck.
This attempt is required by default for new collection tasks; existing ledgers
retain their frozen policy. See the required discovery policy below.
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

## Required discovery policy

New collection tasks freeze `search_policy.exa = required` by default. Ultra must
make one task-grounded Exa metadata discovery attempt before normal completion,
preferably after the first Tavily attempt for independent source discovery. The
runtime forces Exa early if necessary and hides normal finish while the obligation
is pending. It remains at most one provider attempt per task, including failures.
Invalid parameters rejected before provider execution do not satisfy it.

The output reports `exa_requirement.attempt_requirement_met` separately from
successful discovery and acquisition adequacy. Provider failure counts as an
attempt, not useful evidence. Acceptance fails when the required attempt is missing
or its HTTP outcome is unknown; capture integrity remains independently inspectable.
Missing credentials, zero frozen allowance and an
interrupted unknown reservation are explicit unmet gaps. Dispatch/time/lifetime
limits or repeated invalid arguments allow bounded closure with gaps; the
obligation cannot create a model loop or renew any quota.

Existing ledgers retain their prior optional/authorized supplement behavior. An
explicit `exa_search_policy: optional` can be frozen at creation for ablation
experiments; resumed tasks cannot change policy by changing their input. Adding a
key later never implicitly grants an allowance to a zero-budget ledger.
