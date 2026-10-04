# Material gap acquisition experiment

This development-only workflow uses `collection_workflow: material-gap-v1`, an
explicit experiment ID, and `pipeline: collection`. Production routing is not
changed. The `gap2` cohort runs the existing IMO and Kaub acceptance cases.

## Flow

1. The existing model builds the acquisition plan and discovers sources.
2. The program executes exact discovered reading batches. Each URL is still
   validated against the catalog and charged to existing HTTP capacity. These
   program batches are recorded separately from model HTTP requests and do not
   acknowledge source delivery to a model that was not called.
3. A document need ledger identifies literal candidate coverage for each need.
4. Independent supplementation processes existing leads, then searches missing
   critical document families even when other readable bodies already exist.
5. Each discovery request is reserved before HTTP. One request per need/provider
   is permitted, with no transport retries. Discovered exact URLs join the bounded
   capture frontier. Bodies and failed attempts remain auditable.
6. Export the material ledger and termination reason for later analysis.

## Files and meaning

- `acquisition/material-needs.json`: initial acquisition handoff.
- `supplement/material-needs.json`: document candidate inventory after repair.
- `supplement/termination.json`: stop reason and unresolved critical need IDs.
- `supplement/state.json`: durable reservations and provider records, including
  an empty journal when no requests were made.
- `intelligence-bundle.json`: original and supplemental capture inventory.

`candidate_captured` means a readable body has observable family/topic signals.
It does not establish the event, exact model identity, temporal equivalence,
correct numerical value, or satisfaction of the resolution criteria. Candidate
URLs carry body hashes and remain subject to later analysis. Future-rollout
language alone does not count as a current-access document candidate.

Missing material is distinguished from `search_attempted`, `discovery_failed`,
`read_failed`, and `budget_exhausted`. Timestamp conversion and condition binding
are deferred to analysis, rather than endlessly reopening a captured document.
Sources absent from the ledger may still be saved in the raw inventory. Lexical
coverage is a routing proxy and requires manual review during acceptance.

## Shared limits

The experiment retains six Tavily BASIC requests, two Exa requests, 32 initial
HTTP attempts, 64 combined HTTP attempts, 12 browser renders, and all frozen
model/time/Extract limits. These are task lifetime totals across initial and
supplemental stages, not separate budgets per stage. Default profiles keep their
existing limits. Additional experimental searches accept `gap` and `crosscheck`
roles; the old production role restrictions remain unchanged.

Missing credentials disable that channel. HTTP 401/402/403/429 discovery failures
stop further gap discovery and preserve state; they do not trigger a second
provider to bypass an account failure. Failed and interrupted reservations count
as consumed. Resume uses the same parent hash, journals, implementation hashes,
capacity, and enabled channel set; it cannot silently restart allowances.

## Acceptance

Run offline routing, missing-family discovery, resume, accounting, and program
reading tests first. Compare online `gap2` artifacts with run `37134826108` using
the same question definitions and resource limits. This is an exploratory online
comparison, because source/provider responses vary between runs. Inspect actual
saved documents, requests, failures and remaining needs; job success alone is
not material completeness. No forecasts or analysis scores are produced.
